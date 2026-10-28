"""Which archives are waiting for the backup destination: the rows, and nothing else (#325).

Below both `core.backups` and `core.offsite`, because each needs it: the backups module queues
an archive the moment it verifies and asks which archives retention must leave alone, and the
offsite module sends what is queued and needs `backups` to find the files. Kept to the
`OffsiteBackup` rows and the chosen destination's name, so it imports neither of them.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from . import site

logger = logging.getLogger(__name__)


def chosen_name() -> str:
    """The destination an administrator chose, by name, installed or not; ``""`` for none."""
    return site.current().backup_destination or ""


def enqueue(name: str):
    """One pending row for archive ``name`` at the chosen destination; the row, or ``None``.

    The caller has already checked that ``name`` is one of Postulo's own archives, in its
    directory (`backups.queue_send`). Idempotent: an archive queued twice is one row.
    """
    from .models import OffsiteBackup, OffsiteState

    destination = chosen_name()
    if not destination:
        return None
    row, created = OffsiteBackup.objects.get_or_create(
        archive=name,
        destination=destination,
        defaults={"state": OffsiteState.PENDING, "next_attempt_at": timezone.now()},
    )
    if created:
        logger.info("Backup %s queued for the %s destination.", name, destination)
    return row


def held_back(destination: str | None = None) -> set[str]:
    """The archives local retention must not delete: not yet at the chosen destination.

    Whether waiting or failed, and however many attempts they have spent: a failed send
    never deletes or rotates the archive it could not send. A row for a destination that is
    no longer chosen holds nothing back, or changing the destination would pin every archive
    the old one never received.
    """
    from .models import OffsiteBackup, OffsiteState

    destination = chosen_name() if destination is None else destination
    if not destination:
        return set()
    return set(
        OffsiteBackup.objects.filter(
            destination=destination, state__in=(OffsiteState.PENDING, OffsiteState.FAILED)
        ).values_list("archive", flat=True)
    )
