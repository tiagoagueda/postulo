"""The table the default cache lives in.

Django ships ``createcachetable`` for this, and asking an operator to run a management
command before their instance can count a failed sign-in is a step nobody will remember.
The command is idempotent and looks at ``CACHES``, so on an instance pointed at Redis it
finds no database cache and does nothing at all.
"""

from django.core.management import call_command
from django.db import migrations


def create(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


def drop(apps, schema_editor):
    from django.conf import settings

    for config in settings.CACHES.values():
        if config["BACKEND"] != "django.core.cache.backends.db.DatabaseCache":
            continue
        table = config["LOCATION"]
        if table in schema_editor.connection.introspection.table_names():
            schema_editor.execute(f"DROP TABLE {schema_editor.quote_name(table)}")


class Migration(migrations.Migration):
    dependencies = [("core", "0002_sitesettings")]

    operations = [migrations.RunPython(create, drop)]
