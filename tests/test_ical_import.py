"""An iCalendar file read into plain values, and put through a review before it is added (#661)."""

import datetime as dt

import pytest

from postulo.applications import ical_reader
from postulo.applications.ical_reader import Refused, read


def calendar(*components: str, head: str = "") -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN", *([head] if head else [])]
    lines += components
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode()


def event(*props: str, uid: str = "one@example.test") -> str:
    return "\r\n".join(
        ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260101T000000Z", *props, "END:VEVENT"]
    )


def todo(*props: str, uid: str = "task@example.test") -> str:
    return "\r\n".join(["BEGIN:VTODO", f"UID:{uid}", *props, "END:VTODO"])


def only(data: bytes):
    entries = read(data).entries
    assert len(entries) == 1
    return entries[0]


# ------------------------------------------------------------------------ what is read


def test_a_timed_event_is_read_as_a_moment_with_its_place_and_notes():
    entry = only(
        calendar(
            event(
                "DTSTART:20260310T100000Z",
                "DTEND:20260310T113000Z",
                "SUMMARY:Technical interview\\, round 2",
                "LOCATION:https://meet.example.test/x",
                "DESCRIPTION:Bring the portfolio.\\nAsk about the team.",
                "STATUS:CONFIRMED",
            )
        )
    )
    assert entry.kind == ical_reader.EVENT and entry.uid == "one@example.test"
    assert entry.start == dt.datetime(2026, 3, 10, 10, 0, tzinfo=dt.UTC)
    assert entry.end == dt.datetime(2026, 3, 10, 11, 30, tzinfo=dt.UTC)
    assert entry.summary == "Technical interview, round 2"
    assert entry.location == "https://meet.example.test/x"
    assert entry.description == "Bring the portfolio.\nAsk about the team."
    assert not entry.needs_zone and not entry.refusal


def test_an_event_with_a_duration_has_an_end():
    entry = only(calendar(event("DTSTART:20260310T100000Z", "DURATION:PT45M")))
    assert entry.end == entry.start + dt.timedelta(minutes=45)


def test_a_task_is_read_by_its_due_moment_and_whether_it_is_done():
    entry = only(
        calendar(
            todo(
                "SUMMARY:Send the references",
                "DUE:20260312T080000Z",
                "STATUS:COMPLETED",
                "COMPLETED:20260311T080000Z",
            )
        )
    )
    assert entry.kind == ical_reader.TODO
    assert entry.start == dt.datetime(2026, 3, 12, 8, 0, tzinfo=dt.UTC)
    assert entry.completed and entry.summary == "Send the references"


def test_an_all_day_event_is_a_day_and_not_a_moment():
    entry = only(
        calendar(event("DTSTART;VALUE=DATE:20260315", "DTEND;VALUE=DATE:20260316", "SUMMARY:x"))
    )
    assert entry.day == dt.date(2026, 3, 15)
    assert entry.start is None and entry.end is None and not entry.needs_zone


def test_a_task_with_no_date_cannot_be_placed():
    assert only(calendar(todo("SUMMARY:Someday"))).refusal == ical_reader.NO_DATE


def test_an_event_with_an_unreadable_date_cannot_be_placed():
    assert only(calendar(event("DTSTART:garbage"))).refusal == ical_reader.UNREADABLE


# ---------------------------------------------------------------------------- time


def test_a_zone_written_as_an_iana_name_resolves():
    entry = only(calendar(event("DTSTART;TZID=Europe/Lisbon:20260701T100000")))
    assert entry.start == dt.datetime(2026, 7, 1, 9, 0, tzinfo=dt.UTC), "Lisbon is UTC+1 in July"


def test_a_zone_written_as_a_windows_name_resolves():
    entry = only(calendar(event("DTSTART;TZID=W. Europe Standard Time:20260115T100000")))
    assert entry.start == dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC)


def test_a_zone_the_file_defines_itself_resolves():
    zone = "\r\n".join(
        [
            "BEGIN:VTIMEZONE",
            "TZID:Our Office",
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            "TZOFFSETFROM:+0300",
            "TZOFFSETTO:+0300",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    )
    entry = only(calendar(zone, event("DTSTART;TZID=Our Office:20260115T100000")))
    assert entry.start == dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.UTC)


def test_a_zone_nothing_knows_is_never_read_as_utc():
    entry = only(calendar(event("DTSTART;TZID=Nowhere/Land:20260115T100000")))
    assert entry.start is None, "no moment was invented"
    assert entry.needs_zone and entry.zone == "Nowhere/Land"
    assert entry.local == dt.datetime(2026, 1, 15, 10, 0)


def test_a_floating_time_is_never_read_as_utc():
    entry = only(calendar(event("DTSTART:20260115T100000", "DTEND:20260115T110000")))
    assert entry.start is None and entry.end is None
    assert entry.needs_zone and entry.zone == ""
    assert entry.local_end == dt.datetime(2026, 1, 15, 11, 0)


def test_a_zone_written_as_a_path_into_somebody_elses_database_is_not_guessed_at():
    entry = only(
        calendar(
            event("DTSTART;TZID=/freeassociation.sourceforge.net/Europe/Lisbon:20260115T100000")
        )
    )
    assert entry.start is None and entry.needs_zone


def test_a_zone_named_afterwards_puts_the_time_in_it():
    moment = ical_reader.aware(dt.datetime(2026, 7, 1, 10, 0), "America/New_York")
    assert moment.astimezone(dt.UTC) == dt.datetime(2026, 7, 1, 14, 0, tzinfo=dt.UTC)
    assert ical_reader.aware(dt.datetime(2026, 7, 1, 10, 0), "Nowhere/Land") is None


