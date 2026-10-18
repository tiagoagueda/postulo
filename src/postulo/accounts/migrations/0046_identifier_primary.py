"""A primary identifier (#922): one flag, one per person, and the first row of everybody who
already has identifiers is made it, so that a person with identifiers always has one."""

from django.db import migrations, models


def first_is_primary(apps, schema_editor):
    PersonIdentifier = apps.get_model("accounts", "PersonIdentifier")
    seen = set()
    for row in PersonIdentifier.objects.order_by("profile_id", "scheme", "value", "pk"):
        if row.profile_id in seen:
            continue
        seen.add(row.profile_id)
        PersonIdentifier.objects.filter(pk=row.pk).update(is_primary=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0045_encrypt_authenticators"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="personidentifier",
            options={
                "ordering": ("-is_primary", "scheme", "value"),
                "verbose_name": "identifier",
                "verbose_name_plural": "identifiers",
            },
        ),
        migrations.AddField(
            model_name="personidentifier",
            name="is_primary",
            field=models.BooleanField(default=False, verbose_name="primary"),
        ),
        migrations.RunPython(first_is_primary, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="personidentifier",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_primary", True)),
                fields=("profile",),
                name="one_primary_identifier_per_person",
            ),
        ),
    ]
