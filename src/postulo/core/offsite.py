"""Backups that leave the machine: encrypted, sent by the scheduler, kept in check (#325).

`core.backups` writes a verified archive into ``POSTULO_BACKUP_DIR``. A copy on the same disk
is no help the day the disk goes, so an administrator may choose a **backup destination**, a
plugin of its own kind (`BackupDestinationPlugin`), under *Server settings → Backups*. This is
everything between an archive that has just verified and an archive that has arrived there.

**Nothing leaves in clear.** The archive holds every account's data, and only the stored
secrets in it are encrypted. So the core encrypts it to the operator's **age recipient**
before any destination sees it, into a file of its own that is deleted when the send is over,
and refuses a destination with no recipient at all. Only the public key is on the instance:
the identity that opens an archive stays with the operator, so neither a copy of the
destination nor a copy of this database opens one. Restoring starts by decrypting by hand.
The recipient is an X25519 one and nothing else: a plugin recipient names a program for age
to run, which is not something a form on a web page should be able to choose (and was a
remote code execution in the library, GHSA-4fg7-vxc8-qx5w).

**Sent like a document copy, not like an errand** (`documents.archiving`, #247). After an
archive verifies -- from the schedule, *Back up now*, or ``manage.py backup`` into the
directory -- one :class:`~postulo.core.models.OffsiteBackup` row is queued. The scheduler
sends what is due, retries a failure with a growing wait, and after a few attempts leaves it
for an administrator to send again. A row is claimed with a lease before it is sent, so two
schedulers never send one archive twice, and the send runs inside a deadline that ends before
the lease does: an upload can take an hour, and must neither outlive its claim nor hold the
pass that carries everybody's reminders, so it runs after that pass's work, in the worker
when there is one.

**Retention there only follows a confirmed send**, keeps the newest *N* (its own number,
the local one by default, or none deleted), and deletes only a reference the destination's
own ``list()`` returned **and** that Postulo itself sent. A destination that lists somebody
else's files beside ours never has them deleted for it.

**A failed send never costs the local archive.** Local retention skips an archive that is
still waiting for the destination, so the copy that has not left yet is the one copy there
is.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import tempfile
from pathlib import Path

from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.plugins import base, http
from postulo.plugins.secrets import SecretsUnreadable

from . import site
from .memo import forget_current
from .offsite_queue import chosen_name

logger = logging.getLogger(__name__)

#: After this many failed attempts a send waits for an administrator rather than the clock.
MAX_ATTEMPTS = 6
#: The wait doubles from here: ten, twenty, forty minutes, then hours.
FIRST_RETRY = dt.timedelta(minutes=10)
#: How long a claimed send is held. An archive with media can be gigabytes on a home uplink.
SENDING_LEASE = dt.timedelta(hours=4)
#: What the send leaves of its lease unspent, for encrypting and for recording the outcome.
LEASE_MARGIN = dt.timedelta(minutes=15)
#: How many archives one pass sends at most. Uploads are long; the rest wait for the next.
BATCH = 2
#: How long *Test* may take: one request or two, from a page somebody is waiting on.
TEST_SECONDS = 30

#: An X25519 age recipient: ``age1`` and fifty-eight characters of lower-case bech32.
#: Upper case is valid bech32 and age never writes it; a plugin recipient (``age1name1…``)
#: is refused on purpose, see the module's docstring.
RECIPIENT = re.compile(r"age1[02-9ac-hj-np-z]{58}")


class RecipientRefused(ValueError):
    """Not an age recipient this instance will encrypt to."""


def parse_recipient(text: str):
    """The recipient ``text`` names, or :class:`RecipientRefused` saying why not."""
    import pyrage
    from pyrage import x25519

    text = (text or "").strip()
    if not RECIPIENT.fullmatch(text):
        raise RecipientRefused(
            _("That is not an age recipient. It starts age1 and is 62 characters long.")
        )
    try:
        return x25519.Recipient.from_str(text)
    except pyrage.RecipientError as error:
        raise RecipientRefused(
            _("That is not an age recipient: its checksum does not match.")
        ) from error


def encrypt(source: Path, target: Path, recipient: str) -> None:
    """Encrypt the file at ``source`` to ``recipient`` into ``target``, streaming."""
    import pyrage

    pyrage.encrypt_file(str(source), str(target), [parse_recipient(recipient)])


# ------------------------------------------------------------------- the destination


def available() -> list:
    """Every backup destination installed here."""
    from postulo.plugins.registry import plugins

    return plugins("backup-destination")


def selected():
    """The destination an administrator chose, if it is installed; else ``None``.

    Never "the one that happens to be installed": installing a package must not start
    sending everybody's data somewhere, which is the transport's rule too.
    """
    name = chosen_name()
    if not name:
        return None
    return next((item for item in available() if item.name == name), None)


def configuration(row=None) -> dict:
    """The chosen destination's settings and secrets together, as a plugin is handed them."""
    row = row or site.current()
    return {**(row.backup_destination_config or {}), **row.backup_destination_secrets}


