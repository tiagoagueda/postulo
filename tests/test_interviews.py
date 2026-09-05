"""Interviews: scheduled meetings, not only lines in the timeline."""

import datetime as dt
import io
import json
import zipfile

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import analytics, ical
from postulo.applications.models import (
    Application,
    ApplicationEvent,
    EventKind,
    Interview,
    InterviewKind,
    InterviewOutcome,
    Reminder,
    Status,
)
from postulo.applications.services import (
    change_status,
    reschedule_interview,
    schedule_interview,
    settle_interview,
)
from postulo.core import export as export_module
from postulo.core import importer
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def recruiter(user, company):
    return Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", role="CEO", email="cave@aperture.test"
    )


@pytest.fixture
def application(user, company):
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=10))
    application.refresh_from_db()
    return application


def in_days(days: float, hour: int = 10) -> dt.datetime:
    return (timezone.now() + dt.timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


# --------------------------------------------------------------------- scheduling


def test_scheduling_writes_the_timeline_and_a_reminder_for_the_day_before(application, recruiter):
    starts = in_days(5)
    interview = schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=starts,
        location="https://meet.example.org/abc",
        contacts=[recruiter],
    )

    assert interview.ends_at == starts + dt.timedelta(hours=1), "an hour when no end is given"
    assert interview.is_scheduled and not interview.is_settled
    assert interview.uid.endswith("@postulo")

    event = application.events.first()
    assert event.kind == EventKind.INTERVIEW_SCHEDULED
    assert "Video call scheduled for" in event.summary
    assert event.body == "https://meet.example.org/abc"

    reminder = interview.reminder
    assert reminder is not None and reminder.application == application
    assert reminder.due_at == starts - dt.timedelta(days=1)
    assert "Interview tomorrow" in reminder.summary and "Aperture Science" in reminder.summary


def test_an_interview_within_a_day_gets_no_reminder(application):
    interview = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=timezone.now() + dt.timedelta(hours=5)
    )
    assert interview.reminder is None
    assert application.events.filter(kind=EventKind.INTERVIEW_SCHEDULED).count() == 1


def test_a_reminder_can_be_declined(application):
    interview = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=in_days(9), remind=False
    )
    assert interview.reminder is None


def test_one_already_over_is_recorded_as_held_straight_away(application):
    """The person forgot to schedule it; the timeline should read as if they had not."""
    when = timezone.now() - dt.timedelta(days=2)
    interview = schedule_interview(application, kind=InterviewKind.ONSITE, starts_at=when)

    assert interview.outcome == InterviewOutcome.DONE
    assert interview.reminder is None
    kinds = list(application.events.values_list("kind", flat=True))
    assert EventKind.INTERVIEW_SCHEDULED not in kinds, "nothing to look forward to"
    held = application.events.get(kind=EventKind.INTERVIEW)
    assert held.summary == "On site held" and held.occurred_at == when
    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING, "the status caught up"


# ----------------------------------------------------------------------- outcomes


def test_holding_an_interview_moves_a_lagging_status_through_the_log(application):
    interview = schedule_interview(application, kind=InterviewKind.PANEL, starts_at=in_days(1))
    settle_interview(interview, InterviewOutcome.DONE, note="Went well.")

    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING
    move = application.events.get(kind=EventKind.STATUS_CHANGE, to_status=Status.INTERVIEWING)
    assert move.occurred_at == interview.starts_at
    held = application.events.get(kind=EventKind.INTERVIEW)
    assert held.body == "Went well."
    assert interview.reminder is None or interview.reminder.is_done


def test_a_phone_screen_only_moves_the_status_to_screening(application):
    interview = schedule_interview(application, kind=InterviewKind.PHONE, starts_at=in_days(1))
    settle_interview(interview, InterviewOutcome.DONE)
    application.refresh_from_db()
    assert application.status == Status.SCREENING


