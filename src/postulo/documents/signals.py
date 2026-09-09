"""What happens to a document when something around it changes.

A new document is offered to every external store its owner has connected, and a render
forgets the thing that made it when that thing is deleted -- which is the `SET_NULL` a
column used to carry, written out because a generic link has no `on_delete` (#130).

Nothing observed a file being created until now except the code creating it. A signal is
the right observer here because documents are created from several places — recording
what was sent, uploading, importing an archive, the demo seed — and every one of them
should behave the same. Only creation counts; an edit to a title does not resend a file.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import CV, CoverLetter, RenderedDocument, UploadedDocument


@receiver(post_save, sender=RenderedDocument, dispatch_uid="documents.copy_render")
@receiver(post_save, sender=UploadedDocument, dispatch_uid="documents.copy_upload")
def schedule_copies_on_creation(sender, instance, created, raw=False, **kwargs) -> None:
    if not created or raw or not instance.file:
        return
    from .archiving import schedule_copies

    schedule_copies(instance)


@receiver(post_delete, sender=CV, dispatch_uid="documents.forget_cv_source")
@receiver(post_delete, sender=CoverLetter, dispatch_uid="documents.forget_letter_source")
def forget_the_source_of_a_render(sender, instance, **kwargs) -> None:
    """Deleting a CV must not delete the PDF an employer received.

    That used to be `on_delete=SET_NULL` on a column, and a generic link has no `on_delete`
    at all — so the behaviour is written out here rather than lost in the move (#130).
    Getting it backwards would delete somebody's record of what they sent, which is the one
    thing `RenderedDocument` exists to prevent.

    Cleared rather than left dangling: a `GenericForeignKey` reads a missing row as `None`
    on its own, but the columns would keep pointing at an id another row could later take.
    """
    from django.contrib.contenttypes.models import ContentType

    RenderedDocument.objects.filter(
        source_type=ContentType.objects.get_for_model(sender), source_id=instance.pk
    ).update(source_type=None, source_id=None)
