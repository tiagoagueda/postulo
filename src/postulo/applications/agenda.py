"""The calendar: a month of the dated things of a search, and the same things in a week, a
day and an agenda (#204).

Everything dated was a list, and the lists were in different places: interviews soonest
first on one page, reminders on another, and a person asking "what is on this week"
opened both and read dates. This draws the month.

**A table the server renders, not a script.** Postulo vendors two scripts and its content
security policy allows no inline script and no CDN, and a month is seven columns and a
row per week, which a template can draw: the days of the month in the person's time zone,
*earlier* and *later* as links carrying the month, so it is bookmarkable and works with
nothing loaded. A calendar library is a decision to take deliberately if a week or a day
view ever wants one, and neither does yet.

**One shape for the page to draw.** An :class:`Event` is a moment or a span, a title, a
link, and a kind for its colour and its words. Interviews and reminders fill it now; a
deadline, a listing closing, an appointment at the employment office can fill it later
without the page changing, the way the report reads its rows through `reports.Evidence`.
A reminder that is done is drawn as done rather than dropped, and so is an interview that
was cancelled: the calendar is a record of the month as well as a plan for it.

**The person's days.** The middleware activates their time zone, and every boundary here
is a local midnight made aware in it, so a reminder at half past eleven at night in
Lisbon is on the day it was set for and not on the UTC day after.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from .models import Interview, InterviewOutcome, Reminder
from .reports import week_start

VIEWS = ("month", "week", "day", "agenda")
DEFAULT_VIEW = "month"
#: How far the agenda reads ahead. A month, roughly; a phone wants a list, not a grid.
AGENDA_DAYS = 30
#: How many of a day's events a month cell draws before it counts the rest. A cell with
#: three interviews and two reminders cannot hold five sentences; it holds two and the
#: number left, and the day itself opens on the rest.
PER_CELL = 2

INTERVIEW = "interview"
REMINDER = "reminder"


@dataclass(frozen=True)
class Event:
    """One dated thing, as the calendar draws it."""

    kind: str
    title: str
    url: str
    starts_at: dt.datetime
    ends_at: dt.datetime | None = None
    #: A second line: the company, the kind of interview, the application a reminder is for.
    detail: str = ""
    #: Drawn as over -- a reminder done, an interview cancelled -- rather than dropped.
    muted: bool = False

    @property
    def day(self) -> dt.date:
        return timezone.localdate(self.starts_at)

    @property
    def local_start(self) -> dt.datetime:
        return timezone.localtime(self.starts_at)

    @property
    def local_end(self) -> dt.datetime | None:
        return timezone.localtime(self.ends_at) if self.ends_at else None

    @property
    def is_span(self) -> bool:
        return self.ends_at is not None


def _bounds(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Local midnights, made aware in the person's zone, for a half-open range of days."""
    lower = timezone.make_aware(dt.datetime.combine(start, dt.time.min))
    upper = timezone.make_aware(dt.datetime.combine(end, dt.time.min))
    return lower, upper


def events_between(user, start: dt.date, end: dt.date) -> list[Event]:
    """Every event on a day in ``[start, end)``, soonest first."""
    lower, upper = _bounds(start, end)
    events: list[Event] = []
    interviews = (
        Interview.objects.for_user(user)
        .with_display_data()
        .filter(starts_at__gte=lower, starts_at__lt=upper)
    )
    for interview in interviews:
        posting = interview.application.posting
        events.append(
            Event(
                kind=INTERVIEW,
                title=str(posting.company.name),
                url=interview.get_absolute_url(),
                starts_at=interview.starts_at,
                ends_at=interview.ends_at,
                detail=f"{interview.get_kind_display()} · {posting.title}",
                muted=interview.outcome in {InterviewOutcome.CANCELLED, InterviewOutcome.NO_SHOW},
            )
        )
    reminders = (
        Reminder.objects.for_user(user)
        .select_related("application", "application__posting")
        .filter(due_at__gte=lower, due_at__lt=upper)
    )
    for reminder in reminders:
        application = reminder.application
        events.append(
            Event(
                kind=REMINDER,
                title=reminder.summary,
                url=(
                    application.get_absolute_url()
                    if application is not None
                    else reverse("applications:reminder_list")
                ),
                starts_at=reminder.due_at,
                detail=application.posting.title if application is not None else "",
                muted=reminder.is_done,
            )
        )
    events.sort(key=lambda event: (event.starts_at, event.kind, event.title))
    return events


def by_day(events: list[Event]) -> dict[dt.date, list[Event]]:
    grouped: dict[dt.date, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.day, []).append(event)
    return grouped


@dataclass
class Day:
    """One cell: its date, what is on, and how it relates to the month and to today."""

    date: dt.date
    events: list[Event] = field(default_factory=list)
    outside: bool = False
    today: bool = False

    @property
    def shown(self) -> list[Event]:
        return self.events[:PER_CELL]

    @property
    def more(self) -> int:
        return max(0, len(self.events) - PER_CELL)

    @property
    def url(self) -> str:
        return url_for("day", self.date)


# ------------------------------------------------------------------------ shapes


def first_of_month(day: dt.date) -> dt.date:
    return day.replace(day=1)


def next_month(day: dt.date) -> dt.date:
    first = first_of_month(day)
    return (first + dt.timedelta(days=32)).replace(day=1)


def previous_month(day: dt.date) -> dt.date:
    return (first_of_month(day) - dt.timedelta(days=1)).replace(day=1)