def test_holding_one_never_moves_a_status_that_is_ahead_or_settled(application):
    change_status(application, Status.OFFER)
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(1))
    settle_interview(interview, InterviewOutcome.DONE)
    application.refresh_from_db()
    assert application.status == Status.OFFER

    change_status(application, Status.REJECTED)
    late = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    settle_interview(late, InterviewOutcome.DONE)
    application.refresh_from_db()
    assert application.status == Status.REJECTED, "a remembered interview reopens nothing"


def test_cancelling_records_it_and_settles_the_reminder(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(6))
    reminder = interview.reminder
    settle_interview(interview, InterviewOutcome.CANCELLED, note="They postponed.")

    cancelled = application.events.get(kind=EventKind.INTERVIEW_CANCELLED)
    assert "cancelled" in cancelled.summary and cancelled.body == "They postponed."
    reminder.refresh_from_db()
    assert reminder.is_done
    assert not Interview.objects.for_user(application.owner).upcoming().exists()


def test_a_no_show_is_an_interview_entry_that_moves_nothing(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(1))
    settle_interview(interview, InterviewOutcome.NO_SHOW)
    entry = application.events.get(kind=EventKind.INTERVIEW)
    assert entry.summary == "Nobody showed up for the video call"
    application.refresh_from_db()
    assert application.status == Status.APPLIED


def test_an_outcome_is_recorded_once(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(1))
    settle_interview(interview, InterviewOutcome.DONE)
    settle_interview(interview, InterviewOutcome.DONE)
    assert application.events.filter(kind=EventKind.INTERVIEW).count() == 1
    with pytest.raises(ValueError, match="not an outcome"):
        settle_interview(interview, InterviewOutcome.SCHEDULED)


# ------------------------------------------------------------------- rescheduling


def test_moving_an_interview_moves_its_reminder_and_says_so(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(4))
    interview.reminder.notified_at = timezone.now()
    interview.reminder.save()

    later = in_days(8)
    reschedule_interview(interview, starts_at=later, ends_at=later + dt.timedelta(minutes=45))

    interview.refresh_from_db()
    assert interview.duration == dt.timedelta(minutes=45)
    reminder = interview.reminder
    assert reminder.due_at == later - dt.timedelta(days=1)
    assert reminder.notified_at is None, "announced again at the new time"
    moved = application.events.filter(kind=EventKind.INTERVIEW_SCHEDULED).first()
    assert "moved from" in moved.summary


def test_moving_it_to_within_a_day_retires_the_reminder(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(4))
    soon = timezone.now() + dt.timedelta(hours=3)
    reschedule_interview(interview, starts_at=soon, ends_at=soon + dt.timedelta(hours=1))
    assert interview.reminder.is_done


def test_saving_unchanged_times_records_nothing(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(4))
    before = application.events.count()
    reschedule_interview(interview, starts_at=interview.starts_at, ends_at=interview.ends_at)
    assert application.events.count() == before


# --------------------------------------------------------------------- the diary


def test_the_queryset_knows_what_is_ahead_and_what_awaits_an_outcome(application):
    ahead = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(3))
    passed = Interview.objects.create(
        owner=application.owner,
        application=application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() - dt.timedelta(days=1),
        ends_at=timezone.now() - dt.timedelta(hours=23),
    )
    diary = Interview.objects.for_user(application.owner)
    assert list(diary.upcoming()) == [ahead]
    assert list(diary.awaiting_outcome()) == [passed]
    assert passed.awaits_outcome and not ahead.awaits_outcome


def test_applications_carry_their_next_interview(application, user):
    schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(7))
    soonest = schedule_interview(application, kind=InterviewKind.PHONE, starts_at=in_days(2))
    cancelled = schedule_interview(application, kind=InterviewKind.PANEL, starts_at=in_days(1))
    settle_interview(cancelled, InterviewOutcome.CANCELLED)

    row = Application.objects.for_user(user).with_display_data().get(pk=application.pk)
    assert row.next_interview_at == soonest.starts_at


