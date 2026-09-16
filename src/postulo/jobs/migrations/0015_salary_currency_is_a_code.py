"""A salary's currency is a three-letter code, in capitals (#224).

The field took any three characters and the CSV importer wrote EUR onto everything it
read, so the stored codes are a mixture. Capitalising them is safe -- a code is the same
code in either case -- and it is what makes the new validator a question with one answer.
Anything that is not three letters is left exactly as it is: it is somebody's record of
what they were offered, and a migration that blanked it would lose that to tidy a column.
"""

from django.db import migrations, models
from django.db.models.functions import Upper

import postulo.jobs.models


def capitalise(apps, schema_editor) -> None:
    model = apps.get_model("jobs", "JobPosting")
    model.objects.exclude(salary_currency="").update(salary_currency=Upper("salary_currency"))


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0014_remove_companyidentifier_one_company_per_identifier_per_owner_and_more"),
    ]

    operations = [
        migrations.RunPython(capitalise, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jobposting",
            name="salary_currency",
            field=models.CharField(
                blank=True,
                default="EUR",
                help_text="A three-letter ISO 4217 code, such as EUR, GBP or USD.",
                max_length=3,
                validators=[postulo.jobs.models.currency_code],
                verbose_name="currency",
            ),
        ),
    ]
