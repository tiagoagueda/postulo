"""Delete the CV entries whose career entry was deleted (#381).

Deleting a job from *Your career* used to leave its place on every CV behind, a row that
could not be printed and that the person could not reach to remove. The relation now
cascades; this removes the rows already left over.
"""

from django.db import migrations


def drop_orphans(apps, schema_editor):
    CVItem = apps.get_model("documents", "CVItem")
    ContentType = apps.get_model("contenttypes", "ContentType")
    for content_type in ContentType.objects.filter(cvitem__isnull=False).distinct():
        try:
            target = apps.get_model(content_type.app_label, content_type.model)
        except LookupError:
            # The kind itself is gone, so every row that names it points at nothing.
            CVItem.objects.filter(content_type=content_type).delete()
            continue
        alive = target._default_manager.values("pk")
        CVItem.objects.filter(content_type=content_type).exclude(object_id__in=alive).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0014_type_labels"),
        ("resume", "0008_a_language_is_a_tag"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(drop_orphans, migrations.RunPython.noop)]
