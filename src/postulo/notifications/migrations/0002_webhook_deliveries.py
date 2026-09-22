"""Somewhere for a webhook delivery to wait (#240).

One additive table. A notification bound for a webhook is a row here first and a request
later, made by the scheduler with backoff, so that nothing is posted from inside the
request that caused the event and a receiver down for an afternoon gets everything when
it comes back. Nothing to carry across: there were no webhooks before this.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
        ('plugins', '0006_connection_outbox_kind'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WebhookDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('event', models.CharField(max_length=40, verbose_name='event')),
                ('key', models.CharField(blank=True, db_index=True, max_length=200, verbose_name='key')),
                ('body', models.TextField(verbose_name='body')),
                ('status', models.CharField(choices=[('pending', 'Waiting'), ('sent', 'Sent'), ('failed', 'Failed, will retry'), ('given_up', 'Given up')], default='pending', max_length=12, verbose_name='status')),
                ('attempts', models.PositiveSmallIntegerField(default=0, verbose_name='attempts')),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='next attempt')),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True, verbose_name='last attempt')),
                ('last_status', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='last answer')),
                ('last_error', models.CharField(blank=True, max_length=500, verbose_name='last error')),
                ('sent_at', models.DateTimeField(blank=True, null=True, verbose_name='sent at')),
                ('connection', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='webhook_deliveries', to='plugins.connection', verbose_name='connection')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to=settings.AUTH_USER_MODEL, verbose_name='owner')),
            ],
            options={
                'verbose_name': 'webhook delivery',
                'verbose_name_plural': 'webhook deliveries',
                'ordering': ('-created_at', '-pk'),
            },
        ),
    ]
