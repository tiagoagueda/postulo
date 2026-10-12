"""A person's own numbers stay unique across the instance; a recorded one, per holder (#329).

The flag is filled from the holder's type before the constraints that read it exist: every
number a profile holds identifies its owner, and every other number -- a contact's -- was
merely recorded. Addresses become unique per holder rather than per owner.
"""

from django.conf import settings
from django.db import migrations, models


def mark_profile_numbers(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    PhoneNumber = apps.get_model("core", "PhoneNumber")
    profile = ContentType.objects.filter(app_label="accounts", model="profile").first()
    if profile is not None:
        PhoneNumber.objects.filter(content_type=profile).update(identifies_owner=True)


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0033_map_tiles'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='phonenumber',
            name='phone_number_unique_across_the_instance',
        ),
        migrations.RemoveConstraint(
            model_name='postaladdress',
            name='address_unique_per_owner',
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='identifies_owner',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.RunPython(mark_profile_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='phonenumber',
            constraint=models.UniqueConstraint(condition=models.Q(models.Q(('normalised', ''), _negated=True), ('identifies_owner', True)), fields=('normalised',), name='phone_number_unique_across_the_instance'),
        ),
        migrations.AddConstraint(
            model_name='phonenumber',
            constraint=models.UniqueConstraint(condition=models.Q(('normalised', ''), _negated=True), fields=('content_type', 'object_id', 'normalised'), name='phone_number_once_per_holder'),
        ),
        migrations.AddConstraint(
            model_name='postaladdress',
            constraint=models.UniqueConstraint(condition=models.Q(('comparable', ''), _negated=True), fields=('content_type', 'object_id', 'comparable'), name='address_unique_per_holder'),
        ),
    ]
