"""Telephone numbers, addresses, web links and handles may be a group's (#896).

They copy the owner of what holds them, so on a group's company they are the group's.
"""

import django.db.models.deletion
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
            ("core", "PhoneNumber"),
            ("core", "PostalAddress"),
            ("core", "WebLink"),
            ("core", "MessagingHandle"),
        ],
    )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0036_address_on_the_map"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="messaginghandle",
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
            model_name="phonenumber",
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
            model_name="postaladdress",
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
            model_name="weblink",
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
            model_name="messaginghandle",
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
            model_name="phonenumber",
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
            model_name="postaladdress",
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
            model_name="weblink",
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
        migrations.AddConstraint(
            model_name="messaginghandle",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="messaging_handle_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="phonenumber",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="phone_number_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="postaladdress",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="postal_address_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="weblink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("owner__isnull", False), ("owner_group__isnull", True)),
                    models.Q(("owner__isnull", True), ("owner_group__isnull", False)),
                    _connector="OR",
                ),
                name="web_link_one_owner",
            ),
        ),
        # Last forwards, so first backwards: the rows have an owner before it is required.
        migrations.RunPython(migrations.RunPython.noop, back_to_people),
    ]
