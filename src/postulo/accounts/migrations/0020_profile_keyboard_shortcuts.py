"""A way to switch the single-key shortcuts off (#227).

"d", "j" and "/" did something on their own, with no way to stop them, which WCAG 2.1.4
refuses at level A. On for everybody who already has a profile, because that is what they
have been using; the switch is under Settings -> Appearance for anybody it gets in the way
of.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_forget_the_board_navigation_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="keyboard_shortcuts",
            field=models.BooleanField(default=True, verbose_name="single-key shortcuts"),
        ),
    ]
