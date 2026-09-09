"""Which language somebody's career record is written in.

Blank for everybody, which is what it means: "the same as the interface". Guessing would
have been possible -- the profile already has a language -- and would have been a claim
about somebody's text made from a setting about their menus, which is exactly the pairing
this field exists to stop conflating (#131).

It earns its place by deciding when *not* to warn. Without it, a CV declaring the language
the career is already written in would report every entry as having fallen back.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_recovery_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="record_language",
            field=models.CharField(
                blank=True, max_length=10, verbose_name="language of your career record"
            ),
        ),
    ]
