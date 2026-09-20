"""An upload and a frozen snapshot each record the language they are in (#283).

Both columns are additive and blankable, which is what lets an archive written before this
be restored without inventing anything.

The backfill is only for snapshots, and only where the source is still there. A render's
language was until now read from its source at the moment a store asked, so a value *is*
recoverable for those: it is the same one the store was already being told. Freezing it is
therefore not a new claim, it is the claim that was already being made, written down before
an edit to the source can change it.

Uploads are left blank on purpose. Nobody has ever told Postulo what is inside them, and
the fallback this issue removes -- the owner's own interface language -- is exactly the
guess that filed a German certificate as English. The instance default is not used either,
for a render or an upload: it is a weaker claim still.

The resolution is written out here rather than imported from `rendering.document_language`,
so that a later change to that function cannot silently change what this migration did --
the same reason `accounts/0021` hashes with `hashlib` instead of importing the helper.
"""

from django.db import migrations, models


def freeze_the_language_of_each_snapshot(apps, schema_editor):
    RenderedDocument = apps.get_model("documents", "RenderedDocument")
    ContentType = apps.get_model("contenttypes", "ContentType")

    sources = {}
    for model_name in ("cv", "coverletter"):
        row = ContentType.objects.filter(app_label="documents", model=model_name).first()
        if row is not None:
            sources[row.pk] = apps.get_model("documents", model_name)

    for render in RenderedDocument.objects.exclude(source_id=None).iterator():
        model = sources.get(render.source_type_id)
        if model is None:
            continue
        source = model.objects.filter(pk=render.source_id).only("language", "owner_id").first()
        if source is None:
            continue
        language = (source.language or "").strip()
        if not language:
            profile = (
                apps.get_model("accounts", "Profile")
                .objects.filter(user_id=source.owner_id)
                .only("language")
                .first()
            )
            language = (getattr(profile, "language", "") or "").strip()
        if language:
            render.language = language
            render.save(update_fields=["language"])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0009_backfill_sent_to_and_upload_checksums"),
        ("accounts", "0022_profile_nav_underline"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadeddocument",
            name="language",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Which language this file is in. Postulo cannot read it, so nothing is "
                    "assumed; a store files it by this."
                ),
                max_length=10,
                verbose_name="language",
            ),
        ),
        migrations.AddField(
            model_name="rendereddocument",
            name="language",
            field=models.CharField(
                blank=True, editable=False, max_length=10, verbose_name="language"
            ),
        ),
        migrations.RunPython(freeze_the_language_of_each_snapshot, migrations.RunPython.noop),
    ]