# ------------------------------------------------------------------------- views


def test_the_form_schedules_and_the_page_shows_it(client, user, application, recruiter):
    client.force_login(user)
    starts = in_days(3)
    response = client.post(
        reverse("applications:interview_create", args=[application.pk]),
        {
            "kind": InterviewKind.ONSITE,
            "starts_at": timezone.localtime(starts).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": "",
            "location": "1 Aperture Way",
            "contacts": [recruiter.pk],
            "notes": "Bring the portfolio.",
            "remind": "on",
        },
    )
    assert response.status_code == 302
    interview = Interview.objects.get(application=application)
    assert list(interview.contacts.all()) == [recruiter]
    assert interview.reminder is not None

    page = client.get(application.get_absolute_url()).content.decode()
    assert "On site" in page and "1 Aperture Way" in page and "Cave Johnson" in page
    assert reverse("applications:interview_ics", args=[interview.pk]) in page
    assert "Interview scheduled." in page or "On site scheduled for" in page


def test_the_form_refuses_an_end_before_the_start(client, user, application):
    client.force_login(user)
    starts = in_days(3)
    response = client.post(
        reverse("applications:interview_create", args=[application.pk]),
        {
            "kind": InterviewKind.VIDEO,
            "starts_at": timezone.localtime(starts).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(starts - dt.timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
        },
    )
    assert response.status_code == 200
    assert "cannot end before it starts" in response.content.decode()
    assert not Interview.objects.exists()


def test_the_form_offers_only_this_companys_people(client, user, application, recruiter):
    elsewhere = Company.objects.create(owner=user, name="Black Mesa")
    Contact.objects.create(owner=user, company=elsewhere, name="Gordon Freeman")
    client.force_login(user)
    page = client.get(reverse("applications:interview_create", args=[application.pk]))
    body = page.content.decode()
    assert "Cave Johnson" in body and "Gordon Freeman" not in body


def test_editing_moves_the_interview_through_the_service(client, user, application):
    interview = schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=in_days(4), location="old"
    )
    client.force_login(user)
    later = in_days(9)
    response = client.post(
        reverse("applications:interview_update", args=[interview.pk]),
        {
            "kind": InterviewKind.PANEL,
            "starts_at": timezone.localtime(later).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": "",
            "location": "new",
            "notes": "",
        },
    )
    assert response.status_code == 302
    interview.refresh_from_db()
    assert interview.kind == InterviewKind.PANEL and interview.location == "new"
    assert interview.starts_at == later
    assert interview.reminder.due_at == later - dt.timedelta(days=1)
    assert application.events.filter(summary__contains="moved from").exists()


def test_the_outcome_buttons_settle_it(client, user, application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(1))
    client.force_login(user)
    response = client.post(
        reverse("applications:interview_outcome", args=[interview.pk]),
        {"outcome": "done", "next": application.get_absolute_url()},
    )
    assert response.status_code == 302
    interview.refresh_from_db()
    assert interview.outcome == InterviewOutcome.DONE

    response = client.post(
        reverse("applications:interview_outcome", args=[interview.pk]), {"outcome": "won"}
    )
    assert response.status_code == 302
    interview.refresh_from_db()
    assert interview.outcome == InterviewOutcome.DONE


def test_someone_elses_interview_is_not_there(client, other_user, application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    client.force_login(other_user)
    for name in ("interview_update", "interview_ics"):
        assert client.get(reverse(f"applications:{name}", args=[interview.pk])).status_code == 404
    response = client.post(
        reverse("applications:interview_outcome", args=[interview.pk]), {"outcome": "done"}
    )
    assert response.status_code == 404
    assert (
        client.get(reverse("applications:interview_list")).content.decode().count("Test Engineer")
        == 0
    )


def test_the_list_shows_the_diary_and_the_past_on_request(client, user, application):
    ahead = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    gone = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=timezone.now() - dt.timedelta(days=3)
    )
    client.force_login(user)
    diary = client.get(reverse("applications:interview_list")).content.decode()
    assert f"interview-{ahead.pk}" in diary and f"interview-{gone.pk}" not in diary
    everything = client.get(reverse("applications:interview_list") + "?show=all").content.decode()
    assert f"interview-{ahead.pk}" in everything and f"interview-{gone.pk}" in everything


