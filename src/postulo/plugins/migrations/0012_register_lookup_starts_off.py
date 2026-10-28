"""Register lookup starts switched off, for the instance (#1021).

Every feature Postulo ships is on until an administrator says otherwise, and that is the
right default for what stays on the instance. This one is not: a press sends a company's
number to a government's service, which learns which company somebody here is looking at, and
whether this instance talks to those services at all is the operator's decision. So the row an
administrator would write by switching it off on *Server settings -> Plugins* is written here,
once, for the instance; switching it on there removes it, as for any other plugin.

Written only where no instance-wide row exists, so an instance that already decided keeps its
decision; reversing the migration removes the row it would have written, and no other.
"""

from django.db import migrations

PLUGIN = "register-lookup"


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
        ("plugins", "0011_catalogueserial"),
    ]

    operations = [migrations.RunPython(start_off, forget)]
