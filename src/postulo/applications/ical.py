"""iCalendar text for interviews, written by hand.

RFC 5545 is a large document, but the part an interview needs — one event with a start,
an end, a place, a description and some attendees — fits in a page, and every calendar
application imports it. A dependency would bring the rest of the standard along for
nothing. Times are written in UTC, which every reader understands and no time-zone table
can get wrong.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

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


def escape(text: str) -> str:
    """Text as a property value: backslashes, semicolons, commas and newlines escaped."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def parameter(text: str) -> str:
    """Text as a parameter value, quoted when it holds anything the grammar reserves."""
    text = text.replace('"', "")
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


def event_lines(interview: Interview, *, url: str = "") -> list[str]:
    """The VEVENT for one interview."""
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
        f"STATUS:{STATUS_OF[interview.outcome]}",
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
            lines.append(
                f"ATTENDEE;CN={parameter(person.name)};ROLE=REQ-PARTICIPANT:mailto:{person.email}"
            )
    lines.append("END:VEVENT")
    return lines


def calendar(interviews: Iterable[Interview], *, url_for=None) -> str:
    """A complete iCalendar document holding these interviews.

    ``url_for`` turns an interview into the absolute address of its application page,
    when the caller has a request to build one from.
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
    lines.append("END:VCALENDAR")
    return "\r\n".join(piece for line in lines for piece in fold(line)) + "\r\n"
