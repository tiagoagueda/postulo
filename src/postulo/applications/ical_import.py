"""Putting what an iCalendar file holds into a search, after a review (#661).

**A review, not an action.** A file is read by `ical_reader` and what it held -- plain
values, never the file -- waits in the session. The page lists each event and task with
what it would become, and nothing is made until the person has chosen for each one and
pressed the button. What the page shows is worked out when it is drawn, against the account
as it stands, and worked out again when the button is pressed.

**Each entry becomes one of three things, by choice.** A timed event can be an interview, on
an application the person picks, through `schedule_interview`, which is how an interview
gets its timeline entry and its reminder. An event or a task can be a reminder, on an
application or on none. Or it is left out. An all-day event is offered a reminder only: an
application's deadline is one date per application, and an import should not overwrite it.

**Time is never guessed.** An entry whose time has no zone Postulo could use -- a floating
time, a zone nobody knows -- is shown with a menu asking for one, which is required before
it can become anything. A date alone is a day, and a reminder on a day is due at
`DAY_REMINDER_HOUR` in the person's own zone: the hour is the reminder's and not the
event's.

**The same file twice adds nothing.** What is made keeps the identifier the file gave it,
or one derived from it (`ical_reader.stored_uid`), and an entry whose identifier an
interview or a reminder of this person already holds is shown as already there.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import available_timezones

from django import forms
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .ical_reader import NO_DATE, RECURS, UNREADABLE, Entry, aware, stored_uid
from .models import Application, Interview, InterviewKind, Reminder
from .services import DEFAULT_INTERVIEW_LENGTH, schedule_interview

#: Held in the session between reading a file and confirming it.
SESSION_KEY = "ical_file"

#: When a reminder for a whole day falls due, in the person's own zone.
DAY_REMINDER_HOUR = 9

LEAVE_OUT = ""
AS_INTERVIEW = "interview"
AS_REMINDER = "reminder"

ADD = "add"
PRESENT = "present"
CANNOT = "cannot"

REASONS = {
    RECURS: gettext_lazy(
        "It repeats. Postulo does not read recurring events: add the occurrences that matter "
        "by hand."
    ),
    NO_DATE: gettext_lazy("It has no date, so there is nothing to put on a day."),
    UNREADABLE: gettext_lazy("Its date could not be read."),
}


def hold(request, filename: str, entries: list[Entry], notes: list[str]) -> None:
    """Keep what was read for the review page. Plain values only: a session is JSON."""
    request.session[SESSION_KEY] = {
        "filename": filename[:120],
        "notes": notes,
        "entries": [entry.to_session() for entry in entries],
    }


def forget(request) -> None:
    request.session.pop(SESSION_KEY, None)


def held(request) -> dict | None:
    """What is waiting in this session: ``{"filename", "notes", "entries"}``, or nothing.

    Nothing as well for a session written by something else: it is not worth an error page.
    """
    raw = request.session.get(SESSION_KEY)
    try:
        entries = [Entry.from_session(item) for item in raw["entries"]]
        return {
            "filename": str(raw["filename"]),
            "notes": [str(note) for note in raw["notes"]],
            "entries": entries,
        }
    except (KeyError, TypeError, ValueError):
        forget(request)
        return None


def zone_choices() -> list:
    """Every zone, grouped as the settings menu groups them, with nothing chosen for them.

    The blank choice of the settings menu says which zone it stands for, the instance's own.
    That is the guess this page exists not to make.
    """
    from postulo.accounts.forms import time_zone_choices

    return [("", _("Choose a time zone")), *time_zone_choices()[1:]]


class RowForm(forms.Form):
    """What one entry becomes. Bound to the entry it is for, and to nobody else's records."""

    action = forms.ChoiceField(label=gettext_lazy("Add it as"), required=False)
    application = forms.ModelChoiceField(
        label=gettext_lazy("Application"),
        queryset=Application.objects.none(),
        required=False,
        empty_label=gettext_lazy("None"),
    )
    kind = forms.ChoiceField(
        label=gettext_lazy("Type of interview"),
        choices=InterviewKind.choices,
        initial=InterviewKind.VIDEO,
        required=False,
    )
    zone = forms.ChoiceField(label=gettext_lazy("Time zone"), required=False)

    def __init__(self, *args, user, entry: Entry, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry = entry
        choices = [(LEAVE_OUT, _("Leave it out"))]
        interviewable = entry.is_moment and entry.kind == "event"
        if interviewable:
            choices.append((AS_INTERVIEW, _("An interview")))
        else:
            del self.fields["kind"]
        choices.append((AS_REMINDER, _("A reminder")))
        self.fields["action"].choices = choices
        self.fields["application"].queryset = Application.objects.for_user(user).with_display_data()
        if entry.needs_zone:
            self.fields["zone"].choices = zone_choices()
        else:
            del self.fields["zone"]

    def clean(self) -> dict:
        data = super().clean()
        action = data.get("action") or LEAVE_OUT
        if action == LEAVE_OUT:
            return data
        if action == AS_INTERVIEW and not data.get("application"):
            self.add_error("application", _("An interview belongs to an application. Choose one."))
        if self.entry.needs_zone:
            zone = data.get("zone") or ""
            if not zone:
                self.add_error(
                    "zone",
                    _("The file does not say which time zone this is in. Choose one."),
                )
            elif zone not in available_timezones():
                self.add_error("zone", _("Postulo does not know that time zone."))
        return data

    def moment(self) -> dt.datetime | None:
        """The time this entry is at, once the person has said which zone a floating one is in."""
        entry = self.entry
        if not entry.needs_zone:
            return entry.start
        return aware(entry.local, self.cleaned_data["zone"])

    def end(self) -> dt.datetime | None:
        """The time it ends, if the file said, in the same zone as the start."""
        entry = self.entry
        if not entry.needs_zone:
            return entry.end
        if entry.local_end is None:
            return None
        return aware(entry.local_end, self.cleaned_data["zone"])


@dataclass
class Row:
    """One entry of the file, and what the page can say about it."""

    index: int
    entry: Entry
    outcome: str
    note: str = ""
    form: RowForm | None = None

    @property
    def why_zone(self) -> str:
        """Why a time is asked about: said in words, never left for the person to guess."""
        entry = self.entry
        if not entry.needs_zone:
            return ""
        if entry.zone:
            return _("The file names a time zone, “%(zone)s”, that Postulo does not know.") % {
                "zone": entry.zone
            }
        return _("The file gives this time without a time zone.")


def _already_held(user, entries: list[Entry]) -> set[str]:
    wanted = {stored_uid(entry.uid) for entry in entries if entry.uid}
    if not wanted:
        return set()
    found = set(
        Interview.objects.for_user(user).filter(uid__in=wanted).values_list("uid", flat=True)
    )
    found |= set(
        Reminder.objects.for_user(user).filter(uid__in=wanted).values_list("uid", flat=True)
    )
    return found


def plan(user, entries: list[Entry], data=None) -> list[Row]:
    """A row for every entry: what it would become, or why it cannot be.

    ``data`` is the posted form, when there is one. Every form is built against the
    requesting user, so an application that is not theirs is not a choice that is valid.
    """
    present = _already_held(user, entries)
    rows = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        uid = stored_uid(entry.uid) if entry.uid else ""
        if entry.refusal:
            rows.append(Row(index, entry, CANNOT, str(REASONS.get(entry.refusal, ""))))
        elif uid and uid in present:
            rows.append(Row(index, entry, PRESENT))
        elif uid and uid in seen:
            # Two entries cannot both be kept under one name, and the second is not a second
            # thing: a calendar that repeats an identifier is saying it is the same event.
            note = _("The file gives this the same identifier as an entry above it.")
            rows.append(Row(index, entry, CANNOT, note))
        else:
            seen.add(uid)
            note = _("The file marks this as cancelled.") if entry.cancelled else ""
            form = RowForm(data, prefix=f"r{index}", user=user, entry=entry)
            rows.append(Row(index, entry, ADD, note, form))
    return rows


def adds(rows: list[Row]) -> bool:
    return any(row.outcome == ADD for row in rows)


@dataclass
class Report:
    interviews: int = 0
    reminders: int = 0

    @property
    def total(self) -> int:
        return self.interviews + self.reminders


def _day_due(day: dt.date) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(day, dt.time(DAY_REMINDER_HOUR)))


