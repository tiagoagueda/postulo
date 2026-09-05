"""Give every account a username, and trust the addresses that were already in use.

Two things change for existing accounts. They gain a username derived from their email
address, since the field is obligatory from here on and nobody has chosen one yet. And
their email address is recorded as verified: verification was optional until now, so
these people signed in by that address for as long as the instance has existed, and
demanding a click on a link before their next sign-in would lock out everyone at once.
"""

from django.db import migrations, models

import postulo.accounts.validators


def give_usernames_and_trust_addresses(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    EmailAddress = apps.get_model("account", "EmailAddress")
    from postulo.accounts.models import unique_username
    from postulo.accounts.validators import slug_from_email

    for user in User.objects.order_by("pk"):
        if not user.username:
            user.username = unique_username(
                slug_from_email(user.email),
                lambda name: User.objects.filter(username=name).exists(),
            )
            user.save(update_fields=["username"])
        address, _created = EmailAddress.objects.get_or_create(
            user=user, email__iexact=user.email, defaults={"email": user.email}
        )
        if not address.verified or not address.primary:
            EmailAddress.objects.filter(user=user).exclude(pk=address.pk).update(primary=False)
            address.verified = True
            address.primary = True
            address.save(update_fields=["verified", "primary"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_invite_profile"),
        ("account", "0009_emailaddress_unique_primary_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username",
            field=models.CharField(max_length=32, null=True, verbose_name="username"),
        ),
        migrations.RunPython(give_usernames_and_trust_addresses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                error_messages={"unique": "Somebody already has that username."},
                help_text="Lowercase letters, digits, dots, underscores and hyphens.",
                max_length=32,
                unique=True,
                validators=[postulo.accounts.validators.username_validator],
                verbose_name="username",
            ),
        ),
    ]
