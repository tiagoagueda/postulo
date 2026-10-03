"""What happens to an export's bytes when its row goes (#355).

An export is a zip of somebody's whole job search, and it is written to disk. Django never
removes a file when the row pointing at it is deleted, and a cascade (an account closed, a
queryset delete, the admin) never calls a model's own `delete()`, which is where this used to
live and why the archive of a deleted account stayed on the disk for ever. `post_delete` is
sent for every row however it goes, so this is the one place that sees them all.

After the transaction commits, so a deletion that is rolled back does not take the file with
it: a row with no file is a far worse failure than a file with no row.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ExportArchive

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=ExportArchive, dispatch_uid="core.remove_export_archive_file")
def remove_the_bytes(sender, instance, **kwargs) -> None:
    stored = instance.file
    if not stored or not stored.name:
        return
    storage, name = stored.storage, stored.name

    def remove() -> None:
        try:
            storage.delete(name)
        except OSError:  # pragma: no cover - a file already gone is what was wanted
            logger.warning("Could not remove %s from storage", name, exc_info=True)

    transaction.on_commit(remove)