def test_the_dashboard_says_what_is_coming_up_and_what_awaits_an_outcome(client, user, application):
    schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    Interview.objects.create(
        owner=user,
        application=application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() - dt.timedelta(days=1),
        ends_at=timezone.now() - dt.timedelta(hours=23),
    )
    client.force_login(user)
    page = client.get(reverse("core:home")).content.decode()
    assert "data-coming-up" in page and "Video call" in page
    assert "1 interview has passed without an outcome" in page


def test_the_board_card_and_the_table_row_show_the_next_interview(client, user, application):
    schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    client.force_login(user)
    assert (
        client.get(reverse("applications:board")).content.decode().count("data-next-interview") == 1
    )
    assert (
        client.get(reverse("applications:list")).content.decode().count("data-next-interview") == 1
    )


def test_scheduling_kinds_cannot_be_typed_onto_the_timeline(client, user, application):
    client.force_login(user)
    page = client.get(application.get_absolute_url()).content.decode()
    assert 'value="interview_scheduled"' not in page
    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {"kind": "interview_scheduled", "occurred_at": "2026-01-01T10:00", "summary": "x"},
    )
    assert response.status_code == 302
    assert not application.events.filter(kind=EventKind.INTERVIEW_SCHEDULED).exists()


# --------------------------------------------------------------------- calendars


