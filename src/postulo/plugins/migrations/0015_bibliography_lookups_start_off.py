"""Bibliography lookups start switched off, for the instance (#879).

The same decision as Register lookup's (0012): a DOI typed into the bibliography import is
sent to doi.org, which so learns what somebody on this instance is reading, and whether this
instance talks to it at all is the operator's decision. So the row an administrator would
write by switching the feature off on *Server settings -> Plugins* is written here, once, for
the instance; switching it on there removes it, as for any other plugin.

Written only where no instance-wide row exists, so an instance that already decided keeps its
decision; reversing the migration removes the row it would have written, and no other.
"""

from django.db import migrations

PLUGIN = "bibliography-lookups"


def start_off(apps, schema_editor):
    PluginPolicy = apps.get_model("plugins", "PluginPolicy")
    if not PluginPolicy.objects.filter(plugin=PLUGIN, person__isnull=True).exists():
        PluginPolicy.objects.create(plugin=PLUGIN, person=None, state="off")


def forget(apps, schema_editor):
    PluginPolicy = apps.get_model("plugins", "PluginPolicy")
    PluginPolicy.objects.filter(
        plugin=PLUGIN, person__isnull=True, state="off", decided_by__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("plugins", "0014_connection_sync_state"),
    ]

    operations = [migrations.RunPython(start_off, forget)]
