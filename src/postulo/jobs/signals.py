"""What happens to the files a capture kept when the row that names them goes (#256).

Django never removes a file when the row pointing at it is deleted, so a deletion that
stopped at the database would leave a copy of somebody else's page on the disk with nothing
pointing at it: hidden, not deleted, copied into every backup, and removed by nobody. #217
found documents doing exactly that and settled how it is put right; this does the same for
a kept page.

A signal rather than a `delete()` on the model, because most of the ways a kept page goes
never call one. Deleting an account cascades through its captures to their pages, and a
cascade runs the collector, not the model's own method -- but it does send `post_delete`
for every row, which is what makes this the one place that sees them all: a page thrown
away from its own screen, the scheduler expiring one that was never confirmed, a capture
deleted from the admin, an account closed.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import CapturedPage

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=CapturedPage, dispatch_uid="jobs.remove_captured_page_files")
def remove_the_files_from_disk(sender, instance, **kwargs) -> None:
    """Deleting what a capture kept deletes the files it kept.

    After the transaction commits, so a deletion that is rolled back does not take the
    files with it, and only where no other row uses the name. Nothing shares one today; an
    orphaned row with a live file is a far better failure than a live row with no file, so
    the check stays for the day something does.
    """
    fields = [field for field in (instance.source, instance.rendering) if field and field.name]
    if not fields:
        return
    wanted = [(field.storage, field.name) for field in fields]

    def remove() -> None:
        for storage, name in wanted:
            still_used = (
                CapturedPage.objects.filter(source=name).exists()
                or CapturedPage.objects.filter(rendering=name).exists()
            )
            if still_used:
                continue
            try:
                storage.delete(name)
            except OSError:  # pragma: no cover - a file already gone is what was wanted
                logger.warning("Could not remove %s from storage", name, exc_info=True)

    transaction.on_commit(remove)
