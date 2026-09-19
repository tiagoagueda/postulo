"""Keep a fingerprint of an invitation's token, and not the token (#232).

The fingerprint of every existing token is computed here, so an invitation somebody is
holding still opens: the link they were sent is the same, and only what the database keeps
about it changes. The token column then goes.
"""

import hashlib

from django.db import migrations, models

import postulo.accounts.models


def fingerprint_the_tokens(apps, schema_editor):
    Invite = apps.get_model("accounts", "Invite")
    for invite in Invite.objects.all().iterator():
        invite.token_fingerprint = hashlib.sha256(invite.token.encode()).hexdigest()
        invite.save(update_fields=["token_fingerprint"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0020_profile_keyboard_shortcuts"),
    ]

    operations = [
        migrations.AddField(
            model_name="invite",
            name="token_fingerprint",
            field=models.CharField(default="", editable=False, max_length=64, verbose_name="token"),
        ),
        migrations.RunPython(fingerprint_the_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invite",
            name="token_fingerprint",
            field=models.CharField(
                default=postulo.accounts.models.fingerprint_of_a_fresh_token,
                editable=False,
                max_length=64,
                unique=True,
                verbose_name="token",
            ),
        ),
        migrations.RemoveField(
            model_name="invite",
            name="token",
        ),
    ]