def remote_keep(row=None) -> int:
    """How many archives retention keeps there; ``0`` deletes none."""
    row = row or site.current()
    if row.backup_remote_keep is None:
        return row.backup_keep
    return row.backup_remote_keep


# ----------------------------------------------------------------------- queueing


def queue(path: Path | str):
    """Queue a send of the archive just written at ``path``; the row, or ``None``.

    Only an archive Postulo wrote into its own directory, under a name of its own shape: one
    written elsewhere by ``manage.py backup /somewhere`` is the operator's to carry, and one
    somebody uploaded to restore from was never ours to send. Nothing is queued while no
    destination is chosen. The check lives with the archives, in `backups.queue_send`.
    """
    from . import backups

    return backups.queue_send(Path(path), quietly=False)


def send_again(name: str):
    """An administrator's *Send*: queue it now, with its attempts given back.

    Never sends inside the request. An upload is as long as an upload is, and the scheduler
    is what holds a lease on it; this only makes the row due.
    """
    from . import backups
    from .models import OffsiteBackup, OffsiteState

    destination = chosen_name()
    if not destination:
        return None
    backups.archive_path(name)  # NotAnArchive for anything that is not one of ours
    row, _created = OffsiteBackup.objects.get_or_create(
        archive=name, destination=destination, defaults={"state": OffsiteState.PENDING}
    )
    if row.state in (OffsiteState.SENT, OffsiteState.REMOVED):
        return row
    OffsiteBackup.objects.filter(pk=row.pk).update(
        attempts=0, next_attempt_at=timezone.now(), last_error=""
    )
    row.refresh_from_db()
    return row


def states(names) -> dict:
    """Each named archive's row for the chosen destination, for the *Backups* list."""
    from .models import OffsiteBackup

    destination = chosen_name()
    if not destination:
        return {}
    return {
        row.archive: row
        for row in OffsiteBackup.objects.filter(destination=destination, archive__in=list(names))
    }


# ------------------------------------------------------------------------ sending


def pending(now=None):
    """What the scheduler should send now, for the destination that is chosen."""
    from .models import OffsiteBackup, OffsiteState

    now = now or timezone.now()
    return (
        OffsiteBackup.objects.filter(destination=chosen_name())
        .filter(state__in=(OffsiteState.PENDING, OffsiteState.FAILED))
        .filter(attempts__lt=MAX_ATTEMPTS)
        .filter(next_attempt_at__lte=now)
        .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lt=now))
        .order_by("next_attempt_at", "pk")
    )


def claim(row, now=None) -> bool:
    """Take this row for sending, or learn that somebody else has (as `archiving.claim`).

    A conditional update: only where nobody holds a live lease, and only if the row is as it
    was read, so a pass holding a row another pass has just tried cannot send it again.
    """
    from .models import OffsiteBackup, OffsiteState

    now = now or timezone.now()
    taken = (
        OffsiteBackup.objects.filter(pk=row.pk, state=row.state, attempts=row.attempts)
        .exclude(state__in=(OffsiteState.SENT, OffsiteState.REMOVED))
        .filter(Q(claimed_until__isnull=True) | Q(claimed_until__lt=now))
        .update(claimed_until=now + SENDING_LEASE)
    )
    if taken:
        row.claimed_until = now + SENDING_LEASE
    return bool(taken)