def month_range(day: dt.date) -> tuple[dt.date, dt.date]:
    """The grid's range: whole weeks, from the week the 1st is in to the week the last is in."""
    start = week_start(first_of_month(day))
    end = week_start(next_month(day) - dt.timedelta(days=1)) + dt.timedelta(days=7)
    return start, end


def days_between(start: dt.date, end: dt.date):
    day = start
    while day < end:
        yield day
        day += dt.timedelta(days=1)


def month_grid(day: dt.date, events: list[Event], *, today: dt.date) -> list[list[Day]]:
    """Weeks of seven, the days outside the month marked so the grid can grey them."""
    start, end = month_range(day)
    on = by_day(events)
    days = [
        Day(
            date=date,
            events=on.get(date, []),
            outside=date.month != day.month,
            today=date == today,
        )
        for date in days_between(start, end)
    ]
    return [days[index : index + 7] for index in range(0, len(days), 7)]


def week_days(day: dt.date, events: list[Event], *, today: dt.date) -> list[Day]:
    start = week_start(day)
    on = by_day(events)
    return [
        Day(date=date, events=on.get(date, []), today=date == today)
        for date in days_between(start, start + dt.timedelta(days=7))
    ]


# ------------------------------------------------------------------------- pages


def url_for(view: str, day: dt.date) -> str:
    """The address of one view on one day: the month by its month, the rest by their day."""
    base = reverse("applications:calendar")
    if view == DEFAULT_VIEW:
        return f"{base}?month={day:%Y-%m}"
    return f"{base}?view={view}&on={day:%Y-%m-%d}"


def month_from(raw: str) -> dt.date | None:
    try:
        return dt.datetime.strptime((raw or "").strip(), "%Y-%m").date()
    except ValueError:
        return None


def day_from(raw: str) -> dt.date | None:
    try:
        return dt.datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass
class Page:
    """What the template draws: the view, its range, its title, and where the links go."""

    view: str
    on: dt.date
    start: dt.date
    end: dt.date
    title: str
    earlier: str
    later: str
    today: str
    events: list[Event]
    weeks: list[list[Day]] = field(default_factory=list)
    days: list[Day] = field(default_factory=list)
    weekday_names: list[str] = field(default_factory=list)
    #: The agenda and the day: (date, events) pairs, days with nothing on left out of the
    #: agenda because thirty rows of nothing is what the month grid is for.
    listed: list[tuple[dt.date, list[Event]]] = field(default_factory=list)

    @property
    def switcher(self) -> list[tuple[str, str, str, bool]]:
        """(view, label, url, current) for each shape, on the day this page is on."""
        return [
            (view, VIEW_LABELS[view], url_for(view, self.on), view == self.view) for view in VIEWS
        ]

    @property
    def empty(self) -> bool:
        return not self.events


VIEW_LABELS = {
    "month": _("Month"),
    "week": _("Week"),
    "day": _("Day"),
    "agenda": _("Agenda"),
}


def build(user, view: str, on: dt.date, *, today: dt.date | None = None) -> Page:
    today = today or timezone.localdate()
    view = view if view in VIEWS else DEFAULT_VIEW
    if view == "month":
        start, end = month_range(on)
        events = events_between(user, start, end)
        page = Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=formats.date_format(first_of_month(on), "F Y"),
            earlier=url_for(view, previous_month(on)),
            later=url_for(view, next_month(on)),
            today=url_for(view, today),
            events=events,
        )
        page.weeks = month_grid(on, events, today=today)
        page.weekday_names = [formats.date_format(day.date, "D") for day in page.weeks[0]]
        return page
    if view == "week":
        start = week_start(on)
        end = start + dt.timedelta(days=7)
        events = events_between(user, start, end)
        page = Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=_("Week of %(day)s") % {"day": formats.date_format(start, "j F Y")},
            earlier=url_for(view, start - dt.timedelta(days=7)),
            later=url_for(view, end),
            today=url_for(view, today),
            events=events,
        )
        page.days = week_days(on, events, today=today)
        return page
    if view == "day":
        start, end = on, on + dt.timedelta(days=1)
        events = events_between(user, start, end)
        return Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=formats.date_format(on, "l j F Y"),
            earlier=url_for(view, on - dt.timedelta(days=1)),
            later=url_for(view, end),
            today=url_for(view, today),
            events=events,
            listed=[(on, events)],
        )
    start, end = on, on + dt.timedelta(days=AGENDA_DAYS)
    events = events_between(user, start, end)
    return Page(
        view="agenda",
        on=on,
        start=start,
        end=end,
        title=_("%(days)s days from %(day)s")
        % {"days": AGENDA_DAYS, "day": formats.date_format(on, "j F Y")},
        earlier=url_for("agenda", on - dt.timedelta(days=AGENDA_DAYS)),
        later=url_for("agenda", end),
        today=url_for("agenda", today),
        events=events,
        listed=sorted(by_day(events).items()),
    )


def page_for(params, user, *, today: dt.date | None = None) -> Page:
    """The page a request asks for: ``?month=2026-09``, or ``?view=week&on=2026-09-15``.

    Anything unreadable falls back to today, in the shape asked for: an address somebody
    mistyped still gets a calendar rather than an error about dates.
    """
    today = today or timezone.localdate()
    view = params.get("view", "") or DEFAULT_VIEW
    on = day_from(params.get("on", "")) or month_from(params.get("month", "")) or today
    if view == DEFAULT_VIEW and "on" not in params and "month" in params:
        on = month_from(params.get("month", "")) or today
    return build(user, view, on, today=today)
