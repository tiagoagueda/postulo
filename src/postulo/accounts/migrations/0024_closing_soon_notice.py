"""How much warning somebody wants before a listing closes (#238).

Three days by default, which is the number the announcement uses when nobody has
chosen: long enough to write something and sleep on it, short enough that the message
is still about this week. Beside `quiet_after_days`, which is the same kind of
preference -- how patient this person is -- and lives in the same place.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_profile_density'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='closing_notice_days',
            field=models.PositiveSmallIntegerField(default=3, help_text='Days of warning before a listing you have not decided about closes.', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(90)], verbose_name='notice before a listing closes'),
        ),
    ]
