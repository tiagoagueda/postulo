"""Give a web link the service it is on, and work it out for the rows already stored (#305).

The column first, blank on every row, and blank means *Other*: a row nobody has looked at
is exactly what it was. Then each stored row is read the way a newly pasted address is
(``core.link_services.settle`` with nothing chosen):

- an address on a host a service of its kind answers on, with the shape that service's
  addresses have, becomes that service's -- ``https://www.linkedin.com/in/alex`` under
  *Social profiles* is LinkedIn;
- everything else stays *Other*: a host no service knows, an address on a known host that
  does not have the shape (LinkedIn's front page), a kind with no services. **Nothing is
  refused and no address is touched**;
- a row somebody *named* stays *Other* with its name, because a named service has no name
  of its own and the name is theirs -- unless the name says only what the service is
  ("LinkedIn" on a LinkedIn address), which the service now says, and is dropped.

**Here and at once, rather than on first read**, because blank has to go on meaning
*Other*. Worked out lazily, a blank row would be either "Other" or "nobody has looked
yet", and telling them apart needs a third state on every row for the rest of its life.
Done here, the page, the API and the archive agree from the first request.

**It asks the registry as it is on the day it runs**, which is the same code the form and
the importer ask, so there is one rule and not a copy of it frozen in a migration; a
service a package installed beside Postulo offers is counted too. The cost is that a
later run cannot guess more than that day's list knew, and it need not: a row left as
*Other* loses nothing, and its owner can choose.

Going back drops the column. A name that said only the service's name is not put back, and
the row is then shown by its host, as a row with no name always was.
"""

from django.db import migrations, models


def guess_services(apps, schema_editor):
    from django.core.exceptions import ValidationError

    from postulo.core import link_services

    WebLink = apps.get_model("core", "WebLink")
    changed = []
    for row in WebLink.objects.filter(service="").order_by("pk").iterator():
        try:
            service, label = link_services.settle(row.kind, "", row.url, row.label)
        except ValidationError:
            # A row that is refused is a row left as it is. Only that: anything else is
            # the code this reads having moved, and caught here it would leave every row
            # *Other* and the migration recorded as run, with nothing to say so.
            continue
        if service:
            row.service, row.label = service, label
            changed.append(row)
    # Written once the reading is over: a table is not changed under the cursor reading it.
    WebLink.objects.bulk_update(changed, ["service", "label"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_a_language_is_a_tag"),
    ]

    operations = [
        migrations.AddField(
            model_name="weblink",
            name="service",
            field=models.CharField(blank=True, max_length=40, verbose_name="service"),
        ),
        migrations.AlterField(
            model_name="weblink",
            name="label",
            field=models.CharField(
                blank=True,
                help_text="A blog, a portfolio — what to call it. Left blank, the host is shown.",
                max_length=60,
                verbose_name="name",
            ),
        ),
        migrations.RunPython(guess_services, migrations.RunPython.noop),
    ]
