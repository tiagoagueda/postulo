"""Operations that touch more than one model.

Status changes and intake live here rather than in a view or a signal. A signal would
fire on fixtures, imports and admin edits, where an automatic event entry is usually
wrong; a view would mean the next view has to remember the same steps. A named function
that the caller invokes on purpose is easier to read and easier to test.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from postulo.jobs.models import Company, JobPosting

from .models import (
    BOARD_STATUSES,
    OPEN_STATUSES,
    SETTLED_OUTCOMES,
    Application,
    ApplicationEvent,
    EventKind,
    Interview,
    InterviewKind,
    InterviewOutcome,
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
) -> ApplicationEvent:
    """Append one entry to an application's timeline.

    ``actor`` names who wrote it when it was not the person themselves — an API token, an
    import — so the timeline shows what an agent did and the person can undo it by hand.
    """
    return ApplicationEvent.objects.create(
        application=application,
        kind=kind,
        summary=summary,
        body=body,
        occurred_at=occurred_at or timezone.now(),
        from_status=from_status,
        to_status=to_status,
        actor=actor,
    )


@transaction.atomic
def change_status(
    application: Application,
    new_status: str,
    *,
    note: str = "",
    occurred_at=None,
    actor: str = "",
) -> ApplicationEvent | None:
    """Move an application to ``new_status`` and record why.

    Returns the event, or ``None`` if the status was already that value — re-saving a
    form should not litter the timeline with entries saying nothing changed.

    Two timestamps are maintained as a side effect, because deriving them from the log
    on every read would be needless work:

    ``applied_at``
        Set the first time the application reaches "applied", and never moved
        afterwards. It is the date you actually applied, which is what response times
        are measured from.

    ``closed_at``
        Set when the outcome is settled, and cleared if the application reopens — which
        does happen, when a company comes back weeks after a rejection.
    """
    previous = application.status
    if previous == new_status:
        return None

    application.status = new_status
    changed = ["status", "updated_at"]

    if new_status == Status.APPLIED and application.applied_at is None:
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

    return record_event(
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
    )


@transaction.atomic
def create_listing(owner, *, company: Company, posting_data: dict) -> JobPosting:
    """Add a listing: a posting the person has noticed and not yet decided about."""
    return JobPosting.objects.create(owner=owner, company=company, **posting_data)


@transaction.atomic
def apply_to_listing(
    posting: JobPosting, application_data: dict, *, actor: str = ""
) -> Application:
    """The decision: an application for a listing.

    The listing's derived state becomes *applied* by the mere existence of the
    application; only the moment of decision is written down.
    """
    application_data = dict(application_data)
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
        change_status(application, status, actor=actor)

    if posting.decided_at is None:
        posting.decided_at = timezone.now()
        posting.save(update_fields=["decided_at", "updated_at"])
    return application


@transaction.atomic
def create_application(
    owner, *, company: Company, posting_data: dict, application_data: dict, actor: str = ""
):
    """Record a listing and an application for it in one step.

    Applications are almost always entered while looking at a posting, so asking someone
    to add a listing and then apply to it would be two forms for one thought. Underneath
    it is exactly those two steps, so the data is the same whichever door was used.
    """
    posting = create_listing(owner, company=company, posting_data=posting_data)
    return apply_to_listing(posting, application_data, actor=actor)


def get_or_create_company(owner, name: str) -> Company:
    """Find this owner's company by name, case-insensitively, or add it.

    Matching loosely on the way in avoids ending up with "Acme", "acme" and "ACME" as
    three separate employers after a few weeks of typing.
    """
    name = name.strip()
    existing = Company.objects.for_user(owner).filter(name__iexact=name).first()
    if existing is not None:
        return existing
    return Company.objects.create(owner=owner, name=name)


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
    return formats.date_format(timezone.localtime(moment), "j M Y, H:i")


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
) -> Interview:
    """Put an interview in the diary, on the timeline, and — the day before — in the reminders.

    One that is already over when it is entered, because the person forgot to schedule
    it, is recorded as held straight away: the timeline then reads as it would have had
    they remembered, and there is nothing to remind them of.
    """
    ends_at = ends_at or starts_at + DEFAULT_INTERVIEW_LENGTH
    interview = Interview.objects.create(
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

    due = starts_at - INTERVIEW_REMINDER_LEAD
    if remind and due > now:
        interview.reminder = Reminder.objects.create(
            owner=application.owner,
            application=application,
            summary=str(
                _("Interview tomorrow: %(kind)s at %(company)s, %(time)s")
                % {
                    "kind": interview.get_kind_display(),
                    "company": application.posting.company.name,
                    "time": formats.date_format(timezone.localtime(starts_at), "H:i"),
                }
            ),
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

    reminder = interview.reminder
    if reminder is not None and not reminder.is_done:
        due = starts_at - INTERVIEW_REMINDER_LEAD
        if due > timezone.now():
            # Announced again at the new time, even if the old one had already been.
            reminder.due_at, reminder.notified_at = due, None
            reminder.save(update_fields=["due_at", "notified_at", "updated_at"])
        else:
            reminder.complete()
    return interview


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
        )
    else:
        record_event(
            application,
            kind=EventKind.INTERVIEW,
            summary=str(_("Nobody showed up for the %(kind)s") % {"kind": str(kind).lower()}),
            body=note,
            occurred_at=interview.starts_at,
            actor=actor,
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
