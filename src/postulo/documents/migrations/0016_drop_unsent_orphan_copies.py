"""Delete the unsent copies whose connection was removed (#511).

They could only fail again, on every pass and every *Send now*. Sent copies stay: they are
the reference to where the file went.
"""

from django.db import migrations


def drop_orphans(apps, schema_editor):
    DocumentCopy = apps.get_model("documents", "DocumentCopy")
    DocumentCopy.objects.filter(connection__isnull=True).exclude(status="sent").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0015_drop_cv_entries_without_a_target"),
    ]

    operations = [migrations.RunPython(drop_orphans, migrations.RunPython.noop)]
