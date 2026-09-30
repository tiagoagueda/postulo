"""A form of address and pronouns on the profile, as two answers (#309).

Both blank for every profile that exists, which is what nobody having said anything looks
like: nothing is assumed, and nothing is worked out from a name.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0026_profile_navigation_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="form_of_address",
            field=models.CharField(blank=True, max_length=40, verbose_name="form of address"),
        ),
        migrations.AddField(
            model_name="profile",
            name="pronouns",
            field=models.CharField(blank=True, max_length=40, verbose_name="pronouns"),
        ),
    ]
