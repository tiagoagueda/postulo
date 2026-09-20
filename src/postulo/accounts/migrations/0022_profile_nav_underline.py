"""The underline on the current navigation link becomes a preference (#289)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_invite_token_fingerprint"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="nav_underline",
            field=models.BooleanField(default=True, verbose_name="underline the page you are on"),
        ),
    ]
