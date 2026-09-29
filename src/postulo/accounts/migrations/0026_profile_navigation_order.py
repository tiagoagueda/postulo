"""The main navigation takes the person's own order (#299).

A new list beside `hidden_nav_items`, empty for every profile that exists: empty is the
default order, which is the order they have been reading all along, so nothing is
carried, rewritten or lost. The keys a person places are what it holds; an item in
neither list is drawn after them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_profile_keep_captured_page"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="nav_order",
            field=models.JSONField(blank=True, default=list, verbose_name="navigation order"),
        ),
    ]
