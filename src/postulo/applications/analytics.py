"""What the record can tell you about your own job search.

Everything here is computed from the event log rather than from current statuses, and
that distinction is the whole point. An application that reached an interview and was
then rejected has a current status of "rejected"; counting only current statuses would
say you have had no interviews. The log remembers that you did.

The figures are deliberately plain. A job search produces small numbers — dozens, not
thousands — and at that size a median is honest where a mean is not, a percentage of
eleven things needs saying out loud, and anything more elaborate would be decoration
implying a confidence the sample does not support.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from django.db.models import Count, Min, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.jobs.models import JobPosting, ListingState

from .models import Application, ApplicationEvent, Status

#: The stages a funnel counts, in order. Each is "reached this, ever".
FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    (Status.APPLIED, _("Applied")),
    (Status.ACKNOWLEDGED, _("Acknowledged")),
    (Status.SCREENING, _("Screening")),
    (Status.INTERVIEWING, _("Interviewing")),
    (Status.ASSESSMENT, _("Assessment")),
    (Status.OFFER, _("Offer")),
)

#: Reaching any of these means somebody at the company replied to you.
RESPONSE_STATUSES = frozenset(
    {
        Status.ACKNOWLEDGED,
        Status.SCREENING,
        Status.INTERVIEWING,
        Status.ASSESSMENT,
        Status.OFFER,
        Status.ACCEPTED,
        Status.REJECTED,
    }
)

#: Below this, a percentage says more about the sample than about the search.
MEANINGFUL_SAMPLE = 5


@dataclass
class Stage:
    status: str
    label: str
    count: int
    #: Share of everything that was ever applied for, as a percentage.
    share: float = 0.0


@dataclass
class SourceRow:
    name: str
    applied: int = 0
    responded: int = 0
    interviewed: int = 0
    offers: int = 0

    @property
    def response_rate(self) -> float | None:
        return 100 * self.responded / self.applied if self.applied else None


@dataclass
class Insights:
    total: int = 0
    applied: int = 0
    open_now: int = 0
    funnel: list[Stage] = field(default_factory=list)
    responded: int = 0
    ghosted: int = 0
    rejected: int = 0
    offers: int = 0
    median_days_to_reply: float | None = None
    fastest_reply_days: int | None = None
    slowest_reply_days: int | None = None
    still_waiting: int = 0
    sources: list[SourceRow] = field(default_factory=list)
    by_month: list[tuple[str, int]] = field(default_factory=list)
    #: The stage before applications: how many listings were noticed, and what became of them.
    listings_noted: int = 0
    listings_applied: int = 0
    listings_discarded: int = 0

    @property
    def selectivity(self) -> float | None:
        """The share of noticed listings that turned into an application."""
        return 100 * self.listings_applied / self.listings_noted if self.listings_noted else None

    @property
    def response_rate(self) -> float | None:
        """The share of applications that got any reply at all."""
        return 100 * self.responded / self.applied if self.applied else None

    @property
    def sample_is_small(self) -> bool:
        """Whether the numbers are too few to read anything into."""
        return self.applied < MEANINGFUL_SAMPLE


def _statuses_ever_reached(applications) -> dict[int, set[str]]:
    """For each application, every status it has ever held.

    Read from the log, so an application that was interviewing before it was rejected
    still counts as having reached an interview — which is the only reading that answers
    "how far do my applications usually get?".
    """
    reached: dict[int, set[str]] = {
        application.pk: {application.status} for application in applications
    }
    events = ApplicationEvent.objects.filter(
        application__in=applications, to_status__gt=""
    ).values_list("application_id", "to_status")
    for application_id, status in events:
        reached.setdefault(application_id, set()).add(status)
    return reached


def _first_reply_days(applications) -> dict[int, int]:
    """Days from applying to the first sign of life, per application."""
    applied_at = {
        application.pk: application.applied_at
        for application in applications
        if application.applied_at is not None
    }
    if not applied_at:
        return {}

    first_response = (
        ApplicationEvent.objects.filter(
            application_id__in=applied_at,
            to_status__in=list(RESPONSE_STATUSES),
        )
        .values("application_id")
        .annotate(first=Min("occurred_at"))
    )

    days: dict[int, int] = {}
    for row in first_response:
        when, sent = row["first"], applied_at[row["application_id"]]
        if when >= sent:
            days[row["application_id"]] = (when - sent).days
    return days


def build(user) -> Insights:
    """Work out what the record says about ``user``'s search."""
    applications = list(
        Application.objects.for_user(user).select_related("posting", "posting__company")
    )
    insights = Insights(total=len(applications))

    listings = JobPosting.objects.for_user(user)
    insights.listings_noted = listings.count()
    insights.listings_applied = listings.in_state("applied").count()
    insights.listings_discarded = listings.in_state(ListingState.DISCARDED).count()

    if not applications:
        return insights

    reached = _statuses_ever_reached(applications)
    ever_applied = [a for a in applications if Status.APPLIED in reached[a.pk] or a.applied_at]
    insights.applied = len(ever_applied)
    insights.open_now = sum(1 for a in applications if a.is_open)

    # ------------------------------------------------------------------ funnel
    for status, label in FUNNEL_STAGES:
        count = sum(1 for a in applications if status in reached[a.pk])
        insights.funnel.append(
            Stage(
                status=status,
                label=str(label),
                count=count,
                share=100 * count / insights.applied if insights.applied else 0.0,
            )
        )

    # --------------------------------------------------------------- outcomes
    insights.responded = sum(1 for a in ever_applied if reached[a.pk] & RESPONSE_STATUSES)
    insights.ghosted = sum(1 for a in applications if a.status == Status.GHOSTED)
    insights.rejected = sum(1 for a in applications if a.status == Status.REJECTED)
    insights.offers = sum(1 for a in applications if Status.OFFER in reached[a.pk])

    # ------------------------------------------------------------ reply times
    reply_days = _first_reply_days(ever_applied)
    if reply_days:
        values = sorted(reply_days.values())
        insights.median_days_to_reply = statistics.median(values)
        insights.fastest_reply_days = values[0]
        insights.slowest_reply_days = values[-1]

    now = timezone.now()
    insights.still_waiting = sum(
        1
        for a in ever_applied
        if a.pk not in reply_days
        and a.applied_at is not None
        and a.status != Status.GHOSTED
        and (now - a.applied_at).days >= 0
    )

    # --------------------------------------------------------------- sources
    rows: dict[str, SourceRow] = {}
    for application in ever_applied:
        name = (application.posting.source or "").strip() or str(_("Not recorded"))
        row = rows.setdefault(name, SourceRow(name=name))
        row.applied += 1
        statuses = reached[application.pk]
        if statuses & RESPONSE_STATUSES:
            row.responded += 1
        if statuses & {Status.INTERVIEWING, Status.SCREENING, Status.ASSESSMENT}:
            row.interviewed += 1
        if Status.OFFER in statuses:
            row.offers += 1
    insights.sources = sorted(rows.values(), key=lambda row: (-row.applied, row.name))

    # ----------------------------------------------------------- over time
    per_month = (
        Application.objects.for_user(user)
        .filter(applied_at__isnull=False)
        .annotate(month=TruncMonth("applied_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    insights.by_month = [
        (row["month"].strftime("%Y-%m"), row["count"]) for row in per_month if row["month"]
    ]

    return insights


def applications_needing_a_nudge(user, *, after_days: int = 14) -> list[Application]:
    """Applications that were sent, never answered, and are getting old."""
    cutoff = timezone.now() - dt.timedelta(days=after_days)
    candidates = (
        Application.objects.for_user(user)
        .filter(applied_at__lte=cutoff, status__in=[Status.APPLIED, Status.ACKNOWLEDGED])
        .with_display_data()
    )
    return list(candidates)


def has_enough_history(user) -> bool:
    """Whether there is enough recorded to be worth showing figures at all."""
    return (
        Application.objects.for_user(user).aggregate(
            sent=Count("id", filter=Q(applied_at__isnull=False))
        )["sent"]
        > 0
    )
