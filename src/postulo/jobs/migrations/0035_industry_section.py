"""An industry gains its sector, the NACE section above it (#897).

Every industry whose name is a NACE division takes that division's section, and every one
whose name is one of Postulo's short names takes the section that name implies. A word of
the person's own has no sector until somebody chooses one, which is allowed and draws
plainly. Read from the vendored classification, offline.
"""

from django.db import migrations, models


def fill_sections(apps, schema_editor):
    from postulo.jobs import industries

    Industry = apps.get_model("jobs", "Industry")
    for industry in Industry.objects.iterator():
        section = industries.section_of(industry.code) or industries.starter_section(
            industry.name
        )
        if section:
            Industry.objects.filter(pk=industry.pk).update(section=section)


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0034_company_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="industry",
            name="section",
            field=models.CharField(blank=True, max_length=1, verbose_name="sector"),
        ),
        migrations.RunPython(fill_sections, migrations.RunPython.noop, elidable=True),
    ]
