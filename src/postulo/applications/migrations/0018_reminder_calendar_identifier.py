import uuid

import postulo.applications.models
from django.db import migrations, models


def give_each_reminder_its_own(apps, schema_editor):
    """A column added with a default holds that one default in every row it finds.

    The identifier has to be unique per owner, so each reminder that was there before gets
    one of its own before the constraint is made.
    """
    Reminder = apps.get_model("applications", "Reminder")
    for reminder in Reminder.objects.all().iterator():
        reminder.uid = f"{uuid.uuid4()}@postulo"
        reminder.save(update_fields=["uid"])


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0017_public_employment_service_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="reminder",
            name="uid",
            field=models.CharField(
                default=postulo.applications.models._mint_uid,
                max_length=64,
                verbose_name="calendar identifier",
            ),
        ),
        migrations.RunPython(give_each_reminder_its_own, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="reminder",
            constraint=models.UniqueConstraint(
                fields=("owner", "uid"), name="reminder_uid_per_owner"
            ),
        ),
    ]
