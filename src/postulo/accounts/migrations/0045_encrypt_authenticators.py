"""Encrypt the authenticator secrets and recovery-code seeds already stored (#743)."""

from django.db import migrations


def encrypt_existing(apps, schema_editor):
    from postulo.plugins import secrets

    Authenticator = apps.get_model("mfa", "Authenticator")
    for authenticator in Authenticator.objects.all():
        data = dict(authenticator.data or {})
        changed = False
        for key in ("secret", "seed"):
            value = data.get(key)
            if isinstance(value, str) and value and not value.startswith(secrets.TEXT_PREFIX):
                data[key] = secrets.encrypt_text(value)
                changed = True
        if changed:
            authenticator.data = data
            authenticator.save(update_fields=["data"])


def decrypt_existing(apps, schema_editor):
    from postulo.plugins import secrets

    Authenticator = apps.get_model("mfa", "Authenticator")
    for authenticator in Authenticator.objects.all():
        data = dict(authenticator.data or {})
        changed = False
        for key in ("secret", "seed"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith(secrets.TEXT_PREFIX):
                data[key] = secrets.decrypt_text(value)
                changed = True
        if changed:
            authenticator.data = data
            authenticator.save(update_fields=["data"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0044_breadcrumb_on_hover"),
        ("mfa", "0003_authenticator_type_uniq"),
    ]

    operations = [migrations.RunPython(encrypt_existing, decrypt_existing)]
