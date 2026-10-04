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
import hashlib
import statistics
from dataclasses import dataclass, field

from django.db.models import Count, F, Min, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.jobs.models import JobPosting, ListingState

from . import endings
from .models import (
    BOARD_STATUSES,
    QUIET_STATUSES,
    Application,
    ApplicationEvent,
    EndReason,
    EventKind,
    Interview,
    InterviewOutcome,
    Status,
)

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
    #: Open right now and gone quiet — the ones the response rate has not yet counted.
    quiet: int = 0

    @property
    def response_rate(self) -> float | None:
        return 100 * self.responded / self.applied if self.applied else None

    @property
    def quiet_rate(self) -> float | None:
        return 100 * self.quiet / self.applied if self.applied else None


@dataclass
class EndingRow:
    """One line of *Where and why applications end*: a stage or a reason, and how many
    ended there, told apart by how -- turned down, walked away from, or never answered."""

    #: The stage or the reason, as stored; empty for the line that gathers the unrecorded.
    key: str
    label: str
    rejected: int = 0
    withdrawn: int = 0
    ghosted: int = 0

    @property
    def total(self) -> int:
        return self.rejected + self.withdrawn + self.ghosted


@dataclass
class Endings:
    """Where applications ended and why, read from the timeline (#239)."""

    total: int = 0
    #: By the stage each had reached when it ended, in the order of the board.
    stages: list[EndingRow] = field(default_factory=list)
    #: By the reason given, commonest first, with the ones nobody gave a reason for last.
    reasons: list[EndingRow] = field(default_factory=list)
    #: How many of them carry a reason at all. Most endings come with none, and a table
    #: of reasons that did not say so would read as if it covered every one.
    explained: int = 0


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
    #: Interviews: how long the first one took to come, and what kinds there were.
    median_days_to_interview: float | None = None
    interviewed: int = 0
    interviews_held: int = 0
    interviews_ahead: int = 0
    interview_kinds: list[tuple[str, int]] = field(default_factory=list)
    sources: list[SourceRow] = field(default_factory=list)
    #: The same figures by who referred the person and by the agency it went through
    #: (#239). Only the applications that name one: most name neither, and a line for
    #: those would be the table above it, printed again.
    by_referrer: list[SourceRow] = field(default_factory=list)
    by_agency: list[SourceRow] = field(default_factory=list)
    #: Where applications ended, and why (#239).
    endings: Endings = field(default_factory=Endings)
    #: The same figures by the company's industries. A company in three fields counts in
    #: all three, which is the honest reading.
    industries: list[SourceRow] = field(default_factory=list)
    by_month: list[tuple[dt.date, int]] = field(default_factory=list)
    #: Gone quiet: open, sent, silent past the person's threshold, nothing planned.
    quiet_now: int = 0
    quiet_after_days: int = 0
    quiet_by_company: list[tuple[str, int]] = field(default_factory=list)
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
        # An entry from a status to itself is a reason given afterwards (#239), dated
        # when somebody typed it. It is not the day anybody replied.
        .exclude(from_status=F("to_status"))
        .values("application_id")
        .annotate(first=Min("occurred_at"))
    )

    days: dict[int, int] = {}
    for row in first_response:
        when, sent = row["first"], applied_at[row["application_id"]]
        if when >= sent:
            days[row["application_id"]] = (when - sent).days
    return days


def interviews_held(user, *, start=None, end=None) -> int:
    """How many interviews were actually attended, from the diary and the timeline together.

    Two records can hold the same interview. Settling one in the diary marks it *held* and
    writes an *interview* entry dated when it happened; typing one straight onto the timeline
    writes only the entry. Counting either source alone gets a different answer, which is
    precisely what went wrong: Insights read the timeline and the report read the diary, so
    somebody who wrote their interviews down as they happened was shown a number by Postulo
    and a nought on the document an employment office reads (#224).

    So both are counted and the overlap is removed. An entry is the same interview as a diary
    one when it belongs to the same application and is dated to the same moment, which is
    exactly what settling writes -- it takes `occurred_at` from the interview's own start.
    Scheduling writes an *interview scheduled* entry, a different kind, not counted here.
    """
    diary = Interview.objects.for_user(user).filter(outcome=InterviewOutcome.DONE)
    entries = ApplicationEvent.objects.for_user(user).filter(kind=EventKind.INTERVIEW)
    if start is not None:
        diary = diary.filter(starts_at__date__gte=start)
        entries = entries.filter(occurred_at__date__gte=start)
    if end is not None:
        diary = diary.filter(starts_at__date__lte=end)
        entries = entries.filter(occurred_at__date__lte=end)

    held = diary.count()
    twins = set(diary.values_list("application_id", "starts_at"))
    typed = sum(
        1 for pair in entries.values_list("application_id", "occurred_at") if pair not in twins
    )
    return held + typed