# ---------------------------------------------------------------- what is not read


def test_an_alarms_text_never_reaches_the_notes():
    entry = only(
        calendar(
            event(
                "DTSTART:20260310T100000Z",
                "SUMMARY:Interview",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Wake up, you have an alarm",
                "TRIGGER:-PT15M",
                "END:VALARM",
            )
        )
    )
    assert entry.description == "", "the event had no notes, and the alarm's words are not them"

    held = only(
        calendar(
            event(
                "DTSTART:20260310T100000Z",
                "DESCRIPTION:The real notes",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Alarm text",
                "TRIGGER:-PT15M",
                "END:VALARM",
            )
        )
    )
    assert held.description == "The real notes"


def test_a_recurring_event_is_refused_with_its_reason():
    entry = only(calendar(event("DTSTART:20260310T100000Z", "RRULE:FREQ=WEEKLY;COUNT=4")))
    assert entry.refusal == ical_reader.RECURS
    rdate = only(calendar(event("DTSTART:20260310T100000Z", "RDATE:20260401T100000Z")))
    assert rdate.refusal == ical_reader.RECURS


def test_nothing_in_the_file_is_fetched_or_turned_into_a_person():
    entry = only(
        calendar(
            event(
                "DTSTART:20260310T100000Z",
                "URL:https://tracker.example.test/pixel",
                "ATTACH:https://tracker.example.test/file.pdf",
                "CONFERENCE;VALUE=URI:https://meet.example.test/room",
                "ATTENDEE;CN=A Stranger:mailto:stranger@example.test",
            )
        )
    )
    assert "tracker.example.test" not in str(entry.to_session())
    assert "stranger@example.test" not in str(entry.to_session())


def test_control_characters_in_a_text_do_not_survive_the_reading():
    entry = only(calendar(event("DTSTART:20260310T100000Z", "SUMMARY:Hello\x00\x07 there")))
    assert entry.summary == "Hello there"


def test_an_invitation_is_read_as_events_and_says_so():
    reading = read(calendar(event("DTSTART:20260310T100000Z"), head="METHOD:REQUEST"))
    assert len(reading.entries) == 1 and reading.notes


def test_an_entry_survives_the_session_it_is_held_in():
    entries = read(
        calendar(
            event("DTSTART:20260310T100000Z", "DTEND:20260310T110000Z", "SUMMARY:A"),
            event("DTSTART:20260315T100000", uid="two@example.test"),
            event("DTSTART;VALUE=DATE:20260315", uid="three@example.test"),
            todo("DUE;VALUE=DATE:20260320"),
        )
    ).entries
    for entry in entries:
        assert ical_reader.Entry.from_session(entry.to_session()) == entry


def test_an_identifier_is_kept_as_it_is_where_that_is_safe_and_derived_where_not():
    assert ical_reader.stored_uid("abc-123@example.test") == "abc-123@example.test"
    derived = ical_reader.stored_uid("has spaces & a very long " + "x" * 100)
    assert derived.endswith("@postulo") and len(derived) <= 64
    assert derived == ical_reader.stored_uid("has spaces & a very long " + "x" * 100)


# ---------------------------------------------------------------------------- bounds


def test_a_file_past_the_size_bound_is_refused_before_it_is_parsed(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the parser was reached")

    monkeypatch.setattr(ical_reader.icalendar.Calendar, "from_ical", never)
    with pytest.raises(Refused):
        read(b"BEGIN:VCALENDAR\r\n" + b"X-PAD:" + b"a" * ical_reader.MAX_BYTES + b"\r\n")


def test_a_file_nested_thousands_deep_is_refused_before_it_is_parsed(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the parser was reached")

    monkeypatch.setattr(ical_reader.icalendar.Calendar, "from_ical", never)
    depth = 5000
    text = "BEGIN:VCALENDAR\r\n" + "BEGIN:VEVENT\r\n" * depth + "END:VEVENT\r\n" * depth
    with pytest.raises(Refused):
        read((text + "END:VCALENDAR\r\n").encode())


def test_a_file_of_an_enormous_number_of_components_is_refused_before_it_is_parsed(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the parser was reached")

    monkeypatch.setattr(ical_reader.icalendar.Calendar, "from_ical", never)
    body = "BEGIN:VEVENT\r\nEND:VEVENT\r\n" * (ical_reader.MAX_COMPONENTS + 1)
    with pytest.raises(Refused):
        read(("BEGIN:VCALENDAR\r\n" + body + "END:VCALENDAR\r\n").encode())


def test_a_line_far_longer_than_any_calendar_writes_is_refused():
    long = "SUMMARY:" + "a" * (ical_reader.MAX_LINE + 1)
    with pytest.raises(Refused):
        read(calendar(event("DTSTART:20260310T100000Z", long)))


def test_a_close_with_no_open_is_refused():
    with pytest.raises(Refused):
        read(b"BEGIN:VCALENDAR\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_text_that_is_not_utf8_or_not_a_calendar_is_refused():
    with pytest.raises(Refused):
        read(b"\xff\xfe\x00BEGIN:VCALENDAR")
    with pytest.raises(Refused):
        read(b"just some words")


def test_more_events_than_the_page_can_review_is_refused():
    many = [
        event("DTSTART:20260310T100000Z", uid=f"u{i}") for i in range(ical_reader.MAX_ENTRIES + 1)
    ]
    with pytest.raises(Refused):
        read(calendar(*many))
