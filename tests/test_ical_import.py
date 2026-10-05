"""An iCalendar file read into plain values, and put through a review before it is added (#661)."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import ical_import, ical_reader
from postulo.applications.ical_reader import Refused, read

pytestmark = pytest.mark.django_db


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


# ------------------------------------------------------------------- the review page

ADDRESS = "applications:ical_import"


@pytest.fixture
def application(user):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def upload(client, data: bytes, name: str = "calendar.ics"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(reverse(ADDRESS), {"file": SimpleUploadedFile(name, data)})


def future(days: int = 10) -> str:
    moment = timezone.now() + dt.timedelta(days=days)
    return moment.strftime("%Y%m%dT%H0000Z")


def choose(client, **fields):
    """Post the confirm button with these choices, `r<row>-<field>`."""
    return client.post(reverse(ADDRESS), {"action": "confirm", **fields})


def test_the_page_needs_a_sign_in(client, db):
    response = client.get(reverse(ADDRESS))
    assert response.status_code == 302 and reverse("account_login") in response["Location"]
    assert client.post(reverse(ADDRESS), {"action": "confirm"}).status_code == 302


def test_reading_a_file_makes_nothing(client, user, application):
    from postulo.applications.models import Interview, Reminder

    client.force_login(user)
    sent = upload(client, calendar(event(f"DTSTART:{future()}", "SUMMARY:Call")))
    assert sent.status_code == 302

    page = client.get(reverse(ADDRESS))
    assert page.status_code == 200 and "Call" in page.content.decode()
    assert not Interview.objects.exists() and not Reminder.objects.exists()


def test_a_timed_event_becomes_an_interview_with_its_timeline_entry(client, user, application):
    from postulo.applications.models import EventKind, Interview

    client.force_login(user)
    upload(
        client,
        calendar(
            event(
                f"DTSTART:{future()}",
                "SUMMARY:Technical round",
                "LOCATION:https://meet.example.test/x",
                "DESCRIPTION:Bring the portfolio",
            )
        ),
        name="meetings.ics",
    )
    response = choose(
        client, **{"r0-action": "interview", "r0-application": application.pk, "r0-kind": "panel"}
    )
    assert response.status_code == 302 and response["Location"] == reverse("applications:calendar")

    interview = Interview.objects.get(owner=user)
    assert interview.application == application and interview.kind == "panel"
    assert interview.location == "https://meet.example.test/x"
    assert interview.notes == "Technical round\n\nBring the portfolio"
    assert interview.uid == "one@example.test", "it keeps the name the file gave it"
    entry = application.events.get(kind=EventKind.INTERVIEW_SCHEDULED)
    assert "meetings.ics" in entry.actor, "the timeline records the file"
    assert client.get(reverse(ADDRESS)).content.decode().count("data-ical-review") == 0, "forgotten"


def test_a_task_and_an_all_day_event_become_reminders(client, user, application):
    from postulo.applications.models import Reminder

    client.force_login(user)
    upload(
        client,
        calendar(
            todo("SUMMARY:Send the references", f"DUE:{future(3)}", "STATUS:COMPLETED"),
            event(
                "DTSTART;VALUE=DATE:20270315",
                "SUMMARY:Open day",
                uid="day@example.test",
            ),
        ),
    )
    choose(
        client,
        **{
            "r0-action": "reminder",
            "r0-application": application.pk,
            "r1-action": "reminder",
        },
    )
    done, day = Reminder.objects.order_by("pk")
    assert done.summary == "Send the references" and done.is_done
    assert done.application == application and done.uid == "task@example.test"
    assert day.summary == "Open day" and day.application is None
    assert timezone.localtime(day.due_at).date() == dt.date(2027, 3, 15)
    assert timezone.localtime(day.due_at).hour == 9, "an hour of the reminder's, not the event's"


def test_nothing_is_made_for_what_was_left_out(client, user, application):
    from postulo.applications.models import Interview, Reminder

    client.force_login(user)
    upload(client, calendar(event(f"DTSTART:{future()}")))
    response = choose(client, **{"r0-action": ""})
    assert response.status_code == 302
    assert not Interview.objects.exists() and not Reminder.objects.exists()


def test_an_interview_needs_an_application(client, user, application):
    from postulo.applications.models import Interview

    client.force_login(user)
    upload(client, calendar(event(f"DTSTART:{future()}")))
    response = choose(client, **{"r0-action": "interview"})
    assert response.status_code == 200 and "Choose one" in response.content.decode()
    assert not Interview.objects.exists()
    assert client.get(reverse(ADDRESS)).content.decode().count("data-ical-review") == 1, "held"


def test_a_floating_time_asks_for_a_zone_and_is_never_utc_unseen(client, user, application):
    from postulo.applications.models import Interview

    client.force_login(user)
    upload(client, calendar(event("DTSTART:20291015T100000", "DTEND:20291015T110000")))
    page = client.get(reverse(ADDRESS)).content.decode()
    assert "data-needs-zone" in page and "without a time zone" in page

    refused = choose(client, **{"r0-action": "interview", "r0-application": application.pk})
    assert refused.status_code == 200 and not Interview.objects.exists()
    assert "time zone" in refused.content.decode()

    choose(
        client,
        **{
            "r0-action": "interview",
            "r0-application": application.pk,
            "r0-zone": "America/New_York",
        },
    )
    interview = Interview.objects.get()
    assert interview.starts_at == dt.datetime(2029, 10, 15, 14, 0, tzinfo=dt.UTC)
    assert interview.ends_at == dt.datetime(2029, 10, 15, 15, 0, tzinfo=dt.UTC)


def test_a_zone_nobody_knows_is_asked_about_by_name(client, user, application):
    client.force_login(user)
    upload(client, calendar(event("DTSTART;TZID=Nowhere/Land:20291015T100000")))
    page = client.get(reverse(ADDRESS)).content.decode()
    assert "Nowhere/Land" in page and "data-needs-zone" in page


def test_a_zone_that_is_not_one_is_refused(client, user, application):
    client.force_login(user)
    upload(client, calendar(event("DTSTART:20291015T100000")))
    response = choose(client, **{"r0-action": "reminder", "r0-zone": "Nowhere/Land"})
    assert response.status_code == 200 and "Select a valid choice" in response.content.decode()


def test_the_same_file_twice_adds_nothing_the_second_time(client, user, application):
    from postulo.applications.models import Interview, Reminder

    client.force_login(user)
    data = calendar(
        event(f"DTSTART:{future()}", uid="meeting@example.test"),
        todo("SUMMARY:Task", f"DUE:{future(2)}", uid="task-2@example.test"),
    )
    upload(client, data)
    choose(
        client,
        **{
            "r0-action": "interview",
            "r0-application": application.pk,
            "r1-action": "reminder",
        },
    )
    assert Interview.objects.count() == 1 and Reminder.objects.filter(owner=user).count() >= 2

    held = Reminder.objects.filter(owner=user).count()
    upload(client, data)
    page = client.get(reverse(ADDRESS)).content.decode()
    assert page.count('data-outcome="present"') == 2
    assert "Add what I chose" not in page, "nothing to add, so no button"
    choose(client, **{"r0-action": "interview", "r0-application": application.pk})
    assert Interview.objects.count() == 1 and Reminder.objects.filter(owner=user).count() == held


def test_an_identifier_the_file_shares_between_two_entries_is_made_once(client, user, application):
    client.force_login(user)
    upload(
        client,
        calendar(
            event(f"DTSTART:{future()}", uid="same@example.test"),
            event(f"DTSTART:{future(11)}", uid="same@example.test"),
        ),
    )
    page = client.get(reverse(ADDRESS)).content.decode()
    assert page.count('data-outcome="cannot"') == 1


def test_a_recurring_event_is_listed_as_one_that_cannot_be_added(client, user, application):
    client.force_login(user)
    upload(client, calendar(event(f"DTSTART:{future()}", "RRULE:FREQ=WEEKLY", "SUMMARY:Standup")))
    page = client.get(reverse(ADDRESS)).content.decode()
    assert 'data-outcome="cannot"' in page and "It repeats" in page
    assert "Add what I chose" not in page


def test_an_invitation_says_no_reply_is_sent(client, user, application):
    client.force_login(user)
    upload(client, calendar(event(f"DTSTART:{future()}"), head="METHOD:REQUEST"))
    assert "no reply is sent" in client.get(reverse(ADDRESS)).content.decode()


def test_a_file_that_is_refused_says_why_and_holds_nothing(client, user):
    client.force_login(user)
    response = upload(client, b"just some words")
    assert response.status_code == 302
    page = client.get(reverse(ADDRESS)).content.decode()
    assert "not an iCalendar file" in page and "data-ical-review" not in page


def test_a_file_past_the_size_bound_is_refused_by_the_page(client, user):
    client.force_login(user)
    upload(client, b"x" * (ical_reader.MAX_BYTES + 1))
    assert "larger than" in client.get(reverse(ADDRESS)).content.decode()


def test_a_file_with_nothing_in_it_is_said_to_be_empty(client, user):
    client.force_login(user)
    upload(client, calendar())
    assert "no events or tasks" in client.get(reverse(ADDRESS)).content.decode()


def test_no_file_chosen_is_said(client, user):
    client.force_login(user)
    client.post(reverse(ADDRESS), {})
    assert "Choose a file first" in client.get(reverse(ADDRESS)).content.decode()


def test_start_again_forgets_the_file(client, user):
    client.force_login(user)
    upload(client, calendar(event(f"DTSTART:{future()}")))
    client.post(reverse(ADDRESS), {"action": "forget"})
    assert "data-ical-review" not in client.get(reverse(ADDRESS)).content.decode()


def test_confirming_with_nothing_held_says_so(client, user):
    client.force_login(user)
    client.post(reverse(ADDRESS), {"action": "confirm"})
    assert "nothing waiting" in client.get(reverse(ADDRESS)).content.decode()


def test_a_session_that_holds_something_else_is_not_an_error_page(client, user):
    client.force_login(user)
    session = client.session
    session[ical_import.SESSION_KEY] = {"entries": "nonsense"}
    session.save()
    assert client.get(reverse(ADDRESS)).status_code == 200
    assert ical_import.SESSION_KEY not in client.session


def test_a_long_summary_and_a_hostile_one_are_cut_to_what_the_column_holds(client, user):
    from postulo.applications.models import Reminder

    client.force_login(user)
    upload(client, calendar(todo("SUMMARY:" + "a" * 3000, f"DUE:{future()}")))
    choose(client, **{"r0-action": "reminder"})
    assert len(Reminder.objects.get().summary) <= 250


def test_an_identifier_that_is_not_plainly_safe_is_derived_and_still_recognised(client, user):
    from postulo.applications.models import Reminder

    client.force_login(user)
    data = calendar(todo("SUMMARY:x", f"DUE:{future()}", uid="has spaces & <odd>"))
    upload(client, data)
    choose(client, **{"r0-action": "reminder"})
    assert Reminder.objects.get().uid.endswith("@postulo")
    upload(client, data)
    assert 'data-outcome="present"' in client.get(reverse(ADDRESS)).content.decode()


# ----------------------------------------------------------------------- the files out


def test_a_reminder_is_written_as_a_task_with_its_application(client, user, application):
    import icalendar

    from postulo.applications.models import Reminder

    due = timezone.now() + dt.timedelta(days=2)
    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase, politely; twice", due_at=due
    )
    client.force_login(user)

    text = client.get(reverse("applications:reminder_calendar")).content.decode()

    todo_part = icalendar.Calendar.from_ical(text).walk("VTODO")[0]
    assert str(todo_part["UID"]) == reminder.uid and str(todo_part["STATUS"]) == "NEEDS-ACTION"
    assert str(todo_part["SUMMARY"]) == "Chase, politely; twice"
    assert todo_part["DUE"].dt == due.replace(microsecond=0)
    assert "RELATED-TO" in todo_part and "VEVENT" not in text
    assert "COMPLETED" not in [name for name in todo_part if name == "COMPLETED"]

    reminder.complete()
    done = client.get(reverse("applications:reminder_calendar")).content.decode()
    part = icalendar.Calendar.from_ical(done).walk("VTODO")[0]
    assert str(part["STATUS"]) == "COMPLETED" and "COMPLETED" in part


def test_a_reminder_about_no_application_has_no_relation(client, user):
    from postulo.applications.models import Reminder

    Reminder.objects.create(owner=user, summary="Renew passport", due_at=timezone.now())
    client.force_login(user)
    text = client.get(reverse("applications:reminder_calendar")).content.decode()
    assert "RELATED-TO" not in text and "Renew passport" in text


def test_a_reminder_done_long_ago_is_not_in_the_file(client, user):
    from postulo.applications import agenda
    from postulo.applications.models import Reminder

    old = Reminder.objects.create(owner=user, summary="Ancient", due_at=timezone.now())
    Reminder.objects.filter(pk=old.pk).update(
        done_at=timezone.now() - dt.timedelta(days=agenda.FEED_DAYS + 5)
    )
    client.force_login(user)
    assert "Ancient" not in client.get(reverse("applications:reminder_calendar")).content.decode()


def test_the_interviews_feed_does_not_change_under_its_subscribers(client, user, application):
    from postulo.applications.models import Reminder

    Reminder.objects.create(
        owner=user, application=application, summary="Nudge", due_at=timezone.now()
    )
    client.force_login(user)
    text = client.get(reverse("applications:interview_calendar")).content.decode()
    assert "VTODO" not in text and "Nudge" not in text


def test_the_calendar_page_downloads_what_it_shows(client, user, application):
    import icalendar

    from postulo.applications.models import Reminder
    from postulo.applications.services import schedule_interview

    soon = timezone.now() + dt.timedelta(days=1)
    schedule_interview(application, kind="video", starts_at=soon)
    Reminder.objects.create(owner=user, application=application, summary="Nudge", due_at=soon)
    application.deadline = timezone.localdate() + dt.timedelta(days=1)
    application.save(update_fields=["deadline"])
    client.force_login(user)
    address = reverse("applications:calendar_download")
    start = timezone.localdate().strftime("%Y-%m-%d")

    everything = client.get(address, {"view": "agenda", "on": start})
    assert everything["Content-Disposition"].startswith("attachment")
    parsed = icalendar.Calendar.from_ical(everything.content.decode())
    assert len(parsed.walk("VEVENT")) == 2, "the interview, and the deadline as a day"
    assert len(parsed.walk("VTODO")) == 1

    narrowed = client.get(address, {"view": "agenda", "on": start, "kinds": "reminder"})
    parsed = icalendar.Calendar.from_ical(narrowed.content.decode())
    assert len(parsed.walk("VEVENT")) == 0 and len(parsed.walk("VTODO")) == 1


def test_the_calendar_page_links_to_the_download_and_the_import(client, user):
    client.force_login(user)
    page = client.get(reverse("applications:calendar"), {"view": "agenda", "kinds": "reminder"})
    html = page.content.decode()
    assert "Download as iCalendar" in html and "Import an iCalendar file" in html
    assert "download.ics?view=agenda&amp;kinds=reminder" in html, "the kinds travel with it"


def test_a_reminders_identifier_travels_in_the_archive_and_comes_back(user, other_user):
    import zipfile

    from postulo.applications.models import Reminder
    from postulo.core import export, importer

    Reminder.objects.create(owner=user, summary="Alone", due_at=timezone.now())
    kept = Reminder.objects.get()
    assert export.build_document(user)["reminders"][0]["uid"] == kept.uid

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    assert Reminder.objects.get(owner=other_user).uid == kept.uid


def test_a_forced_duplicate_import_gives_a_reminder_a_fresh_identifier(user):
    import zipfile

    from postulo.applications.models import Reminder
    from postulo.core import export, importer

    Reminder.objects.create(owner=user, summary="Alone", due_at=timezone.now())
    importer.load(user, zipfile.ZipFile(export.write_archive(user)), force=True)
    assert len({r.uid for r in Reminder.objects.filter(owner=user)}) == 2


def test_an_archive_from_before_the_identifier_restores_each_with_its_own(user):
    from postulo.applications.models import Reminder
    from postulo.core import importer

    assert importer._restored_uid(Reminder, user, None) == {}
    assert importer._restored_uid(Reminder, user, "x@postulo") == {"uid": "x@postulo"}
    assert importer._restored_uid(Reminder, user, "bad\r\nuid") == {}
    Reminder.objects.create(owner=user, summary="A", due_at=timezone.now(), uid="taken@postulo")
    assert importer._restored_uid(Reminder, user, "taken@postulo") == {}
