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


def sync_connection(connection: Connection) -> SyncReport:
    """Run one connection's plugin once, and record how it went. Never raises."""
    now = timezone.now()
    plugin = connection.plugin_instance
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
    connection.last_summary = report.summary()[:500]
    connection.save(
        update_fields=["synced_at", "last_ok_at", "last_error", "last_summary", "updated_at"]
    )
    return report


def due_connections(now=None):
    now = now or timezone.now()
    return [
        connection
        for connection in Connection.objects.filter(kind="sync", enabled=True).select_related(
            "owner"
        )
        if is_due(connection, now)
    ]


def run_syncs() -> tuple[int, int]:
    """Run every sync connection that is due. Returns (ran, failed)."""
    ran = failed = 0
    for connection in due_connections():
        report = sync_connection(connection)
        ran += 1
        if report.error:
            failed += 1
    return ran, failed
