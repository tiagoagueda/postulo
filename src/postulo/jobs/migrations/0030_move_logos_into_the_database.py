"""Move each company's logo from a file into a `CompanyLogo` row (#662).

The old file field stays, and so does its file, until the release after this one: a
rollback finds its files. A file that is missing or no longer decodes is reported, and that
company is left with no logo.
"""

from django.db import migrations


def move(apps, schema_editor):
    from postulo.core import legacy_pictures

    Company = apps.get_model("jobs", "Company")
    CompanyLogo = apps.get_model("jobs", "CompanyLogo")
    left_out = []
    for pk, name in Company.objects.exclude(logo="").values_list("pk", "logo"):
        data, media_type = legacy_pictures.stored_form_of(name)
        if data is None:
            left_out.append(f"{name} (company {pk}): {media_type}")
            # As if it had been removed: no logo, and nothing saying where one came from.
            Company.objects.filter(pk=pk).update(
                logo_source="", logo_source_url="", logo_fetched_at=None
            )
            continue
        CompanyLogo.objects.update_or_create(
            company_id=pk, defaults={"data": data, "media_type": media_type}
        )
        Company.objects.filter(pk=pk).update(has_logo=True)
    legacy_pictures.report(left_out)


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0029_pictures_in_the_database"),
    ]

    operations = [
        migrations.RunPython(move, migrations.RunPython.noop),
    ]
