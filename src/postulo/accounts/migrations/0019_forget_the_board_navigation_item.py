"""Board is no longer an entry in the navigation -- it is a shape of the Applications page
(#102) -- so a preference to hide that entry is about something that no longer exists. The
key comes out of every profile's hidden list; the board itself stays reachable, inside
Applications, for everybody.
"""

from django.db import migrations


def forget_board(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.exclude(hidden_nav_items=[]):
        hidden = [key for key in (profile.hidden_nav_items or []) if key != "board"]
        if hidden != profile.hidden_nav_items:
            profile.hidden_nav_items = hidden
            profile.save(update_fields=["hidden_nav_items"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_profile_show_career_order"),
    ]

    operations = [
        migrations.RunPython(forget_board, migrations.RunPython.noop),
    ]
