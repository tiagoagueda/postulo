"""iCalendar for the dated things of a search, written with the ``icalendar`` library (#661).

It was written by hand for a while, on the argument that RFC 5545 is large and a search needs
a page of it. The page grew a fold, an escape, a parameter quoter and a list of control
characters, a second hand-written parser in the DAV plugin disagreed with it about escaping
and about time zones, and the interview's own `UID` was written without being escaped at all
(#450). The library already knows the rules for folding, escaping and quoting, so what is
left here is what only Postulo knows: what an interview, a whole day and a reminder *say*.

**The library is a writer, not a sanitiser.** A value somebody typed can hold a carriage
return, and in a line-oriented format that ends the property there. The library escapes a
line break in a text and refuses one in an address, but it leaves every other control
character in place, so each value still goes through `without_controls` first (#218).

**Three shapes, because there are three kinds of thing.** An interview is a meeting at an
hour: a ``VEVENT`` in UTC, which every reader understands and no time-zone table can get
wrong. An application's deadline and a listing's closing date are *days*: nobody knows the
hour an employer stops reading, and writing one would be inventing a fact. RFC 5545 has
exactly this distinction, ``DTSTART;VALUE=DATE``, and a calendar draws it along the top of
the day (#238). A reminder is a task: a ``VTODO``, which a calendar that keeps tasks lists
as one and a calendar that does not ignores (#661).

**A day entry's identity comes from its address.** An interview and a reminder carry a
`uid` column because they are pushed into other people's calendars and have to keep their
names across edits. A deadline has no such column, and a UID built from a primary key alone
would collide between two Postulo instances in the same calendar application. The address
of the thing is unique per instance and stable for its life, so the UID is a UUID over it
-- and over ``POSTULO_PUBLIC_URL`` where that is set, so an instance reached under two host
names gives one deadline one UID.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import icalendar
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo import __version__

from .models import Interview, InterviewOutcome, Reminder

PRODID = f"-//Postulo//Postulo {__version__}//EN"

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


def clean(text: str) -> str:
    """A text for a property: every kind of line break is a newline, and no other control
    character is left.

    A lone carriage return is a line break as much as a CRLF pair is, and old Mac text still
    arrives with one; folding it into a newline first means the library's escaping leaves
    no real one behind. The newline is what the library turns into ``\\n``.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch if ch == "\n" else without_controls(ch) for ch in text)


def escape(text: str) -> str:
    """Text as a property value: backslashes, semicolons, commas and newlines escaped."""
    return icalendar.vText(clean(text)).to_ical().decode()


def lines_of(component) -> list[str]:
    """A component as its content lines, unfolded: what a reader sees after reading it.

    The library folds at 75 octets; a plugin building a calendar of its own, and a test
    asking whether a property is there, want the lines whole.
    """
    text = component.to_ical().decode("utf-8")
    return re.sub(r"\r\n[ \t]", "", text).split("\r\n")[:-1]


def _moment(moment: dt.datetime) -> dt.datetime:
    return moment.astimezone(dt.UTC)


def calendar_status(interview: Interview) -> str:
    """What RFC 5545 calls this interview's outcome: ``CONFIRMED`` or ``CANCELLED``.

    A plugin pushing an interview to a calendar has to write the same word Postulo would,
    and a plugin holding its own table of four outcomes is a table that goes stale the day
    a fifth is added (#229).
    """
    # An outcome this version does not know, from an archive of a later Postulo, is a
    # meeting that was not called off (#450).
    return STATUS_OF.get(interview.outcome, "CONFIRMED")


def alarm_component(interview: Interview) -> icalendar.Alarm | None:
    """The VALARM for this interview's reminder, or nothing.

    Only a reminder that is still outstanding and falls before the meeting: one already
    done is not something to be woken for, and one after the meeting is not a warning about
    it. The trigger is written as a lead time rather than an absolute moment, so moving the
    event in a calendar moves the alarm with it.
    """
    reminder = getattr(interview, "reminder", None)
    if reminder is None or reminder.is_done or reminder.due_at >= interview.starts_at:
        return None
    minutes = int((interview.starts_at - reminder.due_at).total_seconds() // 60)
    alarm = icalendar.Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", clean(reminder.summary))
    alarm.add("trigger", -dt.timedelta(minutes=minutes))
    return alarm


def alarm_lines(interview: Interview) -> list[str]:
    alarm = alarm_component(interview)
    return lines_of(alarm) if alarm is not None else []


def event_component(interview: Interview, *, url: str = "", alarm: bool = False) -> icalendar.Event:
    """The VEVENT for one interview, with its reminder as an alarm if ``alarm``."""
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
    url = without_controls(url)
    if url:
        description.append(url)

    event = icalendar.Event()
    event.add("uid", without_controls(interview.uid))
    event.add("dtstamp", _moment(timezone.now()))
    event.add("dtstart", _moment(interview.starts_at))
    event.add("dtend", _moment(interview.ends_at))
    event.add("summary", clean(summary))
    event.add("status", calendar_status(interview))
    event.add("created", _moment(interview.created_at))
    event.add("last-modified", _moment(interview.updated_at))
    if interview.location:
        event.add("location", clean(interview.location))
    if description:
        event.add("description", clean("\n".join(description)))
    if url:
        event.add("url", url)
    for person in people:
        if person.email:
            # The address goes through the same sieve as the name: it is typed by a person
            # or sent by an API client, and it sits on the same line. A quotation mark has
            # no spelling inside a quoted parameter, so it is dropped.
            address = without_controls(person.email)
            event.add(
                "attendee",
                icalendar.vCalAddress(f"mailto:{address}"),
                parameters={
                    "CN": without_controls(person.name.replace('"', "")),
                    "ROLE": "REQ-PARTICIPANT",
                },
            )
    if alarm and (alarm_part := alarm_component(interview)) is not None:
        event.add_component(alarm_part)
    return event


def event_lines(interview: Interview, *, url: str = "", alarm: bool = False) -> list[str]:
    """The VEVENT for one interview as lines, with its reminder as an alarm if ``alarm``.

    The feed Postulo serves carries no alarm: a subscription that rings is a decision for
    whoever subscribes, and their calendar offers it. A sync plugin writing the event *into*
    somebody's own calendar is a different matter -- the reminder is theirs and they set it
    here -- so it asks for one (#229).
    """
    return lines_of(event_component(interview, url=url, alarm=alarm))


def address_uid(address: str) -> str:
    """An identifier made from an address: the same address is always the same identifier."""
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, address)}@postulo"


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
    #: What the identifier is made from, where that is not the address the entry links to:
    #: the same address under the instance's public name (see `agenda.dated_days`).
    identity: str = ""

    @property
    def uid(self) -> str:
        """Stable for the life of the thing, and unique to this instance. See the module."""
        return address_uid(self.identity or self.url or self.summary)


