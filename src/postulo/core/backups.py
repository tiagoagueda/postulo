"""Instance backups as the interface sees them: which archives exist, and the schedule (#242).

:mod:`postulo.core.backup` writes, verifies and restores an archive. This is everything
around it that a page needs: which files in ``POSTULO_BACKUP_DIR`` are archives, what each
says about itself, whether it has been opened lately, how much room is left, when the next
scheduled one is due, and which old ones the retention keeps.

**An archive is addressed by name, and only a name of the shape Postulo writes.** The page
holds everybody's data in a file, and a name from a request is the way a request reaches
a file. So a name is accepted only if it fullmatches the pattern below, names a plain file
directly inside the directory, and is not a symlink; nothing here ever joins a path that
came from outside onto the directory without that check first.

**Verifying is on demand, and remembered by what the file is.** Hashing a database member
reads the whole archive. The answer is kept in the cache against the file's name, size and
modification time, so a listing never re-hashes and a file that changed is not trusted for
having been checked before.

**One at a time**, whoever asks: a cache key held for the length of a backup, so a second
press, and the scheduler on the same minute, find out that one is running.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from . import site
from .backup import BackupError, read_manifest, verify_backup, write_backup
from .memo import forget_current

logger = logging.getLogger(__name__)

#: What Postulo names a file: `manage.py backup` and the scheduler write ``backup``, an
#: archive somebody uploaded to restore from is ``upload``. Nothing else is listed.
NAME = re.compile(r"postulo-(backup|upload)-\d{8}-\d{6}(-\d+)?\.tar\.gz")

LOCK_KEY = "postulo:backup:running"
#: Longer than any backup should take; a crashed one frees the key by expiring.
LOCK_SECONDS = 6 * 3600

#: How long past a slot the scheduler may be before the page calls the run missed.
MISSED_AFTER = dt.timedelta(hours=2)

OFF, DAILY, WEEKLY = "off", "daily", "weekly"


class NotAnArchive(Exception):
    """A name that is not one of Postulo's archives, or not one that can be served."""


@dataclass
class Archive:
    name: str
    path: Path
    size: int
    made_at: dt.datetime
    #: From the manifest; ``None`` when it could not be read.
    version: str = ""
    engine: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    unreadable: bool = False
    #: ``"ok"``, ``"failed"`` or ``""`` for not checked since the file last changed.
    checked: str = ""
    problem: str = ""
    uploaded: bool = False


def directory() -> Path:
    return Path(settings.POSTULO_BACKUP_DIR)


