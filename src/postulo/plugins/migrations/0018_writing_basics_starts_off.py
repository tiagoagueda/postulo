"""Writing basics starts switched off, for the instance (#1258).

Every plugin Postulo ships is on until an administrator says otherwise. This one is not:
its hints about length, repetition and long sentences are opinions about somebody's letter,
where the placeholder checks beside it are facts, and whether an instance offers them is the
operator's decision. So the row an administrator would write by switching it off on *Server
settings -> Plugins* is written here, once, for the instance; switching it on there removes
it, as for any other plugin.

Written only where no instance-wide row exists, so an instance that already decided keeps its
decision; reversing the migration removes the row it would have written, and no other.
"""

from django.db import migrations

PLUGIN = "writing-basics"


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
