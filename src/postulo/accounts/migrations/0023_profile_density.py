"""How much room the interface leaves, chosen per account (#292).

Additive, and comfortable is the default, so every existing account keeps exactly the
interface it had. Nothing is computed from anything; there is nothing to carry across.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_profile_nav_underline"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="density",
            field=models.CharField(
                choices=[("comfortable", "Comfortable"), ("compact", "Compact")],
                default="comfortable",
                max_length=12,
                verbose_name="density",
            ),
        ),
    ]
