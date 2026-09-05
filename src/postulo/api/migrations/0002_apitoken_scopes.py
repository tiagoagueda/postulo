"""Capture tokens become API tokens with scopes.

Every existing token was a capture token, so every existing token gets exactly the
``captures`` scope: nothing anyone has installed stops working, and nothing gains a
power it did not have.
"""

from django.db import migrations, models


def existing_tokens_keep_capturing(apps, schema_editor):
    ApiToken = apps.get_model("api", "ApiToken")
    for token in ApiToken.objects.all().iterator():
        if not token.scopes:
            token.scopes = ["captures"]
            token.save(update_fields=["scopes"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="CaptureToken", new_name="ApiToken"),
        migrations.AlterModelOptions(
            name="apitoken",
            options={
                "ordering": ("-created_at",),
                "verbose_name": "API token",
                "verbose_name_plural": "API tokens",
            },
        ),
        migrations.AddField(
            model_name="apitoken",
            name="scopes",
            field=models.JSONField(blank=True, default=list, verbose_name="scopes"),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="expires"),
        ),
        migrations.RunPython(existing_tokens_keep_capturing, migrations.RunPython.noop),
    ]