def archive_path(name: str) -> Path:
    """The file a name stands for, or :class:`NotAnArchive`. The one way a name becomes a path."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise NotAnArchive(name)
    root = directory()
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise NotAnArchive(name)
    # Belt and braces for a directory that is itself reached through a link: the file's
    # real parent has to be the directory's real self.
    if path.resolve().parent != root.resolve():
        raise NotAnArchive(name)
    return path


def _check_key(path: Path) -> str:
    stat = path.stat()
    return f"postulo:backup:checked:{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def remember_check(path: Path, ok: bool, problem: str = "") -> None:
    cache.set(_check_key(path), ("ok" if ok else "failed", problem), 60 * 60 * 24 * 30)


def verify(path: Path) -> tuple[bool, str]:
    """Open the archive and compare the database to its checksum; remember the answer."""
    try:
        verify_backup(path)
    except BackupError as error:
        remember_check(path, False, str(error))
        return False, str(error)
    remember_check(path, True)
    return True, ""


def _remembered(path: Path) -> tuple[str, str]:
    try:
        return cache.get(_check_key(path)) or ("", "")
    except OSError:
        return "", ""


def _describe(path: Path) -> Archive:
    stat = path.stat()
    archive = Archive(
        name=path.name,
        path=path,
        size=stat.st_size,
        made_at=dt.datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()),
        uploaded=path.name.startswith("postulo-upload-"),
    )
    archive.checked, archive.problem = _remembered(path)
    try:
        with tarfile.open(path, "r:gz") as opened:
            manifest = read_manifest(opened)
    except (BackupError, tarfile.TarError, OSError, EOFError) as error:
        archive.unreadable = True
        archive.problem = archive.problem or str(error)
        return archive
    archive.version = str((manifest.get("postulo") or {}).get("version", ""))
    archive.engine = str((manifest.get("database") or {}).get("engine", ""))
    counts = manifest.get("counts") or {}
    archive.counts = {str(k): v for k, v in counts.items() if isinstance(v, int)}
    created = (manifest.get("postulo") or {}).get("created_at")
    try:
        archive.made_at = dt.datetime.fromisoformat(created).astimezone(
            timezone.get_current_timezone()
        )
    except (TypeError, ValueError):
        pass
    return archive


def archives() -> list[Archive]:
    """Every archive in the directory, newest first. Reads each manifest, never a database."""
    root = directory()
    if not root.is_dir():
        return []
    found = []
    for entry in root.iterdir():
        try:
            path = archive_path(entry.name)
        except NotAnArchive:
            continue
        found.append(_describe(path))
    found.sort(key=lambda a: (a.made_at, a.name), reverse=True)
    return found


def newest() -> Archive | None:
    listed = archives()
    return listed[0] if listed else None


def free_space() -> int | None:
    """Bytes free on the volume the archives go to, or ``None`` if it cannot be asked."""
    probe = directory()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def room_warning(listed: list[Archive], free: int | None) -> bool:
    """Whether the next backup is unlikely to fit, judged by the newest one's size.

    A quarter over, because a database grows between two backups and the media directory
    with it. Nothing to judge by before the first archive, so no warning then.
    """
    if free is None or not listed:
        return False
    return free < listed[0].size * 1.25


def delete_blocked(name: str) -> str:
    """Why this archive may not be deleted, or ``""``.

    The newest archive that is known to open is the one thing standing between an instance
    and having no backup it can trust, so it stays while nothing else is known to open.
    """
    listed = archives()
    mine = next((a for a in listed if a.name == name), None)
    if mine is None or mine.checked == "failed" or mine.unreadable:
        return ""
    if not any(a.checked == "ok" and a.name != name for a in listed):
        return str(
            _(
                "This is the only archive that is known to open. Check another one first, "
                "or take a new backup, and then this one can go."
            )
        )
    return ""


def delete(name: str) -> None:
    archive_path(name).unlink()


# ------------------------------------------------------------------------------ running


class Busy(Exception):
    """A backup is already running."""


def running() -> bool:
    return cache.get(LOCK_KEY) is not None


def take() -> Path:
    """Write one verified archive into the directory; remember that it verified.

    Raises :class:`Busy` when another is running and :class:`BackupError` when the archive
    could not be written or did not verify.
    """
    token = uuid.uuid4().hex
    if not cache.add(LOCK_KEY, token, LOCK_SECONDS):
        raise Busy
    try:
        report = write_backup()
        # `write_backup` has just verified it, so the page does not need to again.
        remember_check(report.path, True)
        return report.path
    finally:
        if cache.get(LOCK_KEY) == token:
            cache.delete(LOCK_KEY)


# ---------------------------------------------------------------------------- the clock


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(site.default_time_zone())
    except Exception:
        return ZoneInfo("UTC")


def last_slot(row, now: dt.datetime | None = None) -> dt.datetime | None:
    """The most recent moment the schedule says a backup was due, or ``None`` when off."""
    if row.backup_schedule not in (DAILY, WEEKLY):
        return None
    local = (now or timezone.now()).astimezone(_zone())
    slot = local.replace(hour=row.backup_hour, minute=0, second=0, microsecond=0)
    if row.backup_schedule == DAILY:
        if slot > local:
            slot -= dt.timedelta(days=1)
    else:
        slot -= dt.timedelta(days=(slot.weekday() - row.backup_weekday) % 7)
        if slot > local:
            slot -= dt.timedelta(days=7)
    return slot


def next_slot(row, now: dt.datetime | None = None) -> dt.datetime | None:
    slot = last_slot(row, now)
    if slot is None:
        return None
    return slot + dt.timedelta(days=1 if row.backup_schedule == DAILY else 7)


def is_due(row, now: dt.datetime | None = None) -> bool:
    slot = last_slot(row, now)
    if slot is None:
        return False
    return row.backup_last_run_at is None or row.backup_last_run_at < slot


def is_missed(row, now: dt.datetime | None = None) -> bool:
    """A slot that came and went with nobody picking it up: no scheduler is going round."""
    now = now or timezone.now()
    slot = last_slot(row, now)
    return bool(slot and is_due(row, now) and now - slot > MISSED_AFTER)


def claim(row, now: dt.datetime | None = None) -> bool:
    """Say that this slot is being taken. Only one of two schedulers gets ``True``."""
    from .models import SiteSettings

    now = now or timezone.now()
    taken = SiteSettings.objects.filter(
        pk=row.pk, backup_last_run_at=row.backup_last_run_at
    ).update(backup_last_run_at=now, backup_last_run_ok=None)
    forget_current()
    return taken == 1


def record(ok: bool, message: str = "", *, scheduled: bool = False) -> None:
    """Write down how a backup went, in one statement. Never raises."""
    from .models import SiteSettings

    try:
        SiteSettings.get()
        changes: dict = {}
        if ok:
            changes.update(backup_last_ok_at=timezone.now(), backup_last_error="")
            if scheduled:
                changes.update(backup_last_run_ok=True)
        else:
            changes.update(
                backup_failures=F("backup_failures") + 1, backup_last_error=message[:500]
            )
            if scheduled:
                changes.update(backup_last_run_ok=False)
        SiteSettings.objects.filter(pk=1).update(**changes)
        forget_current()
    except Exception:
        logger.exception("Could not record how the backup went.")


def apply_retention(keep: int) -> list[str]:
    """Delete the backups the scheduler or somebody wrote beyond the newest ``keep``.

    Called only after a new archive has verified, and never touches an uploaded one: that is
    somebody's file to restore from, not a copy this schedule made.
    """
    listed = [a for a in archives() if not a.uploaded]
    gone = []
    for old in listed[max(keep, 1) :]:
        try:
            delete(old.name)
        except (NotAnArchive, OSError):
            logger.warning("Retention could not remove %s.", old.name)
            continue
        gone.append(old.name)
    if gone:
        logger.info("Retention kept the newest %s backups and removed %s.", keep, len(gone))
    return gone


def run_scheduled() -> None:
    """The scheduled backup itself: take it, keep the newest few, say how it went."""
    keep = site.current().backup_keep
    try:
        take()
    except Busy:
        record(False, str(_("Another backup was already running.")), scheduled=True)
        return
    except Exception as error:
        logger.exception("The scheduled backup failed.")
        record(False, str(error) or error.__class__.__name__, scheduled=True)
        return
    record(True, scheduled=True)
    apply_retention(keep)


def start_if_due(
    now: dt.datetime | None = None, *, queue: Callable[[], object] | None = None
) -> bool:
    """Called by every scheduler pass: start the backup when its slot has come.

    With a worker the caller passes `queue`, because a backup of a large media directory takes
    minutes and a pass that takes minutes stops the heartbeat the scheduler's healthcheck
    reads. The queue is the caller's rather than this module's: the task that runs a backup
    lives in `postulo.core.tasks`, which imports this module, so reaching for it from here
    would close a cycle.
    """
    row = site.current()
    if not is_due(row, now) or not claim(row, now):
        return False
    if queue is not None:
        try:
            queue()
            return True
        except Exception:
            logger.exception("Could not queue the scheduled backup; taking it here instead.")
    run_scheduled()
    return True
