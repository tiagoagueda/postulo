"""Running the sync plugins: on the scheduler's pass, on their own interval, or on demand.

A **sync** keeps something in Postulo and something elsewhere the same — contacts in an
address book, interviews in a calendar — in both directions. The plugin does the
comparing; this module decides *when* it runs, hands it its connection, and keeps the
outcome where a person can see it. Each connection carries an interval of its own, and
a pass of the scheduler runs those that are due. *Sync now* runs one at once.

What links a local record to its remote twin is a :class:`~postulo.plugins.models.SyncLink`
row — the remote address, the identifier the remote uses, the version tag it last gave,
and a hash of what was last pushed — kept beside the record and never on it. A plugin
reads and writes those through the connection; nothing else in Postulo knows they exist.
"""

from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base import FieldSpec, SyncReport
from .models import Connection
from .secrets import SecretsUnreadable

logger = logging.getLogger(__name__)

#: How often a connection may run, in minutes, as the form offers them.
INTERVALS = (
    ("15", _("Every 15 minutes")),
    ("60", _("Every hour")),
    ("240", _("Every four hours")),
    ("1440", _("Once a day")),
)
DEFAULT_INTERVAL = "60"

#: How long a connection's lease lasts if the run holding it dies without letting go.
LEASE_SECONDS = 15 * 60


def kind_specs() -> list[FieldSpec]:
    """What every sync connection carries, whatever the plugin: how often to run."""
    return [
        FieldSpec(
            "interval",
            str(_("Run")),
            type="choice",
            choices=INTERVALS,
            default=DEFAULT_INTERVAL,
            help=str(
                _("Changes made here are pushed on the next run; the other side is read then too.")
            ),
        )
    ]


def interval_minutes(connection: Connection) -> int:
    try:
        return max(int(connection.config.get("interval") or DEFAULT_INTERVAL), 1)
    except (TypeError, ValueError):
        return int(DEFAULT_INTERVAL)


def is_due(connection: Connection, now=None) -> bool:
    now = now or timezone.now()
    if connection.synced_at is None:
        return True
    elapsed = (now - connection.synced_at).total_seconds() / 60
    return elapsed >= interval_minutes(connection)


def lease_key(connection: Connection) -> str:
    return f"postulo:sync:{connection.pk}"


def sync_connection(connection: Connection) -> SyncReport:
    """Run one connection's plugin once, and record how it went. Never raises.

    One connection runs once at a time: a plugin decides what to push and what to adopt
    from the links it read at the start, so two overlapping runs would each do the other's
    work again. The run that cannot take the connection's lease returns a report that says
    so, without calling the plugin and without touching the connection (#586). The lease
    is in the cache, which is a table of the same database, so a caller inside a
    transaction would not make it visible to anyone else until it ended; *Sync now* is
    therefore not atomic.
    """
    key = lease_key(connection)
    if not cache.add(key, timezone.now().isoformat(), LEASE_SECONDS):
        return SyncReport(
            notes=[str(_("A sync of this connection is already running."))],
            already_running=True,
        )
    try:
        return _run_connection(connection)
    finally:
        cache.delete(key)


def _run_connection(connection: Connection) -> SyncReport:
    now = timezone.now()
    plugin = connection.plugin_instance
    if plugin is not None and not connection.allowed:
        # Off for its owner: nothing runs and nothing is recorded, so reversing the decision
        # finds the connection as it was (#362).
        return SyncReport(error=str(_("That plugin is switched off for you.")))
    if plugin is None:
        report = SyncReport(
            error=str(_("The %(plugin)s plugin is not installed.")) % {"plugin": connection.plugin}
        )
    else:
        try:
            report = plugin.sync(connection, connection.full_config)
        except SecretsUnreadable as error:
            report = SyncReport(error=str(error))
        except Exception as error:
            logger.exception("Sync %r failed for connection %s", connection.plugin, connection.pk)
            report = SyncReport(error=f"{type(error).__name__}: {error}")
    connection.synced_at = now
    if report.error:
        connection.last_error = report.error[:500]
    else:
        connection.last_ok_at = now
        connection.last_error = ""
    connection.last_report = report.record()
    connection.save(
        update_fields=["synced_at", "last_ok_at", "last_error", "last_report", "updated_at"]
    )
    return report


def claim_connection(connection: Connection) -> bool:
    """Take a connection's turn before running it, or say that another run already has.

    ``due_connections`` reads ``synced_at`` and ``sync_connection`` writes it only when the
    sync has finished, so two passes that overlap both found the same connection due and ran
    it at once (#576). This stamps ``synced_at`` with a conditional ``UPDATE`` first, the way
    a reminder is stamped before it is announced: whichever run changes the row owns the turn,
    and the other sees no row changed. The stamp is replaced by the real time when the sync
    ends; if the run dies, the connection waits one interval, not forever.
    """
    claimed = Connection.objects.filter(
        pk=connection.pk,
        synced_at=connection.synced_at,
    ).update(synced_at=timezone.now())
    return bool(claimed)


def due_connections(now=None):
    now = now or timezone.now()
    return [
        connection
        for connection in Connection.objects.filter(
            kind="sync", enabled=True, owner__is_active=True
        ).select_related("owner")
        if is_due(connection, now) and connection.allowed
    ]


def run_syncs(*, budget: int = 0) -> tuple[int, int]:
    """Run every sync connection that is due. Returns (ran, failed).

    With a ``budget`` in seconds, connections are started until that much time has gone and
    the rest are left to the next pass. They run inline, one after another, so before this a
    single calendar server that answered slowly held up every reminder behind it -- and the
    slower it was, the longer the queue it was holding (#221). A connection already started
    is never cut off: the budget decides whether to begin another, which is the only point
    at which stopping is safe.
    """
    started = time.monotonic()
    ran = failed = 0
    for connection in due_connections():
        if budget and time.monotonic() - started >= budget:
            logger.info("Sync budget of %ss spent; the rest wait for the next pass", budget)
            break
        if not claim_connection(connection):
            logger.info("Sync connection %s was claimed by another run", connection.pk)
            continue
        try:
            report = sync_connection(connection)
        except Exception:
            # One sync that raises is one sync. It used to be the whole pass.
            logger.exception("Sync connection %s ended badly", connection.pk)
            ran += 1
            failed += 1
            continue
        ran += 1
        if report.error:
            failed += 1
    return ran, failed