def apply(user, rows: list[Row], filename: str) -> Report:
    """Make what was chosen. Every form is valid; the caller has asked."""
    report = Report()
    actor = _("the file %(name)s") % {"name": filename}
    with transaction.atomic():
        for row in rows:
            if row.outcome != ADD:
                continue
            form, entry = row.form, row.entry
            action = form.cleaned_data.get("action") or LEAVE_OUT
            if action == LEAVE_OUT:
                continue
            uid = stored_uid(entry.uid) if entry.uid else ""
            if action == AS_INTERVIEW:
                starts_at = form.moment()
                ends_at = form.end()
                if ends_at is not None and ends_at <= starts_at:
                    ends_at = None
                if ends_at is not None and ends_at - starts_at > dt.timedelta(days=2):
                    ends_at = starts_at + DEFAULT_INTERVIEW_LENGTH
                notes = "\n\n".join(part for part in (entry.summary, entry.description) if part)
                schedule_interview(
                    form.cleaned_data["application"],
                    kind=form.cleaned_data.get("kind") or InterviewKind.VIDEO,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    location=entry.location,
                    notes=notes,
                    actor=actor,
                    uid=uid,
                )
                report.interviews += 1
            else:
                due = _day_due(entry.day) if entry.day is not None else form.moment()
                Reminder.objects.create(
                    owner=user,
                    application=form.cleaned_data.get("application"),
                    summary=entry.summary or _("(untitled)"),
                    due_at=due,
                    done_at=timezone.now() if entry.completed else None,
                    **({"uid": uid} if uid else {}),
                )
                report.reminders += 1
    return report
