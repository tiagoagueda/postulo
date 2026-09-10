"""A report about a period: what was sent, how regularly, and what came back (#56).

Two different people ask for this and they want the same document. An **employment office**
makes benefit conditional on actually looking, and wants a list: what was applied for,
where, when, and what came of it. The **person searching** wants to know whether a month
that felt like nothing was in fact eleven applications across four weeks, and where the gap
was. The first needs evidence; the second needs a shape; both are the same page.

**Everything is scoped to a period, and no report ever quietly mixes in older activity.**
That is the whole reason this module exists beside :mod:`postulo.applications.analytics`,
which computes across everything and takes no dates. The two share their *definitions* --
what counts as a reply is imported from there rather than restated -- and nothing else.

**The report is a snapshot of the record at a moment**, not a document somebody edits. That
is what makes it evidence, and it is also why nothing here is stored: a report is computed
when it is asked for, from rows that are already the truth. Re-running it next month over
the same period gives the same page, unless the record itself changed.

**Two questions, kept apart on purpose.** *Sent in this period* -- the cadence and the
evidence list -- reads applications by when they were sent. *Happened in this period* --
replies, interviews, offers, rejections -- reads the event log by when the event occurred,
so a reply in September to an August application is September activity, which it is. Folding
the two together would produce a figure that is true of neither question, and the page says
which is which rather than leaving it to be guessed.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from itertools import pairwise

from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from .analytics import RESPONSE_STATUSES
from .models import (
    Application,
    ApplicationEvent,
    Interview,
    InterviewOutcome,
    Status,
)

#: The kinds of period a report can cover. A month is first because it is what an
#: employment office asks for and what a benefit regime is counted in.
KINDS = ("month", "quarter", "weeks", "custom")

#: How many weeks the "last n weeks" period offers, and its default.
WEEK_CHOICES = (4, 8, 12, 26, 52)
DEFAULT_WEEKS = 4

#: A custom range longer than this is refused. Not a limit on ambition: a report is
#: evidence for a period somebody was looking for work, and five years of weekly bars is a
#: page nobody reads and a query nobody needs.
MAX_DAYS = 400


# ------------------------------------------------------------------------ weeks


def week_start(day: dt.date) -> dt.date:
    """The first day of the week ``day`` falls in, for the locale in force.

    Not always a Monday. Django knows which day a week starts on in each language, and a
    report handed to somebody is laid out in their weeks rather than in ISO's.
    """
    first = formats.get_format("FIRST_DAY_OF_WEEK")
    try:
        first = int(first)
    except (TypeError, ValueError):
        first = 1
    return day - dt.timedelta(days=(day.isoweekday() % 7 - first % 7) % 7)


# ----------------------------------------------------------------------- period


@dataclass(frozen=True)
class Period:
    """A run of days, both ends included.

    Inclusive at both ends because that is how a person says it -- *the first to the
    thirtieth* -- and a report whose last day is silently missing is evidence of the wrong
    thing.
    """

    start: dt.date
    end: dt.date
    kind: str = "custom"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        if self.kind == "month":
            return formats.date_format(self.start, "F Y")
        if self.kind == "quarter":
            return _("Q%(quarter)s %(year)s") % {
                "quarter": (self.start.month - 1) // 3 + 1,
                "year": self.start.year,
            }
        return _("%(from)s to %(to)s") % {
            "from": formats.date_format(self.start, "DATE_FORMAT"),
            "to": formats.date_format(self.end, "DATE_FORMAT"),
        }

    def holds(self, day: dt.date | None) -> bool:
        return day is not None and self.start <= day <= self.end

    def shifted(self, step: int) -> Period | None:
        """The same shape of period, ``step`` of them away. ``None`` where that has no meaning.

        A month before a month and a quarter before a quarter; anything measured in weeks or
        given by hand moves by its own length, which is the only reading that keeps the
        period the same size.
        """
        if self.kind == "month":
            month = self.start.month - 1 + step
            return month_period(self.start.year + month // 12, month % 12 + 1)
        if self.kind == "quarter":
            quarter = (self.start.month - 1) // 3 + step
            return quarter_period(self.start.year + quarter // 4, quarter % 4 + 1)
        length = dt.timedelta(days=self.days)
        return Period(self.start + length * step, self.end + length * step, self.kind)


def month_period(year: int, month: int) -> Period:
    last = calendar.monthrange(year, month)[1]
    return Period(dt.date(year, month, 1), dt.date(year, month, last), "month")


def quarter_period(year: int, quarter: int) -> Period:
    first_month = (quarter - 1) * 3 + 1
    start = dt.date(year, first_month, 1)
    end_month = first_month + 2
    return Period(
        start, dt.date(year, end_month, calendar.monthrange(year, end_month)[1]), "quarter"
    )


def weeks_period(weeks: int, *, today: dt.date) -> Period:
    """The last ``weeks`` whole weeks, ending with the one in progress.

    The current week is included rather than waited for: a person asking on a Wednesday how
    the last month went means including this week, and a chart that stops on Sunday hides
    the two applications sent since.
    """
    end = week_start(today) + dt.timedelta(days=6)
    return Period(week_start(today) - dt.timedelta(weeks=weeks - 1), end, "weeks")


def _int(raw, fallback: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return fallback


def _date(raw) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def period_from(params, *, today: dt.date | None = None) -> Period:
    """Read a period out of a query string, falling back to the month in progress.

    Nothing here refuses: an address somebody edited by hand, or a link that has outlived
    the shape it was written for, gets a sensible period rather than an error page. What it
    will not do is silently produce a period of a different *kind* from the one asked for,
    which is why each branch falls back within itself.
    """
    today = today or timezone.localdate()
    kind = str(params.get("period", "") or "month")
    if kind not in KINDS:
        kind = "month"

    if kind == "month":
        on = _date(f"{params.get('on', '')}-01") or today
        return month_period(on.year, on.month)

    if kind == "quarter":
        # `2026-Q3` for a link somebody wrote or bookmarked, and `2026-09` for the month
        # control on the page -- which then means *the quarter that month falls in*. One
        # control for both, because a second one that is ignored three times out of four is
        # worse than none, and a quarter silently ignoring what was typed is worse than that.
        raw = str(params.get("on", "") or "")
        year, marked, number = raw.partition("-Q")
        if marked:
            return quarter_period(
                _int(year, today.year), min(4, max(1, _int(number, (today.month - 1) // 3 + 1)))
            )
        month = _date(f"{raw}-01") or today
        return quarter_period(month.year, (month.month - 1) // 3 + 1)

    if kind == "weeks":
        weeks = _int(params.get("weeks"), DEFAULT_WEEKS)
        return weeks_period(min(104, max(1, weeks)), today=today)

    start = _date(params.get("from")) or today.replace(day=1)
    end = _date(params.get("to")) or today
    if end < start:
        start, end = end, start
    if (end - start).days + 1 > MAX_DAYS:
        end = start + dt.timedelta(days=MAX_DAYS - 1)
    return Period(start, end, "custom")


def as_query(period: Period) -> dict[str, str]:
    """The query string that asks for this period again. What every link on the page uses."""
    if period.kind == "month":
        return {"period": "month", "on": period.start.strftime("%Y-%m")}
    if period.kind == "quarter":
        return {
            "period": "quarter",
            "on": f"{period.start.year}-Q{(period.start.month - 1) // 3 + 1}",
        }
    if period.kind == "weeks":
        return {"period": "weeks", "weeks": str(period.days // 7)}
    return {"period": "custom", "from": period.start.isoformat(), "to": period.end.isoformat()}


# ---------------------------------------------------------------------- cadence


@dataclass
class Week:
    start: dt.date
    end: dt.date
    count: int = 0
    #: Whether this week has not finished yet, so its bar is not a whole week's worth.
    in_progress: bool = False
    #: Whether the week reaches outside the period. A month rarely begins on the first day
    #: of a week, so the first and last bars usually cover fewer days than the rest -- and a
    #: short bar that is short because the week was short is a bar that misleads unless it
    #: is marked. Nothing from outside the period is ever *counted* in it.
    partial: bool = False

    @property
    def empty(self) -> bool:
        return self.count == 0

    @property
    def label(self) -> str:
        return formats.date_format(self.start, "j M")


@dataclass
class Cadence:
    """How regularly applications went out, which is the question the report is named for."""

    weeks: list[Week] = field(default_factory=list)
    total: int = 0
    #: The longest run of days inside the period with nothing sent. Measured inside it, so a
    #: silence that began in March is not carried into April's report.
    longest_gap: int = 0
    #: Weeks in a row with at least one application, ending with the last week of the period.
    current_run: int = 0

    @property
    def empty_weeks(self) -> int:
        return sum(1 for week in self.weeks if week.empty)

    @property
    def per_week(self) -> float:
        return self.total / len(self.weeks) if self.weeks else 0.0

    @property
    def busiest(self) -> int:
        """The tallest bar, and never zero: a chart whose scale is zero has no scale."""
        return max((week.count for week in self.weeks), default=0) or 1


def _cadence(period: Period, days_sent: list[dt.date], *, today: dt.date) -> Cadence:
    cadence = Cadence(total=len(days_sent))

    cursor = week_start(period.start)
    while cursor <= period.end:
        finish = cursor + dt.timedelta(days=6)
        cadence.weeks.append(
            Week(
                start=cursor,
                end=finish,
                count=sum(1 for day in days_sent if cursor <= day <= finish),
                in_progress=cursor <= today <= finish,
                partial=cursor < period.start or finish > period.end,
            )
        )
        cursor = finish + dt.timedelta(days=1)

    # A period still running is only a gap up to today. Counting the rest of the month as
    # silence would report a gap nobody has had yet.
    last_day = min(period.end, today) if period.start <= today else period.end
    marks = sorted({day for day in days_sent if period.start <= day <= last_day})
    if not marks:
        cadence.longest_gap = max(0, (last_day - period.start).days + 1)
    else:
        gaps = [(marks[0] - period.start).days, (last_day - marks[-1]).days]
        gaps += [(later - earlier).days - 1 for earlier, later in pairwise(marks)]
        cadence.longest_gap = max(gaps)

    for week in reversed(cadence.weeks):
        if week.empty:
            break
        cadence.current_run += 1

    return cadence


# --------------------------------------------------------------------- evidence


@dataclass
class Evidence:
    """One application as the report lists it. The row an employment office reads."""

    applied_on: dt.date
    company: str
    role: str
    source: str
    url: str
    status: str
    last_activity_on: dt.date | None
    pk: int = 0


@dataclass
class Tally:
    """Where the effort went, under one heading."""

    name: str
    count: int

    def share_of(self, total: int) -> float:
        return 100 * self.count / total if total else 0.0


@dataclass
class Happened:
    """What came back during the period, whoever it was to."""

    replies: int = 0
    interviews: int = 0
    offers: int = 0
    rejections: int = 0


@dataclass
class Report:
    period: Period
    cadence: Cadence
    evidence: list[Evidence] = field(default_factory=list)
    happened: Happened = field(default_factory=Happened)
    sources: list[Tally] = field(default_factory=list)
    industries: list[Tally] = field(default_factory=list)
    #: Drafted in the period and never sent. Counted so the page can say what it leaves out.
    drafts: int = 0
    produced_at: dt.datetime | None = None
    person: str = ""

    @property
    def total(self) -> int:
        return len(self.evidence)


def _first_reaching(user, statuses, period: Period) -> int:
    """Applications whose *first* arrival at any of ``statuses`` falls inside the period.

    First rather than any: an application acknowledged, then screened, then interviewed in
    one month has had one reply, and counting three would report a busier month than
    happened.
    """
    earliest: dict[int, dt.datetime] = {}
    rows = ApplicationEvent.objects.filter(
        application__owner=user, to_status__in=list(statuses)
    ).values_list("application_id", "occurred_at")
    for application_id, when in rows:
        if application_id not in earliest or when < earliest[application_id]:
            earliest[application_id] = when
    return sum(1 for when in earliest.values() if period.holds(timezone.localdate(when)))


def build(user, period: Period, *, today: dt.date | None = None) -> Report:
    """The report for one person and one period."""
    today = today or timezone.localdate()

    sent = list(
        Application.objects.for_user(user)
        .filter(applied_at__isnull=False)
        .select_related("posting", "posting__company")
        .prefetch_related("posting__company__industries")
        .with_activity()
        .order_by("applied_at")
    )
    inside = [a for a in sent if period.holds(timezone.localdate(a.applied_at))]

    labels = dict(Application._meta.get_field("status").choices)
    evidence = [
        Evidence(
            applied_on=timezone.localdate(application.applied_at),
            company=application.posting.company.name,
            role=application.posting.title,
            source=(application.posting.source or "").strip(),
            url=application.posting.url or "",
            status=str(labels.get(application.status, application.status)),
            last_activity_on=(
                timezone.localdate(application.last_activity_at)
                if getattr(application, "last_activity_at", None)
                else None
            ),
            pk=application.pk,
        )
        for application in inside
    ]

    report = Report(
        period=period,
        cadence=_cadence(period, [row.applied_on for row in evidence], today=today),
        evidence=evidence,
        produced_at=timezone.localtime(),
        person=user.display_name if hasattr(user, "display_name") else str(user),
    )

    report.drafts = (
        Application.objects.for_user(user)
        .filter(status=Status.DRAFT, created_at__date__gte=period.start)
        .filter(created_at__date__lte=period.end)
        .count()
    )

    report.happened = Happened(
        replies=_first_reaching(user, RESPONSE_STATUSES, period),
        interviews=Interview.objects.for_user(user)
        .filter(outcome=InterviewOutcome.DONE)
        .filter(starts_at__date__gte=period.start, starts_at__date__lte=period.end)
        .count(),
        offers=_first_reaching(user, {Status.OFFER}, period),
        rejections=_first_reaching(user, {Status.REJECTED}, period),
    )

    unrecorded = str(_("Not recorded"))
    by_source: dict[str, int] = {}
    for row in evidence:
        name = row.source or unrecorded
        by_source[name] = by_source.get(name, 0) + 1
    report.sources = [
        Tally(name, count)
        for name, count in sorted(by_source.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    by_industry: dict[str, int] = {}
    for application in inside:
        names = [i.name for i in application.posting.company.industries.all()] or [unrecorded]
        for name in names:
            by_industry[name] = by_industry.get(name, 0) + 1
    report.industries = [
        Tally(name, count)
        for name, count in sorted(by_industry.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    return report


# -------------------------------------------------------------------------- CSV

#: The evidence list, and only it. A cadence is a reading of these rows rather than a
#: separate fact, so a spreadsheet holding them can produce its own.
CSV_HEADERS: tuple = (
    _("Applied on"),
    _("Company"),
    _("Role"),
    _("Found via"),
    _("Address"),
    _("Status"),
    _("Last activity"),
)


def as_csv(report: Report) -> str:
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([str(header) for header in CSV_HEADERS])
    for row in report.evidence:
        writer.writerow(
            [
                row.applied_on.isoformat(),
                row.company,
                row.role,
                row.source,
                row.url,
                row.status,
                row.last_activity_on.isoformat() if row.last_activity_on else "",
            ]
        )
    return out.getvalue()


def filename(report: Report, suffix: str) -> str:
    """A name that says what the file is about without being opened."""
    return f"postulo-report-{report.period.start:%Y-%m-%d}-{report.period.end:%Y-%m-%d}.{suffix}"
