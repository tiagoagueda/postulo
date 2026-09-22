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

**One shape for the page to draw.** An :class:`Event` is a moment, a span or a whole day,
a title, a link, and a kind for its colour and its words. Four kinds fill it: an interview, a
reminder, an application's deadline and a listing's closing date. An appointment at the
employment office can fill it later without the page changing, the way the report reads its
rows through `reports.Evidence`. A reminder that is done is drawn as done rather than
dropped, and so is an interview that was cancelled: the calendar is a record of the month as
well as a plan for it.

**Deadlines and closing dates were stored and never shown** (#238). `Application.deadline`
and `JobPosting.closes_at` have been columns since the beginning, `closing_soon` counted one
of them on the dashboard, and neither was ever on the calendar or in the feed -- so a page
built to answer "what is on this week" answered it without the two dates that cannot be
missed. They are whole days rather than moments, because that is what they are: nobody knows
the hour an employer stops reading, and inventing midnight would put a deadline at the top of
a day it is really the end of.

**Four kinds means a way to see fewer.** `?kinds=` narrows to the ones named, and the page
draws a legend that is also the control: each kind is a link that takes it out or puts it
back, so what the colours mean and what is being shown are the same thing in one place rather
than a key and a filter bar that can disagree.

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

from postulo.jobs.models import JobPosting

from .models import Application, Interview, InterviewOutcome, Offer, Reminder, Status
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
DEADLINE = "deadline"
CLOSING = "closing"
ANSWER = "answer"

#: Every kind, in the order the legend draws them, with what it is called and the tone the
#: stylesheet paints it. A tuple rather than a set because the legend is a row somebody
#: reads, and a set would shuffle it between requests.
KINDS: tuple[tuple[str, object, str], ...] = (
    (INTERVIEW, _("Interviews"), "brand"),
    (REMINDER, _("Reminders"), "amber"),
    (DEADLINE, _("Deadlines"), "rose"),
    (CLOSING, _("Closing dates"), "teal"),
    (ANSWER, _("Answers due"), "violet"),
)

ALL_KINDS = frozenset(kind for kind, _label, _tone in KINDS)

TONES = {kind: tone for kind, _label, tone in KINDS}

#: What an event is, said in words, for the reader who cannot see which colour it is. Keyed
#: on the kind and on whether it is over, because "cancelled interview" and "interview" are
#: different facts and the strike through the title is the only thing that distinguishes
#: them on screen.
#:
#: Empty where the title already says it: a deadline reads *Deadline: Test Engineer* and a
#: closing *Closes: Test Engineer*, and a prefix would make a screen reader say the word
#: twice. That is deliberate rather than an omission -- the kind has to be in the words
#: somewhere, because colour is never the only carrier (#274), and for these two the
#: cheapest place is the title everybody reads.
SPOKEN: dict[tuple[str, bool], object] = {
    (INTERVIEW, False): _("Interview:"),
    (INTERVIEW, True): _("Cancelled interview:"),
    (REMINDER, False): _("Reminder:"),
    (REMINDER, True): _("Done reminder:"),
    (DEADLINE, False): "",
    (DEADLINE, True): _("Already sent —"),
    (CLOSING, False): "",
    (CLOSING, True): _("Already decided —"),
    (ANSWER, False): "",
    (ANSWER, True): _("Already answered —"),
}


def kinds_from(raw: str) -> frozenset[str]:
    """The kinds an address asks for. Anything unrecognised, or nothing, means all of them.

    Never empty. A calendar showing nothing because a parameter was mistyped is a page that
    looks broken rather than narrow, and "none of them" is not a question anybody asks of a
    calendar.
    """
    asked = {piece.strip() for piece in (raw or "").split(",") if piece.strip()}
    wanted = asked & ALL_KINDS
    return frozenset(wanted) if wanted else frozenset(ALL_KINDS)


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
    #: Drawn as over -- a reminder done, an interview cancelled, a listing already applied
    #: to or discarded -- rather than dropped.
    muted: bool = False
    #: A whole day rather than a moment: a deadline and a closing date are dates, and a time
    #: drawn beside them would be a midnight nobody chose (#238).
    all_day: bool = False

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

    @property
    def tone(self) -> str:
        """The stylesheet class this event is painted with, or grey for a kind nobody knows.

        The same guard a tag has: the kinds are a closed set here, but the template asking
        for `cal-<kind>` would draw an unpainted box rather than a wrong one, which looks
        like a broken stylesheet.
        """
        return f"cal-{TONES.get(self.kind, 'grey')}"

    @property
    def said(self) -> str:
        """What this event is, in words, for a reader who cannot see the colour.

        Empty where the title already carries it, which is the whole of `SPOKEN`'s comment.
        """
        return str(SPOKEN.get((self.kind, self.muted), ""))


def _bounds(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Local midnights, made aware in the person's zone, for a half-open range of days."""
    lower = timezone.make_aware(dt.datetime.combine(start, dt.time.min))
    upper = timezone.make_aware(dt.datetime.combine(end, dt.time.min))
    return lower, upper


def events_between(user, start: dt.date, end: dt.date, kinds=None) -> list[Event]:
    """Every event on a day in ``[start, end)``, soonest first, of the kinds asked for."""
    wanted = frozenset(kinds) if kinds else ALL_KINDS
    lower, upper = _bounds(start, end)
    events: list[Event] = []
    interviews = (
        Interview.objects.for_user(user)
        .with_display_data()
        .filter(starts_at__gte=lower, starts_at__lt=upper)
        if INTERVIEW in wanted
        else ()
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
        if REMINDER in wanted
        else ()
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
    if DEADLINE in wanted:
        events += _deadlines(user, start, end)
    if CLOSING in wanted:
        events += _closings(user, start, end)
    if ANSWER in wanted:
        events += _answers(user, start, end)
    events.sort(key=lambda event: (event.starts_at, event.kind, event.title))
    return events


def _at_midnight(day: dt.date) -> dt.datetime:
    """A whole-day event's moment, so it sorts and groups with everything else.

    `Event.day` reads the local date back out of it, which is all the grid needs; `all_day`
    is what stops the template printing the hour.
    """
    return timezone.make_aware(dt.datetime.combine(day, dt.time.min))


def _deadlines(user, start: dt.date, end: dt.date) -> list[Event]:
    """Every application whose deadline falls in the range.

    Muted once the application has been sent: the date still belongs on the calendar as a
    record of what it was, and it is no longer something to act on. That is the rule a done
    reminder already follows.
    """
    applications = (
        Application.objects.for_user(user)
        .select_related("posting", "posting__company")
        .filter(deadline__gte=start, deadline__lt=end)
    )
    return [
        Event(
            kind=DEADLINE,
            title=str(_("Deadline: %(role)s") % {"role": application.posting.title}),
            url=application.get_absolute_url(),
            starts_at=_at_midnight(application.deadline),
            detail=str(application.posting.company.name),
            muted=application.applied_at is not None,
            all_day=True,
        )
        for application in applications
    ]


def _answers(user, start: dt.date, end: dt.date) -> list[Event]:
    """Every offer whose answer is due in the range (#237).

    Muted once the application has moved on from *Offer* -- accepted, withdrawn, whatever
    it became -- because the date has been answered, one way or another.
    """
    offers = (
        Offer.objects.for_user(user)
        .select_related("application", "application__posting", "application__posting__company")
        .filter(answer_by__gte=start, answer_by__lt=end)
    )
    return [
        Event(
            kind=ANSWER,
            title=str(
                _("Answer by: %(company)s") % {"company": offer.application.posting.company.name}
            ),
            url=offer.get_absolute_url(),
            starts_at=_at_midnight(offer.answer_by),
            detail=str(offer.application.posting.title),
            muted=offer.application.status != Status.OFFER,
            all_day=True,
        )
        for offer in offers
    ]


def _closings(user, start: dt.date, end: dt.date) -> list[Event]:
    """Every posting whose closing date falls in the range.

    Including the ones already decided, muted. A listing applied to or discarded is not
    something to hurry about, and leaving it off altogether would make the month disagree
    with itself for anybody who remembers putting the date in.
    """
    postings = (
        JobPosting.objects.for_user(user)
        .with_application_count()
        .select_related("company")
        .filter(closes_at__gte=start, closes_at__lt=end)
    )
    return [
        Event(
            kind=CLOSING,
            title=str(_("Closes: %(role)s") % {"role": posting.title}),
            url=posting.get_absolute_url(),
            starts_at=_at_midnight(posting.closes_at),
            detail=str(posting.company.name),
            muted=bool(posting.application_count) or posting.closed_at is not None,
            all_day=True,
        )
        for posting in postings
    ]


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


def kinds_parameter(kinds) -> str:
    """``kinds`` as an address writes them, or empty where every kind is wanted.

    All four is the default, so spelling it out would put a parameter on every link on the
    page to say what leaving it off already says.
    """
    wanted = frozenset(kinds or ALL_KINDS)
    if wanted >= ALL_KINDS:
        return ""
    return ",".join(kind for kind, _label, _tone in KINDS if kind in wanted)


def url_for(view: str, day: dt.date, kinds=None) -> str:
    """The address of one view on one day: the month by its month, the rest by their day.

    The kinds travel with it, so *Earlier* does not quietly widen a narrowed calendar.
    """
    base = reverse("applications:calendar")
    query = f"month={day:%Y-%m}" if view == DEFAULT_VIEW else f"view={view}&on={day:%Y-%m-%d}"
    narrowed = kinds_parameter(kinds)
    if narrowed:
        query += f"&kinds={narrowed}"
    return f"{base}?{query}"


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
    #: Which kinds this page is showing. All four unless the address narrowed it (#238).
    kinds: frozenset[str] = field(default_factory=lambda: frozenset(ALL_KINDS))

    @property
    def switcher(self) -> list[tuple[str, str, str, bool]]:
        """(view, label, url, current) for each shape, on the day this page is on."""
        return [
            (view, VIEW_LABELS[view], url_for(view, self.on, self.kinds), view == self.view)
            for view in VIEWS
        ]

    @property
    def legend(self) -> list[tuple[str, object, str, bool, str]]:
        """(kind, label, tone, showing, url) for each kind, for the key that is the filter.

        One control, not two. A key that says what the colours mean and a separate row of
        checkboxes that decides what is drawn are two places to read and two places to
        disagree; here the key *is* the switch, and it is made of links, so a narrowed
        calendar is an address somebody can keep.

        The link for a kind that is showing takes it out; the link for one that is not puts
        it back. Taking out the last one would leave a calendar of nothing, so it gives back
        all four instead -- which is what its label will then offer to undo.
        """
        rows = []
        for kind, label, tone in KINDS:
            showing = kind in self.kinds
            wanted = set(self.kinds) - {kind} if showing else set(self.kinds) | {kind}
            rows.append(
                (kind, label, tone, showing, url_for(self.view, self.on, wanted or ALL_KINDS))
            )
        return rows

    @property
    def narrowed(self) -> bool:
        return self.kinds != ALL_KINDS

    @property
    def everything(self) -> str:
        """This same period with every kind back. The way out of a calendar narrowed to one."""
        return url_for(self.view, self.on, ALL_KINDS)

    @property
    def empty(self) -> bool:
        return not self.events


VIEW_LABELS = {
    "month": _("Month"),
    "week": _("Week"),
    "day": _("Day"),
    "agenda": _("Agenda"),
}


def build(user, view: str, on: dt.date, *, today: dt.date | None = None, kinds=None) -> Page:
    today = today or timezone.localdate()
    view = view if view in VIEWS else DEFAULT_VIEW
    kinds = frozenset(kinds) if kinds else frozenset(ALL_KINDS)
    if view == "month":
        start, end = month_range(on)
        events = events_between(user, start, end, kinds)
        page = Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=formats.date_format(first_of_month(on), "YEAR_MONTH_FORMAT"),
            earlier=url_for(view, previous_month(on), kinds),
            later=url_for(view, next_month(on), kinds),
            today=url_for(view, today, kinds),
            events=events,
            kinds=kinds,
        )
        page.weeks = month_grid(on, events, today=today)
        page.weekday_names = [formats.date_format(day.date, "D") for day in page.weeks[0]]
        return page
    if view == "week":
        start = week_start(on)
        end = start + dt.timedelta(days=7)
        events = events_between(user, start, end, kinds)
        page = Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=_("Week of %(day)s") % {"day": formats.date_format(start, "DATE_FORMAT")},
            earlier=url_for(view, start - dt.timedelta(days=7), kinds),
            later=url_for(view, end, kinds),
            today=url_for(view, today, kinds),
            events=events,
            kinds=kinds,
        )
        page.days = week_days(on, events, today=today)
        return page
    if view == "day":
        start, end = on, on + dt.timedelta(days=1)
        events = events_between(user, start, end, kinds)
        return Page(
            view=view,
            on=on,
            start=start,
            end=end,
            title=f"{formats.date_format(on, 'l')} {formats.date_format(on, 'DATE_FORMAT')}",
            earlier=url_for(view, on - dt.timedelta(days=1), kinds),
            later=url_for(view, end, kinds),
            today=url_for(view, today, kinds),
            events=events,
            listed=[(on, events)],
            kinds=kinds,
        )
    start, end = on, on + dt.timedelta(days=AGENDA_DAYS)
    events = events_between(user, start, end, kinds)
    return Page(
        view="agenda",
        on=on,
        start=start,
        end=end,
        title=_("%(days)s days from %(day)s")
        % {"days": AGENDA_DAYS, "day": formats.date_format(on, "DATE_FORMAT")},
        earlier=url_for("agenda", on - dt.timedelta(days=AGENDA_DAYS), kinds),
        later=url_for("agenda", end, kinds),
        today=url_for("agenda", today, kinds),
        events=events,
        listed=sorted(by_day(events).items()),
        kinds=kinds,
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
    return build(user, view, on, today=today, kinds=kinds_from(params.get("kinds", "")))
