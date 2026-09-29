"""A snapshot keeps its words as well as its markup, and an export stays here (#236).

**The column is added empty and stays empty for what is already there.** A CV frozen before
this has its themed page in `source_text` and nothing else, and the words cannot be put
back: they would have to be built from the CV as it stands today, which is the one thing a
snapshot exists not to be. The comparison says so for those rather than diffing markup.

**What was waiting to go to a store, for a PDF exported on its own, is withdrawn.** From
here on a store is sent what went with an application, and `schedule_copies` queues nothing
for anything else -- but a copy queued last week is a row this release would still send,
and one that had failed would go on saying so beside a document that no longer has a button
to try it again. So the queue is brought into line with the rule, once.

Only the queue. **A copy a store already holds is left exactly where it is**, and so is one
the store declined: those are records of what happened, and nothing here reaches into a
store to take anything back. A report is left alone too, for the reason
`RenderedDocument.goes_to_stores` gives.

The rule is written out here rather than imported from the model, so that a later change to
it cannot change what this migration did -- the reason `0010` gives for its own.
"""

from django.db import migrations, models


def withdraw_what_was_waiting(apps, schema_editor):
    RenderedDocument = apps.get_model("documents", "RenderedDocument")
    DocumentCopy = apps.get_model("documents", "DocumentCopy")
    ContentType = apps.get_model("contenttypes", "ContentType")

    renders = ContentType.objects.filter(app_label="documents", model="rendereddocument").first()
    if renders is None:
        return
    # `sent_to` as well as the link: an application that was deleted leaves the words, and
    # a PDF an employer received is not an export because somebody tidied up (#217).
    on_their_own = RenderedDocument.objects.filter(application__isnull=True, sent_to="").exclude(
        kind="report"
    )
    DocumentCopy.objects.filter(
        document_type_id=renders.pk,
        document_id__in=on_their_own.values("pk"),
        status__in=("pending", "failed"),
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0010_document_language"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="rendereddocument",
            name="plain_text",
            field=models.TextField(
                blank=True, editable=False, verbose_name="text without its layout"
            ),
        ),
        migrations.RunPython(withdraw_what_was_waiting, migrations.RunPython.noop),
    ]
