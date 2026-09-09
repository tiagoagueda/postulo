"""A verified state on a telephone number, and deliberately nothing else (#142).

There is no data migration here and that absence is the point. Numbers have been storable
since #90 and are first-come-first-served, so every row that exists today is a claim nobody
checked. Granting them verification would hand a way back into an account to whoever typed a
stranger's number a month ago, before there was any reason to think it would ever matter.

Every row starts NULL. Verification is earned, however much churn that causes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_mail_security'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonenumber',
            name='verified_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='verified'),
        ),
    ]
