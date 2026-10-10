import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def date_the_claims_there_already_are(apps, schema_editor):
    """Give every unverified, non-primary address a date: today.

    allauth never recorded when one was added, so the true age is unknowable. Dating them
    now means each is given the full lifetime of a confirmation link before it expires,
    which is the generous reading (#891).
    """
    EmailAddress = apps.get_model("account", "EmailAddress")
    AddressClaim = apps.get_model("accounts", "AddressClaim")
    unproved = EmailAddress.objects.filter(verified=False, primary=False)
    AddressClaim.objects.bulk_create(
        [AddressClaim(address_id=pk) for pk in unproved.values_list("pk", flat=True)]
    )


class Migration(migrations.Migration):
    dependencies = [
        ("account", "0009_emailaddress_unique_primary_email"),
        ("accounts", "0037_cv_photo"),
    ]

    operations = [
        migrations.CreateModel(
            name="AddressClaim",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "claimed_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "address",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="claim",
                        to="account.emailaddress",
                    ),
                ),
            ],
            options={
                "verbose_name": "address claim",
                "verbose_name_plural": "address claims",
            },
        ),
        migrations.RunPython(date_the_claims_there_already_are, migrations.RunPython.noop),
    ]
