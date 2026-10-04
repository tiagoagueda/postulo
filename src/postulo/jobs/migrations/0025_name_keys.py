"""Hold a company's and a department's name to a case-folded key (#546).

The unique constraints compared the name exactly, so *Acme* beside *acme* was allowed and
only a Python-side `iexact` lookup, which on SQLite folds ASCII alone, kept it from
happening. They now compare `name_key`, filled here for the rows already there.

**Rows that already clash are not merged**: which one to keep, and what of the other to
carry over, is the person's to say. The migration stops and lists them; merge each pair with
the company's *Merge* page (or run `manage.py company_name_clashes` first to see them all)
and migrate again.
"""

from django.db import migrations, models

from postulo.core.slugs import name_key
from postulo.jobs import name_clashes


def fill(apps, schema_editor) -> None:
    company, department = apps.get_model("jobs", "Company"), apps.get_model("jobs", "Department")
    clashes = name_clashes.find(company, department)
    if clashes:
        raise RuntimeError(
            "Postulo cannot add its case-insensitive company-name rule: these records "
            "already share a name once capitals and spacing are ignored.\n  "
            + "\n  ".join(clashes)
            + "\nMerge each pair (a company's page has Merge; a department can be renamed "
            "or its contacts moved), then run the upgrade again. "
            "`manage.py company_name_clashes` lists them without changing anything."
        )
    for model in (company, department):
        batch = []
        for row in model.objects.only("pk", "name").iterator(chunk_size=500):
            row.name_key = name_key(row.name)
            batch.append(row)
            if len(batch) >= 500:
                model.objects.bulk_update(batch, ["name_key"])
                batch = []
        if batch:
            model.objects.bulk_update(batch, ["name_key"])


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0024_url_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='name_key',
            field=models.CharField(blank=True, editable=False, max_length=600),
        ),
        migrations.AddField(
            model_name='department',
            name='name_key',
            field=models.CharField(blank=True, editable=False, max_length=360),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='company', name='unique_company_name_per_owner'),
        migrations.RemoveConstraint(model_name='department', name='unique_department_name_per_company'),
        migrations.AddConstraint(
            model_name='company',
            constraint=models.UniqueConstraint(fields=('owner', 'name_key'), name='unique_company_name_per_owner'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('owner', 'company', 'name_key'), name='unique_department_name_per_company'),
        ),
    ]