def _first_interview_days(applications) -> dict[int, int]:
    """Days from applying to the first interview, per application.

    Read from the log: an interview recorded by hand years ago and one settled through
    the diary both leave an *interview* entry dated when it happened.
    """
    applied_at = {
        application.pk: application.applied_at
        for application in applications
        if application.applied_at is not None
    }
    if not applied_at:
        return {}
    first_interview = (
        ApplicationEvent.objects.filter(application_id__in=applied_at, kind=EventKind.INTERVIEW)
        .values("application_id")
        .annotate(first=Min("occurred_at"))
    )
    days: dict[int, int] = {}
    for row in first_interview:
        when, sent = row["first"], applied_at[row["application_id"]]
        if when >= sent:
            days[row["application_id"]] = (when - sent).days
    return days


#: The stages that mean an interview took place, as far as the status alone can tell.
#: Screening is not one: a recruiter's call moves it without any interview being recorded.
INTERVIEW_STATUSES = {Status.INTERVIEWING, Status.ASSESSMENT}


def _reached_interview(applications, reached: dict[int, set[str]]) -> set[int]:
    """The applications that reached an interview, by the one rule every figure shares.

    An application has reached one when it has an *interview* entry on its timeline, or its
    status ever reached *Interviewing* or *Assessment*. The Interviews widget and the
    Interviews column of every table of sources read this set, so the two can be compared
    (#449).
    """
    ids = {a.pk for a in applications}
    typed = set(
        ApplicationEvent.objects.filter(application_id__in=ids, kind=EventKind.INTERVIEW)
        .values_list("application_id", flat=True)
        .distinct()
    )
    return typed | {pk for pk in ids if reached[pk] & INTERVIEW_STATUSES}


#: What the cached figures are shaped like. The cache is a table and outlives an upgrade,
#: so figures kept by one release are read by the next -- and when `Insights` gains a field
#: they would arrive without it. Counted into the fingerprint, so a release that changes
#: the shape asks for keys no earlier one wrote. 2 added the endings and the two
#: breakdowns of the sources (#239); 3 made one rule of an interview reached (#449).
SHAPE = 3

#: How long a set of figures may sit in the cache at most. The fingerprint below is what
#: actually decides whether they are still true; this is only so that a key for a state
#: nobody will ever be in again does not live in the table for ever.
INSIGHTS_TTL = 60 * 60 * 24


def fingerprint(user) -> str:
    """What the figures depend on, in five small aggregates (#231).

    Everything `build` reads is an application, a timeline entry or a listing of this
    person's, and the names it prints are a company's or a contact's. So: how many of each
    there are, the newest change to one, and the newest entry written. Any of those moving
    means the figures may have moved; none of them moving means they cannot have.

    Five indexed aggregates against loading every application, every industry and every
    status event a search has ever produced, which is what `build` does and what the
    dashboard did on every view. Counting *and* stamping, because a deletion moves the count
    and leaves the newest `updated_at` exactly where it was.

    Companies and contacts joined the other three with #239: the figures by referrer and
    by agency print their names, and somebody renamed -- or merged into somebody else --
    has to be renamed here as well.
    """
    from django.db.models import Max

    from postulo.core import languages
    from postulo.jobs.models import Company, Contact

    applications = Application.objects.for_user(user).aggregate(n=Count("pk"), at=Max("updated_at"))
    events = ApplicationEvent.objects.filter(application__owner=user).aggregate(
        n=Count("pk"), at=Max("pk")
    )
    listings = JobPosting.objects.for_user(user).aggregate(n=Count("pk"), at=Max("updated_at"))
    companies = Company.objects.for_user(user).aggregate(n=Count("pk"), at=Max("updated_at"))
    contacts = Contact.objects.for_user(user).aggregate(n=Count("pk"), at=Max("updated_at"))
    parts = (
        SHAPE,
        # The figures carry words -- the name of a stage, of a reason, *Not recorded* --
        # written in the language they were worked out in, so somebody who changes the
        # language they read in has to be given figures worked out again.
        languages.current(),
        getattr(user, "pk", 0),
        applications["n"],
        applications["at"],
        events["n"],
        events["at"],
        listings["n"],
        listings["at"],
        companies["n"],
        companies["at"],
        contacts["n"],
        contacts["at"],
        # The threshold is a preference rather than a record, and `quiet_now` is computed
        # from it. Somebody changing it from 21 days to 14 changes the figures without
        # touching anything the three aggregates above can see.
        getattr(getattr(user, "profile", None), "quiet_after_days", None),
    )
    # Hashed rather than joined. The parts hold timestamps, which carry spaces and colons,
    # and a cache key with either in it is a key some backends refuse -- Django warns about
    # memcached by name. What is wanted here is "has any of this changed", and a digest
    # answers that exactly.
    material = "|".join(str(part) for part in parts).encode("utf-8")
    return f"insights:{hashlib.sha256(material).hexdigest()}"


