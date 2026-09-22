"""Three composite indexes for the three subqueries every list runs (#231).

Additive and reversible: an index is not data, and dropping these leaves every row
exactly as it was. None of them changes an answer -- they change how long the database
takes to give it, on the three questions asked once per row on the busiest pages: the
latest timeline entry, the next outstanding reminder, the next unsettled interview.

Each is the whole of what its subquery filters and orders by, in that order, so the
database can walk it and stop rather than find the rows and then sort them.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0011_backfill_applied_at'),
        ('jobs', '0016_closing_soon_notice'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name='applicationevent',
            index=models.Index(fields=['application', '-occurred_at'], name='event_latest_per_app'),
        ),
        migrations.AddIndex(
            model_name='interview',
            index=models.Index(fields=['application', 'outcome', 'starts_at'], name='interview_next_per_app'),
        ),
        migrations.AddIndex(
            model_name='reminder',
            index=models.Index(fields=['application', 'done_at', 'due_at'], name='reminder_next_per_app'),
        ),
    ]
