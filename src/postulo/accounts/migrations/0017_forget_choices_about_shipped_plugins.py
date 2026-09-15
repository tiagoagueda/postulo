"""A person's own choice no longer applies to a plugin Postulo ships (#200).

The reader stopped consulting `plugins_off` for those; this takes the names out, so that
a list which looks like a set of choices does not go on holding choices nobody reads. What
is shipped is asked of the live registry, which `ready()` has filled by the time a
migration runs, so this needs no list of names to go stale.
"""

from django.db import migrations


def forget(apps, schema_editor):
    from postulo.plugins.installing import is_internal

    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.order_by("pk").iterator():
        before = list(profile.plugins_off or [])
        kept = [name for name in before if not is_internal(name)]
        if kept != before:
            profile.plugins_off = kept
            profile.save(update_fields=["plugins_off"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0016_remove_profile_web_link_columns")]

    operations = [migrations.RunPython(forget, migrations.RunPython.noop)]
