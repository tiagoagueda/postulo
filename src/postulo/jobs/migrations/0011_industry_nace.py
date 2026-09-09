"""Room for a classification's name, and the code that goes with it.

`Industry` names were capped at sixty characters, which is generous for a word somebody
types and too short for a NACE division: *Computing infrastructure, data processing, hosting
and other information service activities* is ninety-two (#140).

**Nothing is filled in here, and that is the point.** Every existing row is somebody's own
word about an employer, and a migration that went looking for NACE codes to attach to them
would be this project deciding what a person meant. A code arrives when a name is saved that
matches a division, and never retroactively.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0010_identifier_schemes"),
    ]

    operations = [
        migrations.AddField(
            model_name="industry",
            name="code",
            field=models.CharField(blank=True, max_length=4, verbose_name="NACE division"),
        ),
        migrations.AlterField(
            model_name="industry",
            name="name",
            field=models.CharField(max_length=160, verbose_name="name"),
        ),
        migrations.AlterField(
            model_name="industry",
            name="slug",
            field=models.SlugField(max_length=160, verbose_name="slug"),
        ),
    ]