def day_component(entry: DayEntry) -> icalendar.Event:
    """The VEVENT for a whole day.

    ``DTEND`` is the day *after*, because RFC 5545's end is exclusive: a one-day entry that
    ends on its own date is zero days long, and calendars disagree about what to draw for
    one — some nothing at all.
    """
    event = icalendar.Event()
    event.add("uid", entry.uid)
    event.add("dtstamp", _moment(timezone.now()))
    event.add("dtstart", entry.day)
    event.add("dtend", entry.day + dt.timedelta(days=1))
    event.add("summary", clean(entry.summary))
    event.add("status", "CANCELLED" if entry.over else "CONFIRMED")
    # A day nobody has to be anywhere for. Without this a calendar marks the whole day
    # busy, and a month of deadlines makes somebody look unavailable to everyone who can
    # see their free/busy.
    event.add("transp", "TRANSPARENT")
    if entry.description:
        event.add("description", clean(entry.description))
    if entry.url:
        event.add("url", without_controls(entry.url))
    return event


def day_lines(entry: DayEntry) -> list[str]:
    return lines_of(day_component(entry))


def reminder_component(reminder: Reminder, *, url: str = "", identity: str = "") -> icalendar.Todo:
    """The VTODO for one reminder: what to do, by when, and whether it is done (#661).

    ``url`` is the absolute address of the application the reminder is about, which is also
    what ``RELATED-TO`` is made from -- an application has no identifier of its own, and its
    address is the one thing about it that is stable and unique to this instance, as it is for
    a whole day. ``identity`` stands in for it where the instance has a public name.
    """
    url = without_controls(url)
    todo = icalendar.Todo()
    todo.add("uid", without_controls(reminder.uid))
    todo.add("dtstamp", _moment(timezone.now()))
    todo.add("summary", clean(reminder.summary))
    todo.add("due", _moment(reminder.due_at))
    if reminder.is_done:
        todo.add("status", "COMPLETED")
        todo.add("completed", _moment(reminder.done_at))
    else:
        todo.add("status", "NEEDS-ACTION")
    todo.add("created", _moment(reminder.created_at))
    todo.add("last-modified", _moment(reminder.updated_at))
    if reminder.application_id and url:
        todo.add("related-to", address_uid(identity or url))
        todo.add("url", url)
    return todo


def reminder_lines(reminder: Reminder, *, url: str = "", identity: str = "") -> list[str]:
    return lines_of(reminder_component(reminder, url=url, identity=identity))


def reminders_calendar(reminders: Iterable[Reminder], *, url_for=None, identity_for=None) -> str:
    """A complete iCalendar document of reminders as tasks, and nothing else.

    Its own address and its own file: the interviews feed is what existing subscribers read,
    and what it means is not changed under them (#661). ``url_for`` and ``identity_for`` turn
    a reminder into the absolute address of its application, and that address under the
    instance's public name, where it has one.
    """
    wrapper = _wrapper()
    for reminder in reminders:
        wrapper.add_component(
            reminder_component(
                reminder,
                url=url_for(reminder) if url_for else "",
                identity=identity_for(reminder) if identity_for else "",
            )
        )
    return wrapper.to_ical().decode("utf-8")


def _wrapper(method: str | None = "PUBLISH") -> icalendar.Calendar:
    wrapper = icalendar.Calendar()
    wrapper.add("version", "2.0")
    wrapper.add("prodid", PRODID)
    wrapper.add("calscale", "GREGORIAN")
    if method:
        wrapper.add("method", method)
    return wrapper


def calendar(
    interviews: Iterable[Interview],
    *,
    url_for=None,
    days: Iterable[DayEntry] = (),
    reminders: Iterable[Reminder] = (),
    reminder_url_for=None,
    reminder_identity_for=None,
) -> str:
    """A complete iCalendar document holding these interviews, whole days and reminders.

    ``url_for`` turns an interview into the absolute address of its application page, when
    the caller has a request to build one from. ``days`` is already built, because a deadline
    and a closing date come from two different models and only the caller knows the request
    the addresses are absolute against. ``reminders`` are written as tasks, for the one
    download that carries every kind of dated thing; the feed leaves them out (#661).
    """
    wrapper = _wrapper()
    for interview in interviews:
        wrapper.add_component(event_component(interview, url=url_for(interview) if url_for else ""))
    for entry in days:
        wrapper.add_component(day_component(entry))
    for reminder in reminders:
        wrapper.add_component(
            reminder_component(
                reminder,
                url=reminder_url_for(reminder) if reminder_url_for else "",
                identity=reminder_identity_for(reminder) if reminder_identity_for else "",
            )
        )
    return wrapper.to_ical().decode("utf-8")
