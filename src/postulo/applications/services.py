"""Operations that touch more than one model.

Status changes and intake live here rather than in a view or a signal. A signal would
fire on fixtures, imports and admin edits, where an automatic event entry is usually
wrong; a view would mean the next view has to remember the same steps. A named function
that the caller invokes on purpose is easier to read and easier to test.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from postulo.core import slugs
from postulo.jobs.models import Company, JobPosting
from postulo.notifications import slow

from .models import (
    BOARD_STATUSES,
    END_STATUSES,
    OPEN_STATUSES,
    SENT_STATUSES,
    SETTLED_OUTCOMES,
    Application,
    ApplicationEvent,
    EndReason,
    EventKind,
    Interview,
    InterviewKind,
    InterviewOutcome,
    Offer,
    Reminder,
    Status,
)


def record_event(
    application: Application,
    *,
    kind: str = EventKind.NOTE,
    summary: str = "",
    body: str = "",
    occurred_at=None,
    from_status: str = "",
    to_status: str = "",
    actor: str = "",
    end_reason: str = "",
    interview: Interview | None = None,
) -> ApplicationEvent:
    """Append one entry to an application's timeline.

    ``actor`` names who wrote it when it was not the person themselves — an API token, an
    import — so the timeline shows what an agent did and the person can undo it by hand.
    """
    # Clip to the columns: PostgreSQL refuses what SQLite stores silently (#463).
    summary = summary[: ApplicationEvent._meta.get_field("summary").max_length]
    actor = actor[: ApplicationEvent._meta.get_field("actor").max_length]
    return ApplicationEvent.objects.create(
        application=application,
        kind=kind,
        summary=summary,
        body=body,
        occurred_at=occurred_at or timezone.now(),
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        end_reason=end_reason,
        interview=interview,
    )


def moment_for(day):
    """A date somebody typed, as the instant to record it at (#222).

    Midday in the instance's time zone, not midnight: a date written down as midnight is the
    date before it for anybody an hour west, and "I applied on the 3rd" should not become the
    2nd because of where the server is. A datetime is already an instant and is left alone;
    ``None`` means nobody said, and the caller decides what that means.
    """
    if day is None:
        return None
    if isinstance(day, dt.datetime):
        return day
    return timezone.make_aware(
        dt.datetime.combine(day, dt.time(12, 0)), timezone.get_current_timezone()
    )


@transaction.atomic
def change_status(
    application: Application,
    new_status: str,
    *,
    note: str = "",
    occurred_at=None,
    actor: str = "",
    mark_applied: bool = True,
    end_reason: str = "",
) -> ApplicationEvent | None:
    """Move an application to ``new_status`` and record why.

    Returns the event, or ``None`` if the status was already that value — re-saving a
    form should not litter the timeline with entries saying nothing changed.

    ``end_reason`` is why it ended, for a move to *Rejected*, *Withdrawn* or *Ghosted*
    (#239). It is written on the entry, so the log says why beside saying that; with any
    other status it is a mistake in the caller and raises. Given for the status the
    application already stands at, it is a reason learnt afterwards -- which is when most
    of them are learnt -- and is appended as an entry of its own: see `_say_why`.

    Two timestamps are maintained as a side effect, because deriving them from the log
    on every read would be needless work:

    ``applied_at``
        Set the first time the application reaches a status that means it was **sent** --
        any of `SENT_STATUSES`, not only the literal *Applied* -- and never moved
        afterwards. It is the date you actually applied, which is what response times are
        measured from, so recording a reply you already had (straight to *Interviewing*, or
        to *Rejected*) has to stamp it too, or the application counts nowhere (#222).

        ``occurred_at`` is the date, which is how a person says when they applied rather
        than having today assumed for them. ``mark_applied=False`` is for the one caller
        that knows the status but not the date -- a spreadsheet row with no date column --
        and would rather leave it unknown than invent today.

    ``closed_at``
        Set when the outcome is settled, and cleared if the application reopens — which
        does happen, when a company comes back weeks after a rejection.

    **The status it is moving *from* is read under a lock** (#231). It used to be read off
    the object the caller happened to be holding, which on a page is a row fetched before
    the form was drawn. Two things moving one application at the same time -- a board card
    and an API client, or two tabs -- could each read *applied*, and the second would write
    a timeline entry saying *applied → offer* over a row that already said *screening*: a
    transition that never happened, in the record whose whole job is to say what did. The
    row is re-read with `select_for_update` inside the transaction instead, so the second
    one waits and then finds what the first one wrote.

    On SQLite this changes nothing and costs one `SELECT`: writes are serialised there
    anyway. It is PostgreSQL, where they are not, that this is for.
    """
    if end_reason and new_status not in END_STATUSES:
        raise ValueError(
            f"{new_status!r} is not an ending; a reason goes with {sorted(END_STATUSES)}."
        )
    if end_reason and end_reason not in EndReason.values:
        raise ValueError(f"{end_reason!r} is not a reason; one of {sorted(EndReason.values)}.")

    # The caller's object is still the one mutated and returned -- it is what they will go
    # on to use -- but what it is moving *from* comes from the locked row.
    locked = Application.objects.select_for_update().filter(pk=application.pk).first()
    previous = locked.status if locked is not None else application.status
    application.status = previous
    if locked is not None:
        # The two derived columns are decided from the locked row as well (#543): a copy
        # fetched before another move would otherwise stamp or clear them from stale data.
        application.applied_at = locked.applied_at
        application.closed_at = locked.closed_at
    if previous == new_status:
        if end_reason:
            return _say_why(
                application, end_reason, note=note, occurred_at=occurred_at, actor=actor
            )
        return None

    application.status = new_status
    changed = ["status", "updated_at"]

    if mark_applied and new_status in SENT_STATUSES and application.applied_at is None:
        application.applied_at = occurred_at or timezone.now()
        changed.append("applied_at")

    if new_status in OPEN_STATUSES:
        if application.closed_at is not None:
            application.closed_at = None
            changed.append("closed_at")
    elif application.closed_at is None:
        application.closed_at = occurred_at or timezone.now()
        changed.append("closed_at")

    application.save(update_fields=changed)

    entry = record_event(
        application,
        kind=EventKind.STATUS_CHANGE,
        summary=str(
            _("%(old)s → %(new)s")
            % {
                "old": Status(previous).label,
                "new": Status(new_status).label,
            }
        ),
        body=note,
        occurred_at=occurred_at,
        from_status=previous,
        to_status=new_status,
        actor=actor,
        end_reason=end_reason,
    )
    # For a machine that asked: a person's own notifiers keep this off (#240).
    slow.tell(
        "status_changed",
        application.owner,
        subject=application,
        application_id=application.pk,
        event_id=entry.pk,
        role=application.posting.title,
        company=application.posting.company.name,
        from_status=previous,
        to_status=new_status,
        note=note,
        actor=actor,
    )
    return entry


def _say_why(
    application: Application, reason: str, *, note: str, occurred_at, actor: str
) -> ApplicationEvent | None:
    """Write down why an application ended, after it has (#239).

    A rejection arrives first and its reason later, in a call a week on or not at all, so
    the reason has to be sayable once the status already stands. **It is appended, never
    written into the entry that ended it**: a timeline somebody can quietly rewrite is
    worth very little, and that holds for the person's own second thoughts as much as for
    anybody else's. The entry moves the application from its status to the same status,
    which is exactly what happened, and the latest reason on the run is the one that
    stands.

    Saying again what the timeline already says writes nothing, for the reason
    `change_status` gives: a form re-sent should not leave an entry saying nothing changed.
    """
    from . import endings

    standing = endings.of(application, afresh=True)
    if standing is not None and standing.reason == reason and note in ("", standing.note):
        return None
    return record_event(
        application,
        kind=EventKind.STATUS_CHANGE,
        summary=str(Status(application.status).label),
        body=note,
        occurred_at=occurred_at,
        from_status=application.status,
        to_status=application.status,
        actor=actor,
        end_reason=reason,
    )


@transaction.atomic
def create_listing(owner, *, company: Company, posting_data: dict) -> JobPosting:
    """Add a listing: a posting the person has noticed and not yet decided about."""
    return JobPosting.objects.create(owner=owner, company=company, **posting_data)


@transaction.atomic
def apply_to_listing(
    posting: JobPosting, application_data: dict, *, actor: str = "", applied_at=None
) -> Application:
    """The decision: an application for a listing.

    The listing's derived state becomes *applied* by the mere existence of the
    application; only the moment of decision is written down.

    ``applied_at`` is when it was actually sent, which is not always today: recording a
    search already under way is the common case, and the wiki says so (#222). It is carried
    into the status change, so the timeline entry and the date agree.
    """
    application_data = dict(application_data)
    applied_at = application_data.pop("applied_at", None) or applied_at
    status = application_data.pop("status", Status.DRAFT)
    application = Application.objects.create(
        owner=posting.owner, posting=posting, status=Status.DRAFT, **application_data
    )

    record_event(
        application,
        kind=EventKind.NOTE,
        summary=str(_("Application created")),
        occurred_at=application.created_at,
        actor=actor,
    )
    if status != Status.DRAFT:
        change_status(application, status, occurred_at=applied_at, actor=actor)

    if posting.decided_at is None:
        posting.decided_at = timezone.now()
        posting.save(update_fields=["decided_at", "updated_at"])
    return application


@transaction.atomic
def create_application(
    owner,
    *,
    company: Company,
    posting_data: dict,
    application_data: dict,
    actor: str = "",
    applied_at=None,
):
    """Record a listing and an application for it in one step.

    Applications are almost always entered while looking at a posting, so asking someone
    to add a listing and then apply to it would be two forms for one thought. Underneath
    it is exactly those two steps, so the data is the same whichever door was used.
    """
    posting = create_listing(owner, company=company, posting_data=posting_data)
    return apply_to_listing(posting, application_data, actor=actor, applied_at=applied_at)


def get_or_create_company(owner, name: str, *, wikidata: str = "") -> Company:
    """Find this owner's company by name, case-insensitively, or add it.

    Matching loosely on the way in avoids ending up with "Acme", "acme" and "ACME" as
    three separate employers after a few weeks of typing. A Wikidata id, when the caller
    has one, is a stronger match than any spelling: the company carrying it is the one,
    whatever it is called here. A company found or made by name gains the id if it has
    none yet and nobody else's record holds it.
    """
    from postulo.jobs import identifiers
    from postulo.jobs.models import CompanyIdentifier

    name = name.strip()
    if wikidata:
        by_id = Company.by_identifier(owner, identifiers.WIKIDATA, wikidata)
        if by_id is not None:
            return by_id
    company = Company.objects.for_user(owner).filter(name_key=slugs.name_key(name)).first()
    if company is None:
        try:
            # A savepoint, so a concurrent request that made the same company first
            # leaves this one a row to read rather than a broken transaction.
            with transaction.atomic():
                company = Company.objects.create(owner=owner, name=name)
        except IntegrityError:
            company = Company.objects.for_user(owner).filter(name_key=slugs.name_key(name)).first()
            if company is None:
                raise
    if wikidata and not company.identifiers.filter(scheme=identifiers.WIKIDATA).exists():
        try:
            value = identifiers.clean(identifiers.WIKIDATA, wikidata)
        except ValidationError:
            return company
        CompanyIdentifier.objects.get_or_create(
            owner=owner, scheme=identifiers.WIKIDATA, value=value, defaults={"company": company}
        )
    return company


# ------------------------------------------------------------------- interviews

#: An interview entered without an end lasts this long.
DEFAULT_INTERVIEW_LENGTH = dt.timedelta(hours=1)

#: How far ahead of an interview its reminder falls due.
INTERVIEW_REMINDER_LEAD = dt.timedelta(days=1)

#: Where an application has got to once an interview of each kind has been held. Anything
#: not listed means interviews proper.
STATUS_AFTER_INTERVIEW = {
    InterviewKind.PHONE: Status.SCREENING,
    InterviewKind.ASSESSMENT: Status.ASSESSMENT,
}


def _when(moment) -> str:
    """A date and time the way the timeline prints them, in the active time zone."""
    return formats.date_format(timezone.localtime(moment), "DATETIME_FORMAT")


def _interview_reminder_summary(interview: Interview) -> str:
    """What the reminder for an interview says. Built from the interview, every time.

    It names the time, so it has to be rebuilt whenever the interview moves. It was worded
    once at booking and never again, and `reschedule_interview` moved only `due_at` -- so an
    interview put back by two hours produced a reminder that arrived at the right moment
    naming the wrong one, which is worse than not arriving (#224).
    """
    return str(
        _("Interview tomorrow: %(kind)s at %(company)s, %(time)s")
        % {
            "kind": interview.get_kind_display(),
            "company": interview.application.posting.company.name,
            "time": formats.date_format(timezone.localtime(interview.starts_at), "TIME_FORMAT"),
        }
    )


@transaction.atomic
def schedule_interview(
    application: Application,
    *,
    kind: str,
    starts_at,
    ends_at=None,
    location: str = "",
    notes: str = "",
    contacts=(),
    remind: bool = True,
    actor: str = "",
    uid: str = "",
) -> Interview:
    """Put an interview in the diary, on the timeline, and — the day before — in the reminders.

    One that is already over when it is entered, because the person forgot to schedule
    it, is recorded as held straight away: the timeline then reads as it would have had
    they remembered, and there is nothing to remind them of.

    ``uid`` is the calendar identifier it is kept under, for a meeting that came out of a
    calendar file and so keeps the name that file gave it (#661); the column mints one when
    there is none.
    """
    ends_at = ends_at or starts_at + DEFAULT_INTERVIEW_LENGTH
    interview = Interview.objects.create(
        **({"uid": uid} if uid else {}),
        owner=application.owner,
        application=application,
        kind=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        location=location,
        notes=notes,
    )
    if contacts:
        interview.contacts.set(contacts)

    now = timezone.now()
    if ends_at < now:
        return settle_interview(interview, InterviewOutcome.DONE, actor=actor)

    record_event(
        application,
        kind=EventKind.INTERVIEW_SCHEDULED,
        summary=str(
            _("%(kind)s scheduled for %(when)s")
            % {"kind": interview.get_kind_display(), "when": _when(starts_at)}
        ),
        body=location,
        occurred_at=now,
        actor=actor,
    )

    _tell_interview(interview, moved=False, actor=actor)
    due = starts_at - INTERVIEW_REMINDER_LEAD
    if remind and due > now:
        interview.reminder = Reminder.objects.create(
            owner=application.owner,
            application=application,
            summary=_interview_reminder_summary(interview),
            due_at=due,
        )
        interview.save(update_fields=["reminder", "updated_at"])
    return interview


@transaction.atomic
def reschedule_interview(interview: Interview, *, starts_at, ends_at, actor: str = "") -> Interview:
    """Move an interview, and its reminder with it."""
    if starts_at == interview.starts_at and ends_at == interview.ends_at:
        return interview
    previous = interview.starts_at
    interview.starts_at, interview.ends_at = starts_at, ends_at
    interview.save(update_fields=["starts_at", "ends_at", "updated_at"])

    record_event(
        interview.application,
        kind=EventKind.INTERVIEW_SCHEDULED,
        summary=str(
            _("%(kind)s moved from %(old)s to %(new)s")
            % {
                "kind": interview.get_kind_display(),
                "old": _when(previous),
                "new": _when(starts_at),
            }
        ),
        actor=actor,
    )

    _tell_interview(interview, moved=True, actor=actor)
    reminder = interview.reminder
    if reminder is not None and not reminder.is_done:
        due = starts_at - INTERVIEW_REMINDER_LEAD
        if due > timezone.now():
            # Announced again at the new time, even if the old one had already been -- and
            # worded again, because the words name the time (#224).
            reminder.due_at, reminder.notified_at = due, None
            reminder.summary = _interview_reminder_summary(interview)
            reminder.save(update_fields=["due_at", "notified_at", "summary", "updated_at"])
        else:
            reminder.complete()
    return interview


#: One whole sentence per kind of interview, because the noun's case and article are the
#: catalogue's to decide, not Python's (#392): lower-casing a translated label flattens an
#: acronym and misspells a German noun.
_NO_SHOW = {
    InterviewKind.PHONE: _("Nobody showed up for the phone screen"),
    InterviewKind.VIDEO: _("Nobody showed up for the video call"),
    InterviewKind.ONSITE: _("Nobody showed up for the on-site interview"),
    InterviewKind.PANEL: _("Nobody showed up for the panel"),
    InterviewKind.ASSESSMENT: _("Nobody showed up for the assessment or test"),
    InterviewKind.OTHER: _("Nobody showed up for the interview"),
}


def _no_show_sentence(value: str, label):
    """The no-show entry for an interview of kind ``value``; an unlisted kind gets the
    generic sentence, with its label as written."""
    sentence = _NO_SHOW.get(value)
    if sentence is not None:
        return sentence
    return _("Nobody showed up for the interview: %(kind)s") % {"kind": label}


@transaction.atomic
def settle_interview(
    interview: Interview, outcome: str, *, note: str = "", actor: str = ""
) -> Interview:
    """Record how an interview went: held, cancelled, or the other side never came.

    Holding one moves the application forward if the status had not kept up — through
    ``change_status``, so the timeline says so — and never moves one that is settled: an
    interview remembered after a rejection does not reopen the application.
    """
    if outcome not in SETTLED_OUTCOMES:
        raise ValueError(f"{outcome!r} is not an outcome; one of {sorted(SETTLED_OUTCOMES)}.")
    if interview.outcome == outcome:
        return interview
    interview.outcome = outcome
    interview.save(update_fields=["outcome", "updated_at"])

    application = interview.application
    kind = interview.get_kind_display()
    if outcome == InterviewOutcome.DONE:
        record_event(
            application,
            kind=EventKind.INTERVIEW,
            summary=str(_("%(kind)s held") % {"kind": kind}),
            body=note,
            occurred_at=interview.starts_at,
            actor=actor,
            interview=interview,
        )
        _catch_up(application, interview.kind, occurred_at=interview.starts_at, actor=actor)
    elif outcome == InterviewOutcome.CANCELLED:
        record_event(
            application,
            kind=EventKind.INTERVIEW_CANCELLED,
            summary=str(
                _("%(kind)s on %(when)s cancelled")
                % {"kind": kind, "when": _when(interview.starts_at)}
            ),
            body=note,
            actor=actor,
            interview=interview,
        )
    else:
        record_event(
            application,
            kind=EventKind.INTERVIEW_NO_SHOW,
            summary=str(_no_show_sentence(interview.kind, kind)),
            body=note,
            occurred_at=interview.starts_at,
            actor=actor,
            interview=interview,
        )

    reminder = interview.reminder
    if reminder is not None and not reminder.is_done:
        reminder.complete()
    return interview


def _catch_up(application: Application, kind: str, *, occurred_at, actor: str) -> None:
    """Move the status to where a held interview of ``kind`` puts it, if it is behind."""
    target = STATUS_AFTER_INTERVIEW.get(kind, Status.INTERVIEWING)
    order = list(BOARD_STATUSES)
    if application.status in order and order.index(application.status) < order.index(target):
        change_status(application, target, occurred_at=occurred_at, actor=actor)


# ------------------------------------------------------------------- reminders

#: What *Later* offers without asking for a date. Two, because a list of six is a decision
#: and the point of the control is not having to make one; anything else is *a date*.
LATER_TOMORROW = "tomorrow"
LATER_NEXT_WEEK = "next_week"

#: How far out each of them moves the reminder. A week rather than seven days from the
#: original: somebody pressing *Later* on a reminder that fell due last Tuesday means a week
#: from now, not last Tuesday plus seven.
LATER_DAYS = {LATER_TOMORROW: 1, LATER_NEXT_WEEK: 7}


def later_time(choice: str, reminder: Reminder, *, now=None) -> dt.datetime | None:
    """When ``tomorrow`` or ``next week`` falls, keeping the time of day it was set for.

    The day is measured from the person's today and not from the due time, and the hour
    is the reminder's own, in their zone: a reminder to ring somebody at ten was set for
    ten on purpose, and a postponement is a change of day. Adding days to the instant
    would move the local hour across a clock change, so the date is added and the wall
    time put back. A time a change of clocks skips lands after the gap, and one it
    repeats takes its first occurrence (`fold=0`, which is what both rules ask for).
    It is cut to the minute, which is all the edit form shows and posts, so the stored time and
    the displayed one agree (#445).
    """
    days = LATER_DAYS.get(choice)
    if days is None:
        return None
    zone = timezone.get_current_timezone()
    today = timezone.localtime(now or timezone.now(), zone).date()
    wall = (
        timezone.localtime(reminder.due_at, zone)
        .timetz()
        .replace(second=0, microsecond=0, tzinfo=None)
    )
    moved = dt.datetime.combine(today + dt.timedelta(days=days), wall, tzinfo=zone)
    return moved.astimezone(dt.UTC).astimezone(zone)


def postpone_reminder(reminder: Reminder, due: dt.datetime) -> Reminder:
    """Move a reminder to ``due``, and let it be announced again there (#238).

    The stamp has to go with the time. It is the record of *this one has been dealt with*,
    and a reminder somebody has deliberately moved into the future has not been: leaving
    `notified_at` set would move the reminder and silence it, which is the failure that
    looks most like the feature working. `reschedule_interview` has cleared it for the same
    reason since #224; this is that rule with a name.

    A reminder already done is left alone. Postponing something that is finished is either a
    mistake or a request to reopen it, and reopening is not what a button called *Later*
    should quietly do.
    """
    if reminder.is_done:
        return reminder
    reminder.due_at, reminder.notified_at = due, None
    reminder.save(update_fields=["due_at", "notified_at", "updated_at"])
    return reminder


# ---------------------------------------------------------------------- offers

#: How far ahead of the answer-by date its reminder falls due.
ANSWER_REMINDER_LEAD = dt.timedelta(days=1)


def _tell_interview(interview: Interview, *, moved: bool, actor: str) -> None:
    """The interview, for a machine that asked (#240)."""
    application = interview.application
    slow.tell(
        "interview_scheduled",
        application.owner,
        subject=application,
        interview_id=interview.pk,
        application_id=application.pk,
        role=application.posting.title,
        company=application.posting.company.name,
        interview_kind=interview.kind,
        starts_at=interview.starts_at.isoformat(),
        ends_at=interview.ends_at.isoformat(),
        moved=moved,
        actor=actor,
    )


def _tell_offer(offer: Offer, entry, *, revised: bool, actor: str) -> None:
    """The offer, for a machine that asked (#240)."""
    application = offer.application
    slow.tell(
        "offer_recorded",
        application.owner,
        subject=application,
        offer_id=offer.pk,
        event_id=entry.pk,
        application_id=application.pk,
        role=application.posting.title,
        company=application.posting.company.name,
        terms=offer.terms,
        revised=revised,
        actor=actor,
    )


def _offer_summary(offer: Offer, revised: bool) -> str:
    what = _("Offer revised") if revised else _("Offer received")
    return str(f"{what}: {offer.terms}" if offer.terms else what)


def _answer_reminder_summary(offer: Offer) -> str:
    return str(
        _("Answer %(company)s about their offer by %(day)s")
        % {
            "company": offer.application.posting.company.name,
            "day": formats.date_format(offer.answer_by, "DATE_FORMAT"),
        }
    )


def _remind_to_answer(offer: Offer) -> None:
    """A reminder the day before the answer is due, kept in step with the date (#237).

    Made when the date is set, moved when it moves, ticked off when it is taken away or has
    already passed. Through `postpone_reminder`, so a moved date is announced again at its
    new time rather than silenced by the stamp from the old one. Called only when the date
    is what changed: a reminder that was ticked off, put off or deleted is the person's
    answer to it, and an edit to something else must not undo that (#442).
    """
    reminder = offer.reminder
    if offer.answer_by is None:
        if reminder is not None and not reminder.is_done:
            reminder.complete()
        return
    due = timezone.make_aware(
        dt.datetime.combine(offer.answer_by - ANSWER_REMINDER_LEAD, dt.time(9, 0)),
        timezone.get_current_timezone(),
    )
    if due <= timezone.now():
        if reminder is not None and not reminder.is_done:
            reminder.complete()
        return
    if reminder is None:
        offer.reminder = Reminder.objects.create(
            owner=offer.owner,
            application=offer.application,
            summary=_answer_reminder_summary(offer),
            due_at=due,
        )
        offer.save(update_fields=["reminder", "updated_at"])
    else:
        reminder.summary = _answer_reminder_summary(offer)
        reminder.save(update_fields=["summary", "updated_at"])
        if reminder.is_done:
            reminder.done_at = None
            reminder.save(update_fields=["done_at", "updated_at"])
        postpone_reminder(reminder, due)


@transaction.atomic
def record_offer(application: Application, *, actor: str = "", **fields) -> Offer:
    """Record what was offered: the row, the timeline entry, the reminder, and the status.

    The status moves to *Offer* if the application is open and had not got there -- through
    `change_status`, so the timeline says so -- and is left alone if it is already there or
    past it. An offer recorded on an application already accepted is a revision of terms,
    not a step backwards.
    """
    offer = Offer.objects.create(owner=application.owner, application=application, **fields)
    entry = record_event(
        application, kind=EventKind.OFFER, summary=_offer_summary(offer, revised=False), actor=actor
    )
    _remind_to_answer(offer)
    _tell_offer(offer, entry, revised=False, actor=actor)
    order = list(BOARD_STATUSES)
    if application.status in order and order.index(application.status) < order.index(Status.OFFER):
        change_status(application, Status.OFFER, actor=actor)
    return offer


#: What an offer says that a revision is about. Its notes are the person's own and say
#: nothing new about the offer (#442).
OFFER_TERMS = frozenset(
    {
        "base_amount",
        "currency",
        "period",
        "variable_pay",
        "equity",
        "benefits",
        "location",
        "holidays",
        "starts_on",
        "answer_by",
    }
)


@transaction.atomic
def withdraw_offer(offer: Offer) -> None:
    """Delete an offer and tick off its answer-by reminder (#444)."""
    reminder = offer.reminder
    if reminder is not None and not reminder.is_done:
        reminder.complete()
    offer.delete()


@transaction.atomic
def revise_offer(offer: Offer, *, changed=None, actor: str = "") -> Offer:
    """After an offer's row has been edited: the revision on the timeline, the reminder moved.

    ``changed`` names the fields that were edited. An edit that touched none of the terms
    -- the notes, or nothing at all -- is not a revision, so it writes nothing, tells nobody
    and leaves the reminder alone; the reminder follows only a moved ``answer_by``. Without
    it everything is taken to have changed.
    """
    if changed is not None and not OFFER_TERMS & set(changed):
        return offer
    entry = record_event(
        offer.application,
        kind=EventKind.OFFER,
        summary=_offer_summary(offer, revised=True),
        actor=actor,
    )
    if changed is None or "answer_by" in changed:
        _remind_to_answer(offer)
    _tell_offer(offer, entry, revised=True, actor=actor)
    return offer
