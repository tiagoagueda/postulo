"""Give the official repository the address and the key of the catalogue Postulo publishes.

0004 created the row switched off and pointing nowhere, because there was no catalogue to
point at. There is one now (``catalogue/official/``), so the row learns where it is and
which key signs it, and **stays switched off**: Postulo makes no request nobody asked for,
and an administrator turns the tier on from *Server settings → Plugins*.

The address and the key are written here as literals, not read from ``provenance``: a
migration describes what was true on the day it ran, and a later rotation is a migration of
its own rather than an edit to this one.

A row an administrator already filled in (an address, a key, either) is theirs and is left
alone, and so is an instance whose environment names its own ``official`` catalogue.
"""

from django.db import migrations

URL = "https://source.tiagoagueda.com/postulo/postulo/raw/branch/main/catalogue/official/index.json"
KEY = "p3GI+PsmgC3a4pcxgZUCUr79thUMtP8CaX5nhVtmeZ4="


def point(apps, schema_editor):
    PluginRepository = apps.get_model("plugins", "PluginRepository")
    PluginRepository.objects.filter(tier="official", url="", public_key="").update(
        url=URL, public_key=KEY
    )


def unpoint(apps, schema_editor):
    PluginRepository = apps.get_model("plugins", "PluginRepository")
    PluginRepository.objects.filter(tier="official", url=URL, public_key=KEY).update(
        url="", public_key="", enabled=False
    )


class Migration(migrations.Migration):
    dependencies = [("plugins", "0008_connection_last_report")]
    operations = [migrations.RunPython(point, unpoint)]
