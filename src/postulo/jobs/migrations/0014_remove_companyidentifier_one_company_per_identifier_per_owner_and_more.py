"""Identifiers are compared without regard to letter case (#211).

The data step runs **before** the constraints, and has to: rows written before a scheme
folded its values can already collide case-insensitively -- a lowercase `q95` beside `Q95` --
and a unique constraint on `Lower(value)` cannot be created over them.

So each value is put through the current rules first. Where that leaves two rows of one
account holding the same identifier under one scheme, the later row's **identifier** is
removed and the pair is named on the console. Nothing else goes: both companies stay exactly
where they are, because two records of one employer is something only the person can settle,
and merging them here would be this migration deciding it for them.

The rules are imported from the running code rather than frozen into this file. They live in
a plugin (`postulo.plugins.identifiers`), and a copy pinned here would be a second table of
schemes to keep current -- which is the thing the registry exists to avoid.
"""

import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


def fold_and_report(apps, schema_editor) -> None:
    from django.core.exceptions import ValidationError

    from postulo.jobs import identifiers as rules

    model = apps.get_model("jobs", "CompanyIdentifier")
    seen: dict[tuple[int, str, str], str] = {}
    removed = 0

    for row in model.objects.select_related("company").order_by("pk").iterator():
        value = row.value
        if row.scheme != "other":
            try:
                value = rules.clean(row.scheme, row.value)
            except ValidationError:
                # A value the rules no longer accept is left exactly as it is: it is somebody's
                # data, and this migration is about case, not about tidying.
                value = row.value
            if value != row.value:
                row.value = value
                row.save(update_fields=["value"])

        if row.scheme == "other":
            continue
        key = (row.owner_id, row.scheme, value.casefold())
        if key in seen:
            print(
                f"  identifier {row.scheme} {value!r} is on both {seen[key]!r} and "
                f"{row.company.name!r}; removed it from the second. If they are one employer, "
                f"merge them and add the identifier back."
            )
            row.delete()
            removed += 1
            continue
        seen[key] = row.company.name

    if removed:
        print(f"  {removed} duplicate identifier(s) removed; the companies were left alone.")


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0013_company_kind"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(fold_and_report, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="companyidentifier",
            name="one_company_per_identifier_per_owner",
        ),
        migrations.RemoveConstraint(
            model_name="companyidentifier",
            name="unique_other_identifier_per_company",
        ),
        migrations.AddConstraint(
            model_name="companyidentifier",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("value"),
                models.F("owner"),
                models.F("scheme"),
                condition=models.Q(("scheme", "other"), _negated=True),
                name="one_company_per_identifier_per_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="companyidentifier",
            constraint=models.UniqueConstraint(
                models.F("company"),
                models.F("scheme"),
                django.db.models.functions.text.Lower("label"),
                django.db.models.functions.text.Lower("value"),
                name="unique_other_identifier_per_company",
                violation_error_message="This identifier is already listed.",
            ),
        ),
        # Only the wording a form shows changes here; the table is untouched.
        migrations.AlterConstraint(
            model_name="companyidentifier",
            name="one_identifier_per_scheme_per_company",
            constraint=models.UniqueConstraint(
                condition=models.Q(("scheme", "other"), _negated=True),
                fields=("company", "scheme"),
                name="one_identifier_per_scheme_per_company",
                violation_error_message="This kind of identifier is already listed.",
            ),
        ),
    ]
