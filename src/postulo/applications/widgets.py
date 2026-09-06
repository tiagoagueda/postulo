"""The dashboard widgets that come out of applications, interviews and the event log.

Two kinds sit here, and the split is what #44 was about. **What needs doing** — gone quiet,
interviews coming up, reminders due — is the dashboard Postulo always had. **What the
record adds up to** — the funnel, the response rate, how long things take — was a separate
page called Insights, answering the same question at a different distance. They are the
same page now, and which of them somebody sees is theirs to choose.

Everything read here comes from the event log rather than from current statuses. An
application that reached an interview and was then rejected has a current status of
"rejected"; counting only current statuses would say the search reached no interviews.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils.translation import gettext_lazy as _

from postulo.core.widgets import Sources, Widget, register

#: How far back "recent" reaches.
RECENT_DAYS = 30

TODAY = _("What needs doing")
RECORD = _("What the record says")


# ------------------------------------------------------------ what needs doing


def _suggestions(sources: Sources) -> dict:
    from .models import Suggestion

    return {"suggestion_count": Suggestion.objects.for_user(sources.user).pending().count()}


def _counters(sources: Sources) -> dict:
    from .models import Status

    applications = sources.applications
    return {
        "open_count": applications.open().count(),
        "interviewing_count": applications.filter(
            status__in=[Status.SCREENING, Status.INTERVIEWING, Status.ASSESSMENT]
        ).count(),
        "offer_count": applications.filter(status=Status.OFFER).count(),
        "applied_recently": applications.filter(
            applied_at__gte=sources.now - timedelta(days=RECENT_DAYS)
        ).count(),
    }


def _gone_quiet(sources: Sources) -> dict:
    return {
        "quiet_applications": sources.quiet[:10],
        "quiet_count": sources.quiet.count(),
        "quiet_after_days": sources.quiet_after_days,
    }


def _upcoming_interviews(sources: Sources) -> dict:
    from .models import Interview

    interviews = Interview.objects.for_user(sources.user)
    return {
        "upcoming_interviews": interviews.upcoming().with_display_data()[:5],
        "interviews_awaiting_outcome": interviews.awaiting_outcome().count(),
    }


def _due_reminders(sources: Sources) -> dict:
    from .models import Reminder

    return {
        "due_reminders": (
            Reminder.objects.for_user(sources.user)
            .due()
            .select_related("application", "application__posting")[:10]
        )
    }


def _recent_activity(sources: Sources) -> dict:
    from .models import ApplicationEvent

    return {
        "recent_events": ApplicationEvent.objects.for_user(sources.user).select_related(
            "application", "application__posting", "application__posting__company"
        )[:10]
    }


# --------------------------------------------------------- what the record says


def _insight(sources: Sources) -> dict:
    """Every figure widget gets the whole thing; the pass over the log happens once."""
    return {"insights": sources.insights}


# ------------------------------------------------------------------ registered


register(
    Widget(
        key="suggestions",
        label="",
        blurb=_("A line when a plugin thinks something happened and is waiting for you."),
        template="widgets/suggestions.html",
        context=_suggestions,
        width="full",
        group=TODAY,
        default_order=10,
    )
)

register(
    Widget(
        key="counters",
        label=_("Where things stand"),
        blurb=_("Still live, in conversation, offers, and what you sent in the last month."),
        template="widgets/counters.html",
        context=_counters,
        width="full",
        group=TODAY,
        default_order=20,
    )
)

register(
    Widget(
        key="gone_quiet",
        label=_("Gone quiet"),
        blurb=_("Open applications with nothing heard, and the three things to do about it."),
        template="widgets/gone_quiet.html",
        context=_gone_quiet,
        width="half",
        group=TODAY,
        default_order=30,
    )
)

register(
    Widget(
        key="upcoming_interviews",
        label=_("Coming up"),
        blurb=_("The next interviews in the diary, and any that passed without an outcome."),
        template="widgets/upcoming_interviews.html",
        context=_upcoming_interviews,
        width="half",
        group=TODAY,
        default_order=40,
    )
)

register(
    Widget(
        key="due_reminders",
        label=_("Due now"),
        blurb=_("Reminders that have come due, each with a button to mark it done."),
        template="widgets/due_reminders.html",
        context=_due_reminders,
        width="half",
        group=TODAY,
        default_order=50,
    )
)

register(
    Widget(
        key="recent_activity",
        label=_("Recent activity"),
        blurb=_("The last ten things the timeline recorded, whichever application they were on."),
        template="widgets/recent_activity.html",
        context=_recent_activity,
        width="half",
        group=TODAY,
        default_order=60,
    )
)

register(
    Widget(
        key="insight_counters",
        label=_("The search in four numbers"),
        blurb=_("Applications sent, how many got any reply, how long a reply takes, offers."),
        template="widgets/insight_counters.html",
        context=_insight,
        width="full",
        group=RECORD,
    )
)

register(
    Widget(
        key="funnel",
        label=_("How far things got"),
        blurb=_("How many applications ever reached each stage, read from the timeline."),
        template="widgets/funnel.html",
        context=_insight,
        width="half",
        group=RECORD,
    )
)

register(
    Widget(
        key="outcomes",
        label=_("Outcomes"),
        blurb=_("Replied, rejected, went silent, still waiting — what became of what you sent."),
        template="widgets/outcomes.html",
        context=_insight,
        width="half",
        group=RECORD,
    )
)

register(
    Widget(
        key="durations",
        label=_("How long they take"),
        blurb=_("Days to a first reply and to a first interview: fastest, median, slowest."),
        template="widgets/durations.html",
        context=_insight,
        width="half",
        group=RECORD,
    )
)

register(
    Widget(
        key="interviews_summary",
        label=_("Interviews"),
        blurb=_("How many applications reached one, how many were held, and of what kind."),
        template="widgets/interviews_summary.html",
        context=_insight,
        width="half",
        group=RECORD,
    )
)

register(
    Widget(
        key="sources",
        label=_("Where they came from"),
        blurb=_("Applications, replies and offers per company, with the ones gone quiet."),
        template="widgets/sources.html",
        context=_insight,
        width="full",
        group=RECORD,
    )
)

register(
    Widget(
        key="industries",
        label=_("By industry"),
        blurb=_("The same figures by field. A company in three fields counts in all three."),
        template="widgets/industries.html",
        context=_insight,
        width="full",
        group=RECORD,
    )
)

register(
    Widget(
        key="by_month",
        label=_("Applications by month"),
        blurb=_("How much you sent, month by month, so a quiet stretch is visible."),
        template="widgets/by_month.html",
        context=_insight,
        width="half",
        group=RECORD,
    )
)
