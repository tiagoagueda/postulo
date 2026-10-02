"""The identifier schemes an instance defines for itself, on its policy row (#311).

One text column, holding the JSON an administrator types on *Server settings → Plugins*.
Blank on every instance there is, which is what it means to define none: the registry is
then the schemes Postulo ships, as it was. Going back drops the column and the definitions
with it; the identifiers stored under them stay, shown under the key they were stored with.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_web_link_once_per_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="identifier_schemes",
            field=models.TextField(blank=True, verbose_name="identifier schemes of its own"),
        ),
    ]