def insights_for(user) -> Insights:
    """`build`, but not again until something it reads has changed (#231).

    The dashboard's figures widget asked for the whole thing on every view. Nothing about a
    job search changes between two page loads a second apart, and the work is proportional
    to the whole history rather than to what is on screen.

    A cache miss is the old cost plus the fingerprint; a hit is the fingerprint and a read.
    There is no timer to be wrong about: a key nobody can produce again is a key nobody
    reads, and `INSIGHTS_TTL` only stops it being kept for ever.
    """
    from django.core.cache import cache

    key = fingerprint(user)
    held = cache.get(key)
    if held is not None:
        return held
    figures = build(user)
    cache.set(key, figures, INSIGHTS_TTL)
    return figures


def build(user) -> Insights:
    """Work out what the record says about ``user``'s search.

    Always does the whole of the work. `insights_for` is the one that remembers.
    """
    applications = list(
        Application.objects.for_user(user)
        .select_related(
            "posting",
            "posting__company",
            "referred_by",
            "referred_by__company",
            "through_agency",
        )
        .prefetch_related("posting__company__industries")
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
    # Still open, as well as still unanswered. Only *ghosted* was excluded, so an
    # application somebody withdrew -- or one they marked rejected without a reply
    # arriving -- counted as waiting for a reply for the rest of time, and the Outcomes
    # widget showed a number that could only ever grow (#224). Withdrawing is the person
    # saying they have stopped waiting, which is exactly the thing this counts.
    insights.still_waiting = sum(
        1
        for a in ever_applied
        if a.pk not in reply_days
        and a.applied_at is not None
        and a.status in QUIET_STATUSES
        and (now - a.applied_at).days >= 0
    )

    # ------------------------------------------------------------- interviews
    interview_days = _first_interview_days(ever_applied)
    interviewed_ids = _reached_interview(ever_applied, reached)
    insights.interviewed = len(interviewed_ids)
    if interview_days:
        insights.median_days_to_interview = statistics.median(sorted(interview_days.values()))
    diary = Interview.objects.for_user(user)
    insights.interviews_held = interviews_held(user)
    insights.interviews_ahead = diary.upcoming().count()
    kinds = (
        diary.filter(outcome=InterviewOutcome.DONE)
        .values_list("kind")
        .annotate(count=Count("id"))
        .order_by("-count", "kind")
    )
    labels = dict(Interview._meta.get_field("kind").choices)
    insights.interview_kinds = [(str(labels.get(kind, kind)), count) for kind, count in kinds]

    # ------------------------------------------------------------------ quiet
    from .quiet import quiet_applications, threshold_for

    quiet_ids = set(quiet_applications(user).values_list("pk", flat=True))
    insights.quiet_now = len(quiet_ids)
    insights.quiet_after_days = threshold_for(user)
    by_company: dict[str, int] = {}
    for application in applications:
        if application.pk in quiet_ids:
            name = application.posting.company.name
            by_company[name] = by_company.get(name, 0) + 1
    insights.quiet_by_company = sorted(by_company.items(), key=lambda item: (-item[1], item[0]))

    def count(row: SourceRow, application) -> None:
        """One application, added to one line of a table of sources."""
        row.applied += 1
        statuses = reached[application.pk]
        if statuses & RESPONSE_STATUSES:
            row.responded += 1
        if application.pk in interviewed_ids:
            row.interviewed += 1
        if Status.OFFER in statuses:
            row.offers += 1
        if application.pk in quiet_ids:
            row.quiet += 1

    def by_size(rows) -> list[SourceRow]:
        return sorted(rows, key=lambda row: (-row.applied, row.name))

    # --------------------------------------------------------------- sources
    rows: dict[str, SourceRow] = {}
    for application in ever_applied:
        name = (application.posting.source or "").strip() or str(_("Not recorded"))
        count(rows.setdefault(name, SourceRow(name=name)), application)
    insights.sources = by_size(rows.values())

    # ------------------------------------------------- by referrer and by agency
    # Keyed on the record rather than on its name, so two people called the same thing are
    # two lines; the company beside a referrer's name is what tells them apart (#239).
    by_referrer: dict[int, SourceRow] = {}
    by_agency: dict[int, SourceRow] = {}
    for application in ever_applied:
        referrer = application.referred_by
        if referrer is not None:
            name = referrer.name
            if referrer.company_id:
                name = f"{name} · {referrer.company.name}"
            count(by_referrer.setdefault(referrer.pk, SourceRow(name=name)), application)
        agency = application.through_agency
        if agency is not None:
            count(by_agency.setdefault(agency.pk, SourceRow(name=agency.name)), application)
    insights.by_referrer = by_size(by_referrer.values())
    insights.by_agency = by_size(by_agency.values())

    # ------------------------------------------------------------- industries
    by_industry: dict[str, SourceRow] = {}
    for application in ever_applied:
        names = [i.name for i in application.posting.company.industries.all()] or [
            str(_("Not recorded"))
        ]
        for name in names:
            count(by_industry.setdefault(name, SourceRow(name=name)), application)
    insights.industries = by_size(by_industry.values())

    # ---------------------------------------------------------------- endings
    insights.endings = _endings(applications)

    # ----------------------------------------------------------- over time
    per_month = (
        Application.objects.for_user(user)
        .filter(applied_at__isnull=False)
        .annotate(month=TruncMonth("applied_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    insights.by_month = _fill_months(
        {row["month"].date(): row["count"] for row in per_month if row["month"]}
    )

    return insights


def _fill_months(counts: dict[dt.date, int]) -> list[tuple[dt.date, int]]:
    """Every month from the first with an application to this one, nought where none was sent.

    The widget exists so a quiet stretch is visible (#397), and a month left out of the list
    is a quiet stretch that cannot be seen. The months are the first day of each, as dates,
    so the template prints them in the reader's language.
    """
    if not counts:
        return []
    month, last = min(counts), max(max(counts), timezone.localdate().replace(day=1))
    months = []
    while month <= last:
        months.append((month, counts.get(month, 0)))
        month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return months


#: The order the stages of an ending are listed in: the board's, then *Accepted*, which an
#: application can be withdrawn from and which is on no board.
STAGE_ORDER = (*BOARD_STATUSES, Status.ACCEPTED)


def _endings(applications) -> Endings:
    """Where the applications that ended had got to, and why they ended (#239).

    Both are read from the timeline: `endings.for_applications` is one query for every
    application that ended, and the rest is counting. A stage or a reason nothing ended at
    is left out rather than listed at nought, because a job search produces small numbers
    and a table of mostly zeros hides the three that are not.
    """
    found = endings.for_applications(applications)
    summary = Endings(total=len(found))
    if not found:
        return summary

    stage_labels = dict(Status.choices)
    reason_labels = dict(EndReason.choices)
    unrecorded = str(_("Not recorded"))
    stages: dict[str, EndingRow] = {}
    reasons: dict[str, EndingRow] = {}

    for ending in found.values():
        stage = stages.setdefault(
            ending.last_stage,
            EndingRow(
                key=ending.last_stage,
                label=str(stage_labels.get(ending.last_stage, ending.last_stage)) or unrecorded,
            ),
        )
        reason = reasons.setdefault(
            ending.reason,
            EndingRow(
                key=ending.reason,
                label=str(reason_labels.get(ending.reason, ending.reason)) or unrecorded,
            ),
        )
        for row in (stage, reason):
            # `ending.status` is one of the three by construction: `endings.read` answers
            # for nothing else, and the three are the row's three counters.
            setattr(row, ending.status, getattr(row, ending.status) + 1)
        if ending.reason:
            summary.explained += 1

    order = {status: index for index, status in enumerate(STAGE_ORDER)}
    # The unrecorded line goes last in both: what is not known, after what is.
    summary.stages = sorted(
        stages.values(), key=lambda row: (not row.key, order.get(row.key, len(order)), row.label)
    )
    summary.reasons = sorted(reasons.values(), key=lambda row: (not row.key, -row.total, row.label))
    return summary


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
