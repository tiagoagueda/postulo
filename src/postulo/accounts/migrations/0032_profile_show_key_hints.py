"""A switch for the key badges, apart from the switch for the keys (#658)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0031_profile_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="show_key_hints",
            field=models.BooleanField(default=True, verbose_name="show key hints"),
        ),
    ]
