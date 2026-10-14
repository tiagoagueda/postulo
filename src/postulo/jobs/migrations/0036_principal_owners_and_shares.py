"""Companies, contacts and listings owned by a person or a group, and shared (#896)."""

import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


def give_group_rows_to_a_member(apps, schema_editor, models_named):
    """Before ``owner`` is required again: each group's rows go to its first member.

    A group with no member is refused rather than guessed at -- its records have nobody
    to go to, and that is a decision for the operator, on the conflicts page.
    """
    Group = apps.get_model("auth", "Group")
    for app_label, model_name in models_named:
        Model = apps.get_model(app_label, model_name)
        for group_id in (
            Model.objects.filter(owner__isnull=True)
            .values_list("owner_group", flat=True)
            .distinct()
        ):
            group = Group.objects.get(pk=group_id)
            member = group.user_set.order_by("pk").first()
            if member is None:
                raise migrations.exceptions.IrreversibleError(
                    f"The group {group.name!r} owns {Model._meta.verbose_name_plural} and has "
                    "no member to give them to."
                )
            Model.objects.filter(owner_group=group).update(owner=member, owner_group=None)


def back_to_people(apps, schema_editor):
    give_group_rows_to_a_member(
        apps,
        schema_editor,
        [
            ("jobs", "Company"),
            ("jobs", "CompanyName"),
            ("jobs", "CompanyIdentifier"),
            ("jobs", "Department"),
            ("jobs", "Contact"),
            ("jobs", "JobPosting"),
        ],
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("jobs", "0035_industry_section"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Share",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[("read", "Read-only"), ("write", "Read/write")],
                        max_length=5,
                        verbose_name="access",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="shared")),
            ],
            options={
                "verbose_name": "share",
                "verbose_name_plural": "shares",
                "ordering": ("created_at", "pk"),
            },
        ),
        migrations.RemoveConstraint(
            model_name="department",
            name="unique_department_name_per_company",
        ),
        migrations.AddField(
            model_name="company",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AddField(
            model_name="companyidentifier",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AddField(
            model_name="companyname",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AddField(
            model_name="contact",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AddField(
            model_name="department",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AddField(
            model_name="jobposting",
            name="owner_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(app_label)s_%(class)s_owned",
                to="auth.group",
                verbose_name="owning group",
            ),
        ),
        migrations.AlterField(
            model_name="company",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="companyidentifier",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="companyname",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="contact",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="department",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AlterField(
            model_name="jobposting",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(app_label)s_%(class)s_set",
                to=settings.AUTH_USER_MODEL,
                verbose_name="owner",
            ),
        ),
        migrations.AddIndex(
            model_name="jobposting",
            index=models.Index(fields=["owner_group", "url_key"], name="jobposting_group_url_key"),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="company_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.UniqueConstraint(
                fields=("owner_group", "name_key"), name="unique_company_name_per_group"
            ),
        ),
        migrations.AddConstraint(
            model_name="companyidentifier",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("value"),
                models.F("owner_group"),
                models.F("scheme"),
                condition=models.Q(("scheme", "other"), _negated=True),
                name="one_company_per_identifier_per_group",
            ),
        ),
        migrations.AddConstraint(
            model_name="companyidentifier",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="company_identifier_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="companyname",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="company_name_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="contact",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="contact_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="department_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.UniqueConstraint(
                fields=("company", "name_key"), name="unique_department_name_per_company"
            ),
        ),
        migrations.AddConstraint(
            model_name="jobposting",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="job_posting_one_owner",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shares",
                to="jobs.company",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shares",
                to="jobs.contact",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="granted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shares_given",
                to=settings.AUTH_USER_MODEL,
                verbose_name="shared by",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="grantee_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shares_received",
                to="auth.group",
                verbose_name="group",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="grantee_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shares_received",
                to=settings.AUTH_USER_MODEL,
                verbose_name="person",
            ),
        ),
        migrations.AddField(
            model_name="share",
            name="posting",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shares",
                to="jobs.jobposting",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("company__isnull", False),
                        ("contact__isnull", True),
                        ("posting__isnull", True),
                    ),
                    models.Q(
                        ("company__isnull", True),
                        ("contact__isnull", False),
                        ("posting__isnull", True),
                    ),
                    models.Q(
                        ("company__isnull", True),
                        ("contact__isnull", True),
                        ("posting__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="share_has_one_record",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("grantee_group__isnull", True), ("grantee_user__isnull", False)),
                    models.Q(("grantee_group__isnull", False), ("grantee_user__isnull", True)),
                    _connector="OR",
                ),
                name="share_has_one_grantee",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("company__isnull", False), ("grantee_user__isnull", False)),
                fields=("company", "grantee_user"),
                name="one_share_per_company_per_grantee_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("company__isnull", False), ("grantee_group__isnull", False)),
                fields=("company", "grantee_group"),
                name="one_share_per_company_per_grantee_group",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("contact__isnull", False), ("grantee_user__isnull", False)),
                fields=("contact", "grantee_user"),
                name="one_share_per_contact_per_grantee_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("contact__isnull", False), ("grantee_group__isnull", False)),
                fields=("contact", "grantee_group"),
                name="one_share_per_contact_per_grantee_group",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("grantee_user__isnull", False), ("posting__isnull", False)),
                fields=("posting", "grantee_user"),
                name="one_share_per_posting_per_grantee_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="share",
            constraint=models.UniqueConstraint(
                condition=models.Q(("grantee_group__isnull", False), ("posting__isnull", False)),
                fields=("posting", "grantee_group"),
                name="one_share_per_posting_per_grantee_group",
            ),
        ),
        # Last forwards, so first backwards: the rows have an owner before it is required.
        migrations.RunPython(migrations.RunPython.noop, back_to_people),
    ]