def test_the_ics_file_is_one_every_calendar_can_import(client, user, application, recruiter):
    starts = dt.datetime(2026, 10, 5, 9, 30, tzinfo=dt.UTC)
    interview = schedule_interview(
        application,
        kind=InterviewKind.ONSITE,
        starts_at=starts,
        location="1 Aperture Way, Cambridge; floor 3",
        notes="Bring ID.\nAsk about the team.",
        contacts=[recruiter],
    )
    client.force_login(user)
    response = client.get(reverse("applications:interview_ics", args=[interview.pk]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert response["Content-Disposition"].endswith(f'interview-{interview.pk}.ics"')
    text = response.content.decode()
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and "END:VCALENDAR" in lines
    assert f"UID:{interview.uid}" in lines
    assert "DTSTART:20261005T093000Z" in lines and "DTEND:20261005T103000Z" in lines
    assert "SUMMARY:On site: Test Engineer at Aperture Science" in lines
    assert "LOCATION:1 Aperture Way\\, Cambridge\\; floor 3" in lines
    assert any(
        line.startswith("DESCRIPTION:Bring ID.\\nAsk about the team.\\nWith: Cave")
        for line in lines
    )
    assert "ATTENDEE;CN=Cave Johnson;ROLE=REQ-PARTICIPANT:mailto:cave@aperture.test" in lines
    assert any(line.startswith("URL:http://testserver/applications/") for line in lines)
    assert "STATUS:CONFIRMED" in lines
    assert all(len(line.encode()) <= 75 for line in lines), "folded at 75 octets"


def test_a_cancelled_interview_says_so_in_its_calendar(application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    settle_interview(interview, InterviewOutcome.CANCELLED)
    assert "STATUS:CANCELLED" in ical.calendar([interview])


def test_the_whole_diary_downloads_as_one_file(client, user, application):
    first = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    second = schedule_interview(application, kind=InterviewKind.PANEL, starts_at=in_days(5))
    gone = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=timezone.now() - dt.timedelta(days=3)
    )
    client.force_login(user)
    text = client.get(reverse("applications:interview_calendar")).content.decode()
    assert text.count("BEGIN:VEVENT") == 2
    assert first.uid in text and second.uid in text and gone.uid not in text


def test_folding_never_splits_a_character():
    line = "DESCRIPTION:" + "é" * 100
    pieces = ical.fold(line)
    assert all(len(piece.encode()) <= 75 for piece in pieces)
    assert "".join(piece[1:] if index else piece for index, piece in enumerate(pieces)) == line


# ---------------------------------------------------------------- export, import


def test_interviews_travel_in_the_export_and_come_back(user, other_user, application, recruiter):
    interview = schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=in_days(3),
        location="https://meet.example.org/x",
        contacts=[recruiter],
    )
    document = export_module.build_document(user)
    assert document["postulo"]["format"] == 3
    exported = document["companies"][0]["postings"][0]["applications"][0]["interviews"][0]
    assert exported["uid"] == interview.uid
    assert exported["contact_ids"] == [recruiter.pk]
    assert exported["reminder_id"] == interview.reminder_id
    assert document["counts"]["interviews"] == 1

    archive = zipfile.ZipFile(export_module.write_archive(user))
    report = importer.load(other_user, archive)
    assert report.interviews == 1

    restored = Interview.objects.get(owner=other_user)
    assert restored.uid == interview.uid, "the calendar still recognises it"
    assert restored.starts_at == interview.starts_at
    assert [c.name for c in restored.contacts.all()] == ["Cave Johnson"]
    assert restored.reminder is not None and restored.reminder.owner == other_user
    assert restored.reminder.summary == interview.reminder.summary


def test_a_forced_duplicate_import_mints_a_fresh_calendar_identifier(user, application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(3))
    archive = zipfile.ZipFile(export_module.write_archive(user))
    importer.load(user, archive, force=True)
    uids = list(Interview.objects.for_user(user).values_list("uid", flat=True))
    assert len(uids) == 2 and len(set(uids)) == 2 and interview.uid in uids


def test_a_format_two_archive_has_no_interviews_and_still_imports(user, other_user, application):
    document = export_module.build_document(user)
    document["postulo"]["format"] = 2
    for company in document["companies"]:
        for posting in company["postings"]:
            for entry in posting["applications"]:
                entry.pop("interviews", None)
                for event in entry["events"]:
                    event.pop("actor", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    report = importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))
    assert report.applications == 1 and report.interviews == 0


# ---------------------------------------------------------------------- insights


def test_insights_measure_the_days_to_a_first_interview(user, company, application):
    """From the log, so an interview typed in by hand counts as much as one from the diary."""
    first = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=timezone.now() - dt.timedelta(days=6)
    )
    schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=timezone.now() - dt.timedelta(days=2)
    )
    assert first.outcome == InterviewOutcome.DONE

    other = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(owner=user, company=company, title="Other"),
        status=Status.DRAFT,
    )
    change_status(other, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=30))
    ApplicationEvent.objects.create(
        application=other,
        kind=EventKind.INTERVIEW,
        summary="Typed in by hand",
        occurred_at=timezone.now() - dt.timedelta(days=20),
    )
    schedule_interview(other, kind=InterviewKind.PANEL, starts_at=in_days(4))

    insights = analytics.build(user)
    assert insights.interviewed == 2
    assert insights.median_days_to_interview == 7.0, "(4 + 10) / 2: the first interview each"
    assert insights.interviews_held == 2
    assert insights.interviews_ahead == 1
    assert insights.interview_kinds == [("Phone screen", 1), ("Video call", 1)]


# --------------------------------------------------------------------------- API


