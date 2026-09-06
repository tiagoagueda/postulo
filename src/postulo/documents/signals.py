"""A new document is offered to every external store its owner has connected.

Nothing observed a file being created until now except the code creating it. A signal is
the right observer here because documents are created from several places — recording
what was sent, uploading, importing an archive, the demo seed — and every one of them
should behave the same. Only creation counts; an edit to a title does not resend a file.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import RenderedDocument, UploadedDocument


@receiver(post_save, sender=RenderedDocument, dispatch_uid="documents.copy_render")
@receiver(post_save, sender=UploadedDocument, dispatch_uid="documents.copy_upload")
def schedule_copies_on_creation(sender, instance, created, raw=False, **kwargs) -> None:
    if not created or raw or not instance.file:
        return
    from .archiving import schedule_copies

    schedule_copies(instance)