def record(ok: bool, message: str = "") -> None:
    """Write down how a send went, for the page and the metrics, in one statement."""
    from .models import SiteSettings

    try:
        SiteSettings.get()
        if ok:
            changes = {
                "backup_offsite_last_ok_at": timezone.now(),
                "backup_offsite_last_error": "",
            }
        else:
            changes = {
                "backup_offsite_failures": F("backup_offsite_failures") + 1,
                "backup_offsite_last_error": message[:500],
            }
        SiteSettings.objects.filter(pk=1).update(**changes)
        forget_current()
    except Exception:
        logger.exception("Could not record how the off-site backup went.")


def _manifest_for(path: Path) -> base.BackupManifest:
    from . import backups

    described = backups._describe(path)
    return base.BackupManifest(
        archive=path.name,
        created_at=described.made_at,
        version=described.version,
        engine=described.engine,
        instance=site.instance_name(),
        counts=tuple(sorted(described.counts.items())),
    )


def _verified(path: Path) -> str:
    """Why this archive may not leave, or ``""``: it must still be the archive that verified."""
    from . import backups

    checked, problem = backups._remembered(path)
    if checked == "ok":
        return ""
    if checked == "failed":
        return problem or str(_("The archive does not verify."))
    ok, problem = backups.verify(path)
    return "" if ok else problem


def send_one(row) -> bool:
    """One attempt at one claimed row. Records the outcome on the row; never raises."""
    from . import backups
    from .models import OffsiteState

    now = timezone.now()
    row.attempts += 1
    row.last_attempt_at = now

    def fail(message: str, *, final: bool = False) -> bool:
        row.state = OffsiteState.FAILED
        row.last_error = message[:500]
        row.next_attempt_at = now + FIRST_RETRY * (2 ** (row.attempts - 1))
        if final:
            # Nothing a retry could change: wait for a person, who can send it again.
            row.attempts = max(row.attempts, MAX_ATTEMPTS)
        row.claimed_until = None
        row.save()
        record(False, message)
        logger.warning(
            "Backup %s was not sent to %s (attempt %s): %s",
            row.archive,
            row.destination,
            row.attempts,
            message,
        )
        return False

    plugin = selected()
    if plugin is None or plugin.name != row.destination:
        return fail(
            str(
                _("The %(plugin)s destination is not installed or not chosen.")
                % {"plugin": row.destination}
            )
        )
    settings_row = site.current()
    if not settings_row.backup_recipient:
        # The form refuses this; a row edited by hand is refused here as well.
        return fail(str(_("No age recipient is set, and nothing leaves this machine unencrypted.")))
    try:
        path = backups.archive_path(row.archive)
    except backups.NotAnArchive:
        return fail(str(_("The archive is no longer on this server.")), final=True)
    problem = _verified(path)
    if problem:
        return fail(
            str(_("The archive does not verify: %(problem)s") % {"problem": problem}), final=True
        )

    lease_left = (row.claimed_until - timezone.now()) - LEASE_MARGIN if row.claimed_until else None
    handle, scratch_name = tempfile.mkstemp(
        dir=backups.directory(), prefix=".offsite-", suffix=".age"
    )
    os.close(handle)
    scratch = Path(scratch_name)
    try:
        try:
            encrypt(path, scratch, settings_row.backup_recipient)
        except Exception as error:
            logger.exception("Could not encrypt backup %s", row.archive)
            return fail(str(_("It could not be encrypted: %(error)s") % {"error": error}))
        archive = base.BackupArchive(
            name=f"{row.archive}.age", size=scratch.stat().st_size, _path=scratch
        )
        try:
            config = configuration(settings_row)
        except SecretsUnreadable as error:
            return fail(str(error))
        seconds = lease_left.total_seconds() if lease_left else SENDING_LEASE.total_seconds()
        if seconds <= 0:
            return fail(str(_("The time for this send ran out before it began.")))
        try:
            with http.until(http.deadline_in(seconds)):
                ref = plugin.send(archive, _manifest_for(path), config)
        except Exception as error:
            logger.exception("Backup destination %r failed for %s", plugin.name, row.archive)
            return fail(str(error) or type(error).__name__)
    finally:
        scratch.unlink(missing_ok=True)

    if not isinstance(ref, base.BackupRef) or not str(ref.id or ""):
        return fail(str(_("The destination did not say where it put the archive.")))
    row.state = OffsiteState.SENT
    row.sent_at = now
    row.remote_id = str(ref.id)[:500]
    row.remote_label = str(ref.label or ref.id)[:500]
    row.last_error = ""
    row.next_attempt_at = None
    row.claimed_until = None
    row.save()
    record(True)
    logger.info(
        "Backup %s sent to %s as %s (%s bytes, encrypted).",
        row.archive,
        plugin.name,
        row.remote_label,
        archive.size,
    )
    try:
        apply_remote_retention(plugin, config)
    except Exception:
        # The send is done and recorded; a retention that failed is tried after the next.
        logger.exception("Remote retention at %s failed", plugin.name)
    return True


