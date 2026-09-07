"""Give an existing instance the rows its environment already implied.

Two things happen here, and neither changes what any instance can install.

**Whatever ``POSTULO_PLUGIN_CATALOGUES`` names becomes a row.** The environment still wins
at read time, so this alters nothing about which catalogues work — it means an operator
opening the page sees what their instance actually has, marked as pinned, rather than an
empty list beside a working catalogue.

**And the official tier gets its row, switched off and pointing nowhere.** Postulo publishes
no catalogue. Doing so means generating a signing key, keeping it safe for the life of the
project, and having an answer for rotating it if it leaks — a real responsibility, taken
deliberately or not at all. The row exists so the page can show the tier and say plainly
that it is empty, which is more honest than a heading with nothing under it.
"""

from django.conf import settings
from django.db import migrations


def seed(apps, schema_editor):
    PluginRepository = apps.get_model("plugins", "PluginRepository")

    raw = getattr(settings, "POSTULO_PLUGIN_CATALOGUES", "") or ""
    for part in raw.split(","):
        pieces = [piece.strip() for piece in part.strip().split("|")]
        if len(pieces) != 3 or not all(pieces):
            continue
        name, url, key = pieces
        # `official` is a name an operator may already have used for their own catalogue.
        # Theirs, then, and the tier follows the name rather than overruling it.
        tier = "official" if name == "official" else "custom"
        if tier == "official" and PluginRepository.objects.filter(tier="official").exists():
            tier = "custom"
        PluginRepository.objects.get_or_create(
            name=name[:50],
            defaults={"tier": tier, "url": url, "public_key": key, "enabled": True},
        )

    if not PluginRepository.objects.filter(tier="official").exists():
        PluginRepository.objects.create(
            name="official", tier="official", url="", public_key="", enabled=False
        )


def unseed(apps, schema_editor):
    """Only the empty official row goes. Anything an operator configured is theirs."""
    PluginRepository = apps.get_model("plugins", "PluginRepository")
    PluginRepository.objects.filter(tier="official", url="", public_key="").delete()


class Migration(migrations.Migration):
    dependencies = [("plugins", "0003_pluginrepository")]
    operations = [migrations.RunPython(seed, unseed)]
