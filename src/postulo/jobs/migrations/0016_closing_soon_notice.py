"""The stamp that keeps a closing listing from being announced twice (#238).

The closing *date* rather than a moment. A stamp saying only "told them once" would
swallow the case that matters most: a deadline brought forward is news, and comparing
the stamp with the date it was written for is what notices that.

Nothing is backfilled. Every posting starts unannounced, so an instance upgrading with
listings already closing this week hears about them once -- which is the right answer,
since it has never heard about them at all.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0015_salary_currency_is_a_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobposting',
            name='closing_announced_for',
            field=models.DateField(blank=True, editable=False, null=True, verbose_name='closing announced for'),
        ),
    ]