def send_pending(*, limit: int = BATCH) -> tuple[int, int]:
    """Send what is due. Returns (sent, failed)."""
    sent = failed = 0
    if selected() is None:
        return 0, 0
    for row in list(pending()[:limit]):
        if not claim(row):
            continue
        if send_one(row):
            sent += 1
        else:
            failed += 1
    return sent, failed


def start_sending(*, queue=None) -> bool:
    """Called after each scheduler pass: send what is due, in the worker when there is one.

    After the pass's own work and outside its lease, as the scheduled backup is, so an upload
    never holds the reminders of the pass it follows. With a worker it is queued and the pass
    goes on; without one it runs here, one archive at most, and the next pass waits for it.
    """
    if selected() is None or not pending().exists():
        return False
    if queue is not None:
        try:
            queue()
            return True
        except Exception:
            logger.exception("Could not queue the off-site backup; sending it here instead.")
    send_pending(limit=1)
    return True


# ---------------------------------------------------------------------- retention


def apply_remote_retention(plugin, config: dict) -> list[str]:
    """Keep the newest few there; delete only what `list` returned and Postulo sent.

    Called only after a send is confirmed. The order is Postulo's own record of when each
    was sent, never the destination's word, and a reference nobody here sent is never
    deleted: a destination is free to hold the operator's other files beside ours.
    """
    from .models import OffsiteBackup, OffsiteState

    keep = remote_keep()
    if keep <= 0:
        return []
    listed = [ref for ref in plugin.list(config) if isinstance(ref, base.BackupRef)]
    ours = {
        row.remote_id: row
        for row in OffsiteBackup.objects.filter(
            destination=plugin.name, state=OffsiteState.SENT
        ).exclude(remote_id="")
    }
    candidates = [ref for ref in listed if str(ref.id) in ours]

    def newest_first(ref) -> tuple:
        row = ours[str(ref.id)]
        return (row.sent_at.timestamp() if row.sent_at else 0.0, row.pk)

    candidates.sort(key=newest_first, reverse=True)
    gone = []
    for ref in candidates[keep:]:
        row = ours[str(ref.id)]
        try:
            plugin.delete(ref, config)
        except Exception as error:
            logger.warning(
                "Retention could not delete %s (%s) from %s: %s",
                row.archive,
                ref.label or ref.id,
                plugin.name,
                error,
            )
            continue
        OffsiteBackup.objects.filter(pk=row.pk).update(
            state=OffsiteState.REMOVED, removed_at=timezone.now()
        )
        logger.warning(
            "Retention deleted backup %s (%s) from the %s destination, keeping the newest %s.",
            row.archive,
            ref.label or ref.id,
            plugin.name,
            keep,
        )
        gone.append(row.archive)
    return gone


# ------------------------------------------------------------------------- testing


def test(plugin, config: dict) -> base.TestResult:
    """Ask the destination to prove its settings, held to a short deadline. Never raises."""
    try:
        with http.until(http.deadline_in(TEST_SECONDS)):
            result = plugin.test(config)
    except Exception as error:
        logger.warning("Testing backup destination %s failed: %s", plugin.name, error)
        return base.TestResult(False, str(error) or type(error).__name__)
    if not isinstance(result, base.TestResult):
        return base.TestResult(False, str(_("The destination gave no answer.")))
    return result
