"""Experience, education and certifications take the order number like every other
section, seeded once from the dates that decided their order until now (#203).

Sorted by date first, the arrows on the overview changed nothing anybody could see in
those three sections unless two entries shared a date. From here the person owns the
order everywhere, and a new entry lands where its date would have put it. The seed is
the old ordering exactly -- ongoing first, then the newest date, then the old number,
then the pk -- so nothing moves on the day of the upgrade.
"""

import datetime as dt

from django.db import migrations

DATED = (("Experience", "start_date"), ("Education", "end_date"), ("Certification", "issued_on"))


def seed_from_dates(apps, schema_editor):
    for model_name, date_field in DATED:
        model = apps.get_model("resume", model_name)
        by_owner: dict[int, list] = {}
        for row in model.objects.order_by("order", "pk"):
            by_owner.setdefault(row.owner_id, []).append(row)
        for rows in by_owner.values():
            rows.sort(
                key=lambda row: (
                    getattr(row, date_field) is None,
                    getattr(row, date_field) or dt.date.min,
                ),
                reverse=True,
            )
            for position, row in enumerate(rows):
                if row.order != position:
                    row.order = position
                    row.save(update_fields=["order"])


class Migration(migrations.Migration):
    dependencies = [
        ("resume", "0004_translations"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="experience",
            options={
                "ordering": ("order", "pk"),
                "verbose_name": "experience",
                "verbose_name_plural": "experience",
            },
        ),
        migrations.AlterModelOptions(
            name="education",
            options={
                "ordering": ("order", "pk"),
                "verbose_name": "education",
                "verbose_name_plural": "education",
            },
        ),
        migrations.AlterModelOptions(
            name="certification",
            options={
                "ordering": ("order", "pk"),
                "verbose_name": "certification",
                "verbose_name_plural": "certifications",
            },
        ),
        migrations.RunPython(seed_from_dates, migrations.RunPython.noop),
    ]
