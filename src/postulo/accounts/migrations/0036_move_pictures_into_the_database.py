"""Move each avatar and each Gravatar copy from a file into a `ProfilePicture` row (#662).

The old file fields stay, and so do their files, until the release after this one: a
rollback finds its files. A file that is missing or no longer decodes is reported, and that
picture becomes none.
"""

from django.db import migrations

KINDS = (("avatar", "upload", "has_avatar"), ("gravatar_image", "gravatar", "has_gravatar_copy"))


def move(apps, schema_editor):
    from postulo.core import legacy_pictures

    Profile = apps.get_model("accounts", "Profile")
    ProfilePicture = apps.get_model("accounts", "ProfilePicture")
    left_out = []
    for field, kind, flag in KINDS:
        for pk, user_id, name in Profile.objects.exclude(**{field: ""}).values_list(
            "pk", "user_id", field
        ):
            data, media_type = legacy_pictures.stored_form_of(name)
            if data is None:
                left_out.append(f"{name} (user {user_id}): {media_type}")
                continue
            ProfilePicture.objects.update_or_create(
                profile_id=pk, kind=kind, defaults={"data": data, "media_type": media_type}
            )
            Profile.objects.filter(pk=pk).update(**{flag: True})
    legacy_pictures.report(left_out)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0035_pictures_in_the_database"),
    ]

    operations = [
        # Not reversible into files: a rollback returns to the old fields, which were
        # never emptied, and the rows are dropped with their table.
        migrations.RunPython(move, migrations.RunPython.noop),
    ]
