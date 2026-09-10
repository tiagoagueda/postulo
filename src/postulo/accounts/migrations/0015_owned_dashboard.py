"""Every account gets an arrangement of its own, and a record of what it has been offered.

`dashboard_widgets` was nullable, and the null meant *never arranged*: the page was computed
from the registry, so the arrangement belonged to nobody until somebody touched the setting.
A grid has to store what a widget was dragged into, which is why this comes first (#123).

**The null was load-bearing, and this is where the trade is made.** It carried the rule that
a widget added in a later release appears for somebody who never arranged anything. With
every account holding a list, nobody is ever "never arranged" — so the rule moves to
`dashboard_known`, the set of keys an account has already decided about. A key in neither
list is new *to that account*, whether it arrived in a release or with a plugin, and it
waits on the arrange page instead of walking onto the dashboard.

**Filling everybody in freezes today's standard arrangement for accounts that never touched
it**, which is exactly the group the null was protecting. That is the cost, and it is paid
here knowingly rather than discovered in 0.4.0 when a new widget reaches nobody: those
accounts get the seven defaults they were already seeing, plus a seen set that will announce
the eighth.

**The keys are written out rather than read from the registry.** A migration that imported
`core.widgets` would do something different the day a widget is renamed or dropped — it
records what was true when it was written, which is the only thing a migration can honestly
record.
"""

from django.db import migrations, models

#: What the dashboard showed by default when this was written, in that order.
STANDARD = [
    "suggestions",
    "counters",
    "gone_quiet",
    "upcoming_interviews",
    "due_reminders",
    "recent_activity",
    "shortcuts",
]

#: Every widget that existed. An account being filled in here has been offered all of them:
#: the seven above by having them, the rest by the picker that has always listed them.
EVERYTHING = [
    "shortcuts",
    "suggestions",
    "counters",
    "gone_quiet",
    "upcoming_interviews",
    "due_reminders",
    "recent_activity",
    "insight_counters",
    "funnel",
    "outcomes",
    "durations",
    "interviews_summary",
    "sources",
    "industries",
    "by_month",
    "listings",
    "selectivity",
]


def give_everybody_their_own(apps, schema_editor):
    """A null becomes this account's copy of the standard arrangement.

    An empty list is left alone: it means *deliberately cleared*, which is a decision
    somebody made and not an absence.
    """
    Profile = apps.get_model("accounts", "Profile")
    Profile.objects.filter(dashboard_widgets__isnull=True).update(dashboard_widgets=STANDARD)
    Profile.objects.update(dashboard_known=EVERYTHING)


def take_it_back(apps, schema_editor):
    """And the other way: an arrangement identical to the standard one becomes a null again.

    Only an identical one. Anything somebody actually arranged is theirs and stays.
    """
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.all().iterator():
        if profile.dashboard_widgets == STANDARD:
            profile.dashboard_widgets = None
            profile.save(update_fields=["dashboard_widgets"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_identifier_schemes"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="dashboard_known",
            field=models.JSONField(
                blank=True, default=list, verbose_name="widgets already offered"
            ),
        ),
        migrations.RunPython(give_everybody_their_own, take_it_back),
        # Last, so the loop above still sees the nulls it is there to replace.
        migrations.AlterField(
            model_name="profile",
            name="dashboard_widgets",
            field=models.JSONField(blank=True, default=list, verbose_name="dashboard widgets"),
        ),
    ]
