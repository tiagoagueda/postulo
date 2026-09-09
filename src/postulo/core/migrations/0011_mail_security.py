"""One connection-security choice in place of the STARTTLS boolean (#158).

The order is the point. Django's autodetector wanted to drop the old column and add the new
one, which is correct as a schema change and loses every administrator's setting: an instance
running STARTTLS on 587 would come back saying nothing was chosen, fall through to the
environment, and start deciding by a variable most people have never set.

So: add, carry, remove. The reverse carries it back, because `true`/`false` can hold two of
the three states and an instance that has chosen implicit TLS cannot be described by the old
column at all — that one becomes STARTTLS on the way down, which is wrong in the same way the
old field was wrong, and is the closest thing to true that the column can hold.
"""

from django.db import migrations, models


def carry_forward(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    # NULL kept its meaning: nothing chosen here, so the environment answers.
    SiteSettings.objects.filter(email_use_tls=True).update(email_security="starttls")
    SiteSettings.objects.filter(email_use_tls=False).update(email_security="none")


def carry_back(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(email_security="none").update(email_use_tls=False)
    SiteSettings.objects.filter(email_security__in=("starttls", "ssl")).update(email_use_tls=True)
    SiteSettings.objects.filter(email_security="").update(email_use_tls=None)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_mail_health"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="email_security",
            field=models.CharField(
                blank=True,
                choices=[
                    ("none", "None"),
                    ("starttls", "STARTTLS, after connecting"),
                    ("ssl", "TLS from the first byte"),
                ],
                max_length=10,
                verbose_name="connection security",
            ),
        ),
        migrations.RunPython(carry_forward, carry_back),
        migrations.RemoveField(
            model_name="sitesettings",
            name="email_use_tls",
        ),
    ]