def bearer(user, *scopes):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_json(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def test_the_api_schedules_reads_moves_and_settles_interviews(client, user, application, recruiter):
    writer = bearer(user, "write")
    reader = bearer(user, "read")
    starts = in_days(3)

    response = post_json(
        client,
        "/api/v1/interviews",
        {
            "application_id": application.pk,
            "kind": "panel",
            "starts_at": starts.isoformat(),
            "location": "Room 4",
            "contact_ids": [recruiter.pk],
            "notes": "Three people.",
        },
        **writer,
    )
    assert response.status_code == 201, response.content
    made = response.json()
    assert made["kind"] == "panel" and made["contact_ids"] == [recruiter.pk]
    assert made["reminder_id"] is not None and made["uid"].endswith("@postulo")
    assert made["calendar_url"].endswith(f"/api/v1/interviews/{made['id']}/calendar.ics")
    scheduled = application.events.get(kind=EventKind.INTERVIEW_SCHEDULED)
    assert scheduled.actor == "API token Agent"

    listed = client.get("/api/v1/interviews", **reader).json()
    assert [i["id"] for i in listed["items"]] == [made["id"]]
    detail = client.get(f"/api/v1/applications/{application.pk}", **reader).json()
    assert detail["interviews"][0]["id"] == made["id"]
    assert detail["next_interview_at"] is not None

    ics = client.get(made["calendar_url"].replace("http://testserver", ""), **reader)
    assert ics.status_code == 200 and f"UID:{made['uid']}" in ics.content.decode()
    feed = client.get("/api/v1/interviews/calendar.ics", **reader)
    assert feed.status_code == 200 and "BEGIN:VEVENT" in feed.content.decode()

    later = in_days(6)
    response = client.patch(
        f"/api/v1/interviews/{made['id']}",
        data=json.dumps({"starts_at": later.isoformat(), "location": "Room 5"}),
        content_type="application/json",
        **writer,
    )
    assert response.status_code == 200, response.content
    moved = response.json()
    assert moved["location"] == "Room 5"
    assert moved["ends_at"].startswith((later + dt.timedelta(hours=1)).isoformat()[:16])

    response = post_json(
        client,
        f"/api/v1/interviews/{made['id']}/outcome",
        {"outcome": "done", "note": "Fine."},
        **writer,
    )
    assert response.status_code == 200 and response.json()["outcome"] == "done"
    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING
    held = application.events.get(kind=EventKind.INTERVIEW)
    assert held.actor == "API token Agent"


def test_the_api_refuses_what_it_should(client, user, other_user, application, company):
    writer = bearer(user, "write")
    stranger = Contact.objects.create(
        owner=user,
        company=Company.objects.create(owner=user, name="Black Mesa"),
        name="Gordon",
    )
    response = post_json(
        client,
        "/api/v1/interviews",
        {
            "application_id": application.pk,
            "starts_at": in_days(2).isoformat(),
            "contact_ids": [stranger.pk],
        },
        **writer,
    )
    assert response.status_code == 422 and "company" in response.json()["detail"]

    response = post_json(
        client,
        "/api/v1/interviews",
        {"application_id": application.pk, "starts_at": in_days(2).isoformat(), "kind": "duel"},
        **writer,
    )
    assert response.status_code == 422

    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(2))
    response = post_json(
        client, f"/api/v1/interviews/{interview.pk}/outcome", {"outcome": "scheduled"}, **writer
    )
    assert response.status_code == 422

    response = post_json(
        client,
        f"/api/v1/applications/{application.pk}/events",
        {"kind": "interview_scheduled", "summary": "x"},
        **writer,
    )
    assert response.status_code == 422

    theirs = bearer(other_user, "read", "write")
    assert client.get(f"/api/v1/interviews/{interview.pk}", **theirs).status_code == 404
    assert client.get("/api/v1/interviews", **theirs).json()["count"] == 0
    reader = bearer(user, "read")
    assert client.get("/api/v1/interviews?state=soon", **reader).status_code == 422


def test_reminders_made_by_interviews_are_ordinary_reminders(client, user, application):
    interview = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=in_days(3))
    reader = bearer(user, "read")
    reminders = client.get("/api/v1/reminders?outstanding=true", **reader).json()["items"]
    assert [r["id"] for r in reminders] == [interview.reminder_id]
    assert Reminder.objects.get(pk=interview.reminder_id).interview == interview
