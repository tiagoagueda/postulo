"""iCalendar text for the dated things of a search, written by hand.

RFC 5545 is a large document, but the part this needs — an event with a start, an end, a
place, a description and some attendees — fits in a page, and every calendar application
imports it. A dependency would bring the rest of the standard along for nothing. Times are
written in UTC, which every reader understands and no time-zone table can get wrong.

**Two shapes, because there are two kinds of thing.** An interview is a meeting at an hour.
An application's deadline and a listing's closing date are *days*: nobody knows the hour an
employer stops reading, and writing one would be inventing a fact. RFC 5545 has exactly this
distinction — ``DTSTART;VALUE=DATE`` — and a calendar draws it along the top of the day
rather than at a time, which is where it belongs (#238).

**A day entry's identity comes from its address.** An interview carries a `uid` column
because it is pushed into other people's calendars and has to keep its name across edits.
A deadline has no such column, and a UID built from a primary key alone would collide
between two Postulo instances in the same calendar application. The absolute URL of the
thing is already unique per instance and stable for its life, so the UID is a UUID over
that — no migration, no collision, and the same entry updates in place rather than
doubling when the feed is fetched again.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext as _

from postulo import __version__

from .models import Interview, InterviewOutcome

PRODID = f"-//Postulo//Postulo {__version__}//EN"

#: RFC 5545 folds content lines at 75 octets; continuation lines start with one space.
LINE_OCTETS = 75

STATUS_OF = {
    InterviewOutcome.SCHEDULED: "CONFIRMED",
    InterviewOutcome.DONE: "CONFIRMED",
    InterviewOutcome.NO_SHOW: "CONFIRMED",
    InterviewOutcome.CANCELLED: "CANCELLED",
}


#: Everything below a space, and DEL. A calendar file is a list of lines, so a control
#: character in a value somebody typed is not a curiosity: a carriage return ends the
#: property there and whatever follows is read as a property of its own, in every calendar
#: that subscribes to the feed. Nothing in this set carries meaning worth keeping in an
#: interview's note, a place or a person's name, so it all goes (#218).
CONTROLS = frozenset(chr(code) for code in range(0x20)) | {"\x7f"}


def without_controls(text: str) -> str:
    """``text`` with nothing in it that could end a line or start a property."""
    return "".join(ch for ch in text if ch not in CONTROLS)


def escape(text: str) -> str:
    """Text as a property value: backslashes, semicolons, commas and newlines escaped."""
    # A lone carriage return is a line break as much as a CRLF pair is, and old Mac text
    # still arrives with one. Folding it in first means the escaping below cannot leave a
    # real one behind to end the line early.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return without_controls(
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def parameter(text: str) -> str:
    """Text as a parameter value, quoted when it holds anything the grammar reserves.

    The quotation mark is dropped rather than escaped because RFC 5545 gives no way to
    write one inside a quoted string. The control characters go for the reason `CONTROLS`
    states: this value lands mid-line in ``ATTENDEE;CN=``, where a line break would let a
    contact's name add properties to somebody else's calendar.
    """
    text = without_controls(text.replace('"', ""))
    if any(ch in text for ch in ",;:"):
        return f'"{text}"'
    return text


def stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def fold(line: str) -> list[str]:
    """Split one content line so no piece exceeds the octet limit, never mid-character."""
    pieces: list[str] = []
    current, budget = "", LINE_OCTETS
    for char in line:
        width = len(char.encode("utf-8"))
        if len(current.encode("utf-8")) + width > budget:
            pieces.append(current)
            current, budget = " ", LINE_OCTETS
        current += char
    pieces.append(current)
    return pieces


def calendar_status(interview: Interview) -> str:
    """What RFC 5545 calls this interview's outcome: ``CONFIRMED`` or ``CANCELLED``.

    A plugin pushing an interview to a calendar has to write the same word Postulo would,
    and a plugin holding its own table of four outcomes is a table that goes stale the day
    a fifth is added (#229).
    """
    return STATUS_OF[interview.outcome]


def alarm_lines(interview: Interview) -> list[str]:
    """The VALARM for this interview's reminder, or nothing.

    Only a reminder that is still outstanding and falls before the meeting: one already
    done is not something to be woken for, and one after the meeting is not a warning about
    it. The trigger is written as a lead time rather than an absolute moment, so moving the
    event in a calendar moves the alarm with it.
    """
    reminder = getattr(interview, "reminder", None)
    if reminder is None or reminder.is_done or reminder.due_at >= interview.starts_at:
        return []
    minutes = int((interview.starts_at - reminder.due_at).total_seconds() // 60)
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape(reminder.summary)}",
        f"TRIGGER:-PT{minutes}M",
        "END:VALARM",
    ]


def event_lines(interview: Interview, *, url: str = "", alarm: bool = False) -> list[str]:
    """The VEVENT for one interview, with its reminder as an alarm if ``alarm``.

    The feed Postulo serves carries no alarm: a subscription that rings is a decision for
    whoever subscribes, and their calendar offers it. A sync plugin writing the event *into*
    somebody's own calendar is a different matter -- the reminder is theirs and they set it
    here -- so it asks for one (#229).
    """
    application = interview.application
    posting = application.posting
    summary = _("%(kind)s: %(title)s at %(company)s") % {
        "kind": interview.get_kind_display(),
        "title": posting.title,
        "company": posting.company.name,
    }

    description: list[str] = []
    if interview.notes:
        description.append(interview.notes)
    people = list(interview.contacts.all())
    if people:
        description.append(
            _("With: %(people)s")
            % {
                "people": ", ".join(
                    f"{person.name} ({person.role})" if person.role else person.name
                    for person in people
                )
            }
        )
    if url:
        description.append(url)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{interview.uid}",
        f"DTSTAMP:{stamp(timezone.now())}",
        f"DTSTART:{stamp(interview.starts_at)}",
        f"DTEND:{stamp(interview.ends_at)}",
        f"SUMMARY:{escape(summary)}",
        f"STATUS:{calendar_status(interview)}",
        f"CREATED:{stamp(interview.created_at)}",
        f"LAST-MODIFIED:{stamp(interview.updated_at)}",
    ]
    if interview.location:
        lines.append(f"LOCATION:{escape(interview.location)}")
    if description:
        lines.append(f"DESCRIPTION:{escape(chr(10).join(description))}")
    if url:
        lines.append(f"URL:{url}")
    for person in people:
        if person.email:
            # The address goes through the same sieve as the name: it is typed by a person
            # or sent by an API client, and it sits on the same line.
            address = without_controls(person.email)
            lines.append(
                f"ATTENDEE;CN={parameter(person.name)};ROLE=REQ-PARTICIPANT:mailto:{address}"
            )
    if alarm:
        lines += alarm_lines(interview)
    lines.append("END:VEVENT")
    return lines


@dataclass(frozen=True)
class DayEntry:
    """One whole day in the feed: a deadline, or the day a listing closes (#238)."""

    summary: str
    day: dt.date
    url: str = ""
    description: str = ""
    #: Drawn as cancelled rather than left out, so a calendar that already has it takes it
    #: off instead of keeping a date that no longer applies — an application already sent,
    #: a listing already decided.
    over: bool = False

    @property
    def uid(self) -> str:
        """Stable for the life of the thing, and unique to this instance. See the module."""
        return f"{uuid.uuid5(uuid.NAMESPACE_URL, self.url or self.summary)}@postulo"


def day_lines(entry: DayEntry) -> list[str]:
    """The VEVENT for a whole day.

    ``DTEND`` is the day *after*, because RFC 5545's end is exclusive: a one-day entry that
    ends on its own date is zero days long, and calendars disagree about what to draw for
    one — some nothing at all.
    """
    lines = [
        "BEGIN:VEVENT",
        f"UID:{entry.uid}",
        f"DTSTAMP:{stamp(timezone.now())}",
        f"DTSTART;VALUE=DATE:{entry.day:%Y%m%d}",
        f"DTEND;VALUE=DATE:{entry.day + dt.timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{escape(entry.summary)}",
        f"STATUS:{'CANCELLED' if entry.over else 'CONFIRMED'}",
        # A day nobody has to be anywhere for. Without this a calendar marks the whole day
        # busy, and a month of deadlines makes somebody look unavailable to everyone who
        # can see their free/busy.
        "TRANSP:TRANSPARENT",
    ]
    if entry.description:
        lines.append(f"DESCRIPTION:{escape(entry.description)}")
    if entry.url:
        lines.append(f"URL:{entry.url}")
    lines.append("END:VEVENT")
    return lines


def calendar(
    interviews: Iterable[Interview], *, url_for=None, days: Iterable[DayEntry] = ()
) -> str:
    """A complete iCalendar document holding these interviews and whole days.

    ``url_for`` turns an interview into the absolute address of its application page, when
    the caller has a request to build one from. ``days`` is already built, because a deadline
    and a closing date come from two different models and only the caller knows the request
    the addresses are absolute against.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for interview in interviews:
        lines += event_lines(interview, url=url_for(interview) if url_for else "")
    for entry in days:
        lines += day_lines(entry)
    lines.append("END:VCALENDAR")
    return "\r\n".join(piece for line in lines for piece in fold(line)) + "\r\n"
