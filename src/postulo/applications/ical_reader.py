"""An iCalendar file read into plain values, and nothing that is not safe to read (#661).

The file is a stranger's. Its size, the number of its components, how deep they nest and how
long a line is are all asked of the *text*, before the parser has seen a character of it:
``icalendar`` before 7.1.3 took exponential time over a deeply nested file (CVE-2026-55099),
and a parser that is fixed today is still not a reason to hand it a hundred megabytes.

**What comes out is plain values.** A :class:`Entry` is one ``VEVENT`` or ``VTODO``: its
``UID``, a start and an end or a day, a summary, a place, a description and a status. It
holds nothing of the parser, so it survives a session, and the view never meets a component.

**Time is never guessed** (postulo/postulo-dav#24). A ``TZID`` the library resolves -- an IANA
name, a Windows name, or the file's own ``VTIMEZONE`` -- becomes a moment. One it does not,
a time with no zone at all and a date alone are each said as what they are, so the review
page can ask; none of them is ever read as UTC. ``icalendar`` returns such a time without a
zone, and that is the sign this module goes by.

**What is not read.** ``VALARM`` is a sub-component, and only a component's own properties are
read, so an alarm's text can never become somebody's notes (postulo/postulo-dav#11). ``ATTACH``,
``URL`` and ``CONFERENCE`` are not asked for, so nothing is ever fetched. ``ATTENDEE`` is not
turned into contacts. ``RRULE``, ``RDATE`` and ``RECURRENCE-ID`` make an entry that is
refused, with a reason, because a recurrence is expanded by something that knows what a
calendar's rules are.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
import warnings
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
from django.utils.translation import gettext as _

from .ical import without_controls

#: Past this a calendar is an export of somebody's whole life, not a job search.
MAX_BYTES = 2 * 1024 * 1024
#: Every ``BEGIN:`` line, whatever it opens: a time zone has two or three of its own.
MAX_COMPONENTS = 1000
#: A calendar holding an event holding an alarm is three deep. Time zones are three as well.
MAX_DEPTH = 5
#: A physical line, before it is unfolded. Calendars fold at 75; nothing real is near this.
MAX_LINE = 8192
#: Events and tasks kept: the review page draws a row for each.
MAX_ENTRIES = 200

SUMMARY_LENGTH = 250
LOCATION_LENGTH = 500
DESCRIPTION_LENGTH = 10_000

EVENT = "event"
TODO = "todo"

#: Why an entry cannot be placed at all, as the review page says it.
RECURS = "recurs"
NO_DATE = "no_date"
UNREADABLE = "unreadable"


class Refused(Exception):
    """The file cannot be read, and this says why in words for the person who chose it."""


@dataclass
class Entry:
    """One event or task, as plain values.

    Exactly one way of saying when is set: ``start`` (and maybe ``end``) for a moment that
    is known, ``day`` for a whole day, or ``local`` for a time written without a zone the
    reader will name. ``refusal`` is set for what cannot be placed at all.
    """

    kind: str
    uid: str = ""
    summary: str = ""
    location: str = ""
    description: str = ""
    status: str = ""
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    day: dt.date | None = None
    #: A time as the file wrote it, with no zone it can be put in, and the zone it named.
    local: dt.datetime | None = None
    local_end: dt.datetime | None = None
    zone: str = ""
    completed: bool = False
    #: One of the module's reasons, when this entry is refused.
    refusal: str = ""

    @property
    def needs_zone(self) -> bool:
        return self.local is not None

    @property
    def is_moment(self) -> bool:
        return self.start is not None or self.local is not None

    @property
    def cancelled(self) -> bool:
        return self.status == "CANCELLED"

    def to_session(self) -> dict:
        """Strings and numbers only: a session is JSON."""
        out: dict = {}
        for name in (
            "kind",
            "uid",
            "summary",
            "location",
            "description",
            "status",
            "zone",
            "completed",
            "refusal",
        ):
            out[name] = getattr(self, name)
        for name in ("start", "end", "day", "local", "local_end"):
            value = getattr(self, name)
            out[name] = value.isoformat() if value is not None else None
        return out

    @classmethod
    def from_session(cls, raw: dict) -> Entry:
        """The entry a session held, or `ValueError` for anything else."""
        if not isinstance(raw, dict):
            raise ValueError("not an entry")
        entry = cls(kind=EVENT if raw.get("kind") == EVENT else TODO)
        for name in ("uid", "summary", "location", "description", "status", "zone", "refusal"):
            value = raw.get(name, "")
            if not isinstance(value, str):
                raise ValueError(name)
            setattr(entry, name, value)
        entry.completed = bool(raw.get("completed"))
        for name in ("start", "end", "local", "local_end"):
            value = raw.get(name)
            setattr(entry, name, dt.datetime.fromisoformat(value) if value else None)
        if raw.get("day"):
            entry.day = dt.date.fromisoformat(raw["day"])
        return entry


@dataclass
class Reading:
    """What a file held: the entries, and what is worth saying about the file itself."""

    entries: list[Entry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def check_bounds(data: bytes) -> str:
    """The file as text, once it is within every bound; `Refused` before any parsing.

    Every bound is read off the lines themselves, so a file built to cost the parser a long
    time costs this loop a short one.
    """
    if len(data) > MAX_BYTES:
        raise Refused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused(_("That is not a text file in UTF-8, which iCalendar files are.")) from None
    depth = components = 0
    for line in text.splitlines():
        if len(line) > MAX_LINE:
            raise Refused(_("That file has a line far longer than any calendar writes."))
        head = line[:6].upper()
        if head == "BEGIN:":
            components += 1
            depth += 1
            if depth > MAX_DEPTH:
                raise Refused(_("That file nests its parts deeper than a calendar does."))
            if components > MAX_COMPONENTS:
                raise Refused(_("That file has more parts than a calendar of a search has."))
        elif line[:4].upper() == "END:":
            depth -= 1
            if depth < 0:
                raise Refused(_("That file closes a part it never opened."))
    if "BEGIN:VCALENDAR" not in text.upper():
        raise Refused(_("That is not an iCalendar file."))
    return text


def read(data: bytes) -> Reading:
    """Every event and task of an iCalendar file as plain values. Raises `Refused`."""
    text = check_bounds(data)
    with warnings.catch_warnings():
        # A TZID written as a globally unique path is *guessed* at by the library, with a
        # warning. This module does not take a guess: see `_moment`.
        warnings.simplefilter("ignore")
        try:
            calendar = icalendar.Calendar.from_ical(text)
        except (ValueError, KeyError, IndexError, TypeError):
            raise Refused(_("That file could not be read as an iCalendar file.")) from None
        reading = Reading()
        components = [*calendar.walk("VEVENT"), *calendar.walk("VTODO")]
        if len(components) > MAX_ENTRIES:
            raise Refused(
                _("That file holds more than %(limit)s events and tasks, too many to go through.")
                % {"limit": MAX_ENTRIES}
            )
        for component in components:
            reading.entries.append(_entry(component))
    if str(calendar.get("METHOD", "")).upper() == "REQUEST":
        reading.notes.append(
            _("This file is an invitation. Its events are read as any others; no reply is sent.")
        )
    return reading


def _text(component, name: str, limit: int) -> str:
    raw = component.get(name)
    if raw is None:
        return ""
    # Newlines in a description are a person's own; every other control character is not.
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    kept = "".join(ch if ch == "\n" else without_controls(ch) for ch in text)
    return kept.strip()[:limit]


def _entry(component) -> Entry:
    entry = Entry(kind=EVENT if component.name == "VEVENT" else TODO)
    entry.uid = _text(component, "UID", 255)
    entry.summary = _text(component, "SUMMARY", SUMMARY_LENGTH)
    entry.location = _text(component, "LOCATION", LOCATION_LENGTH)
    # Only this component's own property: an alarm is a sub-component with a DESCRIPTION of
    # its own, and `get` never looks inside one.
    entry.description = _text(component, "DESCRIPTION", DESCRIPTION_LENGTH)
    entry.status = _text(component, "STATUS", 20).upper()
    if any(name in component for name in ("RRULE", "RDATE", "RECURRENCE-ID")):
        entry.refusal = RECURS
    entry.completed = entry.status == "COMPLETED" or "COMPLETED" in component
    _place(entry, component)
    return entry


def _place(entry: Entry, component) -> None:
    """Say when, from DTSTART and DTEND or DURATION for an event, DUE or DTSTART for a task."""
    names = ("DTSTART",) if entry.kind == EVENT else ("DUE", "DTSTART")
    for name in names:
        if name in component:
            found = _moment(component[name])
            break
    else:
        if not entry.refusal:
            entry.refusal = NO_DATE
        return
    if found is None:
        entry.refusal = entry.refusal or UNREADABLE
        return
    value, zone, floating = found
    if isinstance(value, dt.datetime) and not floating:
        entry.start = value.astimezone(dt.UTC)
    elif isinstance(value, dt.datetime):
        entry.local, entry.zone = value, zone
    else:
        entry.day = value
    if entry.kind != EVENT or entry.day is not None:
        return
    # An end is the other half of a span, and has to be of the same sort as the start.
    if "DTEND" in component:
        ended = _moment(component["DTEND"])
        if ended is not None and isinstance(ended[0], dt.datetime):
            if entry.start is not None and not ended[2]:
                entry.end = ended[0].astimezone(dt.UTC)
            elif entry.local is not None and ended[2]:
                entry.local_end = ended[0]
    elif "DURATION" in component:
        try:
            length = component["DURATION"].dt
        except Exception:  # a broken property is the library's own error type
            return
        if isinstance(length, dt.timedelta) and dt.timedelta(0) < length < dt.timedelta(days=2):
            if entry.start is not None:
                entry.end = entry.start + length
            elif entry.local is not None:
                entry.local_end = entry.local + length


def _moment(prop):
    """``(value, zone name, floating)`` for a date or date-time property, or `None`.

    ``floating`` is true for a time that has no zone *this reader can use*: none written,
    one that no table knows, or one written as a path to somebody else's zone database.
    """
    try:
        value = prop.dt
    except Exception:  # BrokenCalendarProperty, and whatever else the library raises
        return None
    if isinstance(value, dt.datetime):
        tzid = str(prop.params.get("TZID", "") or "")
        if value.tzinfo is None:
            return value, tzid, True
        if tzid.startswith("/"):
            # `icalendar` strips the prefix and guesses; that is a guess this module will
            # not make for somebody (RFC 5545 §3.2.19).
            return value.replace(tzinfo=None), tzid, True
        return value, tzid, False
    if isinstance(value, dt.date):
        return value, "", False
    return None


def stored_uid(uid: str) -> str:
    """The identifier an imported record is kept under, derived and never random.

    A file's own identifier where it is plainly safe to keep; otherwise a name derived from
    it. Either way the same file read twice gives the same name, which is what lets the
    second reading say *already there* and add nothing.
    """
    if re.fullmatch(r"[A-Za-z0-9@._-]{1,64}", uid):
        return uid
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, uid)}@postulo"


def aware(local: dt.datetime, zone: str) -> dt.datetime | None:
    """A time written without a zone, put in the one the person named. `None` if unknown."""
    try:
        return local.replace(tzinfo=ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
