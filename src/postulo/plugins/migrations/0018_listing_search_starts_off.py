"""Listing search starts switched off, for the instance (#1253).

The same decision as Register lookup's (0012): a press sends the words, the place and the
filters somebody searched for to a job service, which so learns what somebody on this instance
is looking for, and whether this instance talks to those services at all is the operator's
decision. So the row an administrator would write by switching the feature off on *Server
settings -> Plugins* is written here, once, for the instance; switching it on there removes it,
as for any other plugin. Each service's own switch is left alone: it follows the feature.

Written only where no instance-wide row exists, so an instance that already decided keeps its
decision; reversing the migration removes the row it would have written, and no other.
"""

from django.db import migrations

PLUGIN = "listing-search"


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
        ("plugins", "0017_catalogue_answers_and_update_settings"),
    ]

    operations = [migrations.RunPython(start_off, forget)]
