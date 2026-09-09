"""Point a render at whatever made it, and a copy at whatever was copied.

Two pairs of nullable foreign keys become two generic links, so that the next kind of
document is a package rather than two more columns, an ``isinstance`` at every reader and a
migration (#130). `CVItem` decided this the other way one model over, and its docstring is
the argument.

**The order matters and is written out rather than left to the autodetector**, which wanted
to drop the old columns before anything had read them. Add, carry across, then remove — and
a reverse that carries back, so this is not a one-way door on somebody's live instance.

**Two `on_delete` behaviours had to be re-created, and they are not the same one.**
``RenderedDocument.cv`` was ``SET_NULL``: deleting a CV must not delete the PDF an employer
received, which is the whole point of that model. ``DocumentCopy.rendered`` was ``CASCADE``.
A generic foreign key has neither, so the cascade lives on a ``GenericRelation`` at each
document, and the *not*-cascade lives in a receiver that clears the link. Getting this
backwards would delete somebody's record of what they sent.
"""

from django.db import migrations, models
import django.db.models.deletion


def carry_across(apps, schema_editor):
    """Write the pair of columns into the generic link."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    RenderedDocument = apps.get_model("documents", "RenderedDocument")
    DocumentCopy = apps.get_model("documents", "DocumentCopy")

    cv_type = ContentType.objects.get_for_model(apps.get_model("documents", "CV"))
    letter_type = ContentType.objects.get_for_model(apps.get_model("documents", "CoverLetter"))
    render_type = ContentType.objects.get_for_model(RenderedDocument)
    upload_type = ContentType.objects.get_for_model(apps.get_model("documents", "UploadedDocument"))

    for render in RenderedDocument.objects.all().iterator():
        if render.cv_id:
            render.source_type_id, render.source_id = cv_type.pk, render.cv_id
        elif render.cover_letter_id:
            render.source_type_id, render.source_id = letter_type.pk, render.cover_letter_id
        else:
            continue
        render.save(update_fields=["source_type", "source_id"])

    for copy in DocumentCopy.objects.all().iterator():
        if copy.rendered_id:
            copy.document_type_id, copy.document_id = render_type.pk, copy.rendered_id
        elif copy.upload_id:
            copy.document_type_id, copy.document_id = upload_type.pk, copy.upload_id
        else:
            # The check constraint made this impossible, and a row that reached here anyway
            # is a row pointing at nothing. Left for the removal below to take with it.
            continue
        copy.save(update_fields=["document_type", "document_id"])


def carry_back(apps, schema_editor):
    """And the other way, so this is reversible on a live instance."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    RenderedDocument = apps.get_model("documents", "RenderedDocument")
    DocumentCopy = apps.get_model("documents", "DocumentCopy")

    cv_type = ContentType.objects.get_for_model(apps.get_model("documents", "CV"))
    letter_type = ContentType.objects.get_for_model(apps.get_model("documents", "CoverLetter"))
    render_type = ContentType.objects.get_for_model(RenderedDocument)

    for render in RenderedDocument.objects.all().iterator():
        if render.source_type_id == cv_type.pk:
            render.cv_id = render.source_id
        elif render.source_type_id == letter_type.pk:
            render.cover_letter_id = render.source_id
        else:
            continue
        render.save(update_fields=["cv", "cover_letter"])

    for copy in DocumentCopy.objects.all().iterator():
        if copy.document_type_id == render_type.pk:
            copy.rendered_id = copy.document_id
        else:
            copy.upload_id = copy.document_id
        copy.save(update_fields=["rendered", "upload"])


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("documents", "0004_letter_language"),
    ]

    operations = [
        # 1. The new columns, nullable on the copy for the length of this migration: a
        #    row cannot have a document before the loop below gives it one.
        migrations.AddField(
            model_name="rendereddocument",
            name="source_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="contenttypes.contenttype",
                verbose_name="kind of source",
            ),
        ),
        migrations.AddField(
            model_name="rendereddocument",
            name="source_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentcopy",
            name="document_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="contenttypes.contenttype",
                verbose_name="kind of document",
            ),
        ),
        migrations.AddField(
            model_name="documentcopy",
            name="document_id",
            field=models.PositiveBigIntegerField(null=True),
        ),
        # 2. The data, both ways.
        migrations.RunPython(carry_across, carry_back),
        # 3. The old rules, then the old columns. The check constraint goes first because
        #    it names columns that are about to stop existing.
        migrations.RemoveConstraint(
            model_name="documentcopy", name="documents_copy_of_one_document"
        ),
        migrations.RemoveConstraint(
            model_name="documentcopy", name="documents_copy_once_per_render"
        ),
        migrations.RemoveConstraint(
            model_name="documentcopy", name="documents_copy_once_per_upload"
        ),
        migrations.RemoveField(model_name="rendereddocument", name="cv"),
        migrations.RemoveField(model_name="rendereddocument", name="cover_letter"),
        migrations.RemoveField(model_name="documentcopy", name="rendered"),
        migrations.RemoveField(model_name="documentcopy", name="upload"),
        # 4. And the copy's link stops being nullable, now that every row has one.
        migrations.AlterField(
            model_name="documentcopy",
            name="document_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="contenttypes.contenttype",
                verbose_name="kind of document",
            ),
        ),
        migrations.AlterField(
            model_name="documentcopy",
            name="document_id",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AlterModelOptions(
            name="rendereddocument",
            options={
                "ordering": ("-rendered_at", "pk"),
                "verbose_name": "sent document",
                "verbose_name_plural": "sent documents",
            },
        ),
        migrations.AddIndex(
            model_name="rendereddocument",
            index=models.Index(
                fields=["source_type", "source_id"], name="documents_r_source__43cf84_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="documentcopy",
            index=models.Index(
                fields=["document_type", "document_id"], name="documents_d_documen_996e23_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="documentcopy",
            constraint=models.UniqueConstraint(
                condition=models.Q(("connection__isnull", False)),
                fields=("connection", "document_type", "document_id"),
                name="documents_copy_once_per_document",
            ),
        ),
    ]
