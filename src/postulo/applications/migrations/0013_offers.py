"""Somewhere for what was actually offered to live (#237).

One additive table. *Offer* was a status and a timeline kind, and the only money in the
record was the posting's advertised range; nothing is carried across because nothing
was there to carry. Each row hangs off its application and may hold the reminder made
for its answer-by date, which is why the row keeps a nullable pointer to one.
"""

import django.db.models.deletion
import postulo.jobs.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0012_indexes_for_the_hot_subqueries'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Offer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('base_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='base pay')),
                ('currency', models.CharField(blank=True, help_text='A three-letter ISO 4217 code, such as EUR, GBP or USD.', max_length=3, validators=[postulo.jobs.models.currency_code], verbose_name='currency')),
                ('period', models.CharField(choices=[('year', 'Per year'), ('month', 'Per month'), ('day', 'Per day'), ('hour', 'Per hour')], default='year', max_length=10, verbose_name='period')),
                ('variable_pay', models.TextField(blank=True, help_text='A bonus, commission, or a target: in their words, since the terms vary.', verbose_name='variable pay')),
                ('equity', models.TextField(blank=True, verbose_name='equity')),
                ('benefits', models.TextField(blank=True, help_text='Pension, insurance, allowances, equipment: whatever was named.', verbose_name='benefits')),
                ('location', models.CharField(blank=True, help_text='The office, the remote arrangement, or the days of each.', max_length=200, verbose_name='where you would work')),
                ('holidays', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='days of holiday a year')),
                ('starts_on', models.DateField(blank=True, null=True, verbose_name='start date')),
                ('answer_by', models.DateField(blank=True, help_text='On the calendar, with a reminder the day before.', null=True, verbose_name='answer by')),
                ('notes', models.TextField(blank=True, verbose_name='notes')),
                ('application', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offers', to='applications.application', verbose_name='application')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to=settings.AUTH_USER_MODEL, verbose_name='owner')),
                ('reminder', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='offer', to='applications.reminder', verbose_name='reminder')),
            ],
            options={
                'verbose_name': 'offer',
                'verbose_name_plural': 'offers',
                'ordering': ('-created_at', '-pk'),
            },
        ),
    ]
