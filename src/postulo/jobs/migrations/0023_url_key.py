"""Keep each address in the form `same_url` reduces it to (#556).

The duplicate check narrowed by the address's path and compared the rest in Python, over at
most 200 rows, so a board that carries the job id in the query stopped being checked past
200 listings. Both tables now hold the reduced address, indexed, and the rows already there
are filled in.
"""

from django.conf import settings
from django.db import migrations, models

from postulo.core.addresses import same_url


def fill(apps, schema_editor) -> None:
    for name in ("JobPosting", "Capture"):
        model = apps.get_model("jobs", name)
        batch = []
        for row in model.objects.exclude(url="").only("pk", "url").iterator(chunk_size=500):
            row.url_key = same_url(row.url)
            batch.append(row)
            if len(batch) >= 500:
                model.objects.bulk_update(batch, ["url_key"])
                batch = []
        if batch:
            model.objects.bulk_update(batch, ["url_key"])


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0015_type_labels'),
        ('jobs', '0022_type_labels'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='capture',
            name='url_key',
            field=models.CharField(blank=True, editable=False, max_length=500),
        ),
        migrations.AddField(
            model_name='jobposting',
            name='url_key',
            field=models.CharField(blank=True, editable=False, max_length=500),
        ),
        migrations.AddIndex(
            model_name='capture',
            index=models.Index(fields=['owner', 'url_key'], name='capture_owner_url_key'),
        ),
        migrations.AddIndex(
            model_name='jobposting',
            index=models.Index(fields=['owner', 'url_key'], name='jobposting_owner_url_key'),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
