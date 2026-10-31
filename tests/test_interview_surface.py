"""`schedule_interview` on the plugin surface, refused alike wherever it is called from, and the
calendar's other dated things as plain values a plugin can read (#330)."""

from __future__ import annotations

import datetime as dt
import json
import zoneinfo

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications import agenda, ical, services
from postulo.applications.forms import InterviewForm
from postulo.applications.models import (
    Application,
    ApplicationEvent,
    EventKind,
    Interview,
    InterviewKind,
    InterviewOutcome,
    Offer,
    Reminder,
    Status,
)
from postulo.applications.services import InterviewRefused, schedule_interview
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.plugins import api
from postulo.plugins.testing import assert_imports_only_the_surface

pytestmark = pytest.mark.django_db

PUBLIC = "https://postulo.example"
AUCKLAND = "Pacific/Auckland"


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def application(user, company):
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture
def recruiter(user, company):
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


@pytest.fixture
def elsewhere(user):
    """One of the owner's own contacts, at another company."""
    black_mesa = Company.objects.create(owner=user, name="Black Mesa")
    return Contact.objects.create(owner=user, company=black_mesa, name="Gordon Freeman")


@pytest.fixture
def theirs(other_user, company):
    """Somebody else's contact, at a company of theirs with the same name."""
    their_company = Company.objects.create(owner=other_user, name="Aperture Science")
    return Contact.objects.create(owner=other_user, company=their_company, name="GLaDOS")


def in_days(days: float) -> dt.datetime:
    return (timezone.now() + dt.timedelta(days=days)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_json(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def nothing_written(application) -> bool:
    return (
        not Interview.objects.exists()
        and not Reminder.objects.exists()
        and not application.events.filter(kind=EventKind.INTERVIEW_SCHEDULED).exists()
    )


# ---------------------------------------------------------------------------- the surface


def test_the_new_names_are_the_cores_own():
    from postulo.applications import models

    expected = {
        "schedule_interview": services.schedule_interview,
        "InterviewKind": models.InterviewKind,
        "InterviewRefused": services.InterviewRefused,
        "calendar_items": agenda.items_between,
        "CalendarItem": agenda.Item,
        "CalendarItems": agenda.Items,
        "DayEntry": ical.DayEntry,
        "day_lines": ical.day_lines,
    }
    for name, value in expected.items():
        assert name in api.__all__, name
        assert getattr(api, name) is value, name
    assert issubclass(api.InterviewRefused, ValueError), "a plugin catching the broad one"
    for name in ("Offer", "JobPosting", "absolute_url"):
        assert name not in api.__all__, f"plain values, not records, and no {name} (#330)"


def test_a_plugin_using_them_passes_the_published_check(tmp_path):
    package = tmp_path / "postulo_diary"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import (\n"
        "    CalendarItem, CalendarItems, DayEntry, InterviewKind, InterviewRefused,\n"
        "    calendar_items, day_lines, schedule_interview,\n"
        ")\n",
        encoding="utf-8",
    )

    assert_imports_only_the_surface(package, package="postulo_diary")


# ----------------------------------------------------------------- made by a plugin


def test_a_plugin_makes_an_interview_as_the_form_does(user, application, recruiter):
    interview = api.schedule_interview(
        application,
        kind=api.InterviewKind.PANEL,
        starts_at=in_days(3),
        contacts=[recruiter],
        actor="DAV calendar",
    )

    entry = application.events.get(kind=EventKind.INTERVIEW_SCHEDULED)
    assert entry.actor == "DAV calendar"
    assert list(interview.contacts.all()) == [recruiter]
    reminder = interview.reminder
    assert reminder is not None and reminder.actor == "DAV calendar"
    assert reminder.due_at == interview.starts_at - services.INTERVIEW_REMINDER_LEAD


def test_the_reminder_is_made_by_create_reminder(monkeypatch, user, application):
    """The one way a reminder comes to be (#331), with the interview's actor."""
    calls = []
    original = services.create_reminder

    def spy(owner, **kwargs):
        calls.append((owner, kwargs))
        return original(owner, **kwargs)

    monkeypatch.setattr(services, "create_reminder", spy)

    interview = schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=in_days(3), actor="DAV calendar"
    )

    assert len(calls) == 1
    owner, kwargs = calls[0]
    assert owner == user and kwargs["application"] == application
    assert kwargs["actor"] == "DAV calendar"
    assert interview.reminder.pk == Reminder.objects.get().pk


def test_one_already_over_is_recorded_as_held_through_the_surface(application):
    interview = api.schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=in_days(-2), actor="DAV calendar"
    )

    assert interview.outcome == InterviewOutcome.DONE
    assert interview.reminder is None


# ------------------------------------------------------- refused alike: the service


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"kind": "duel"}, "kind"),
        ({"ends_at": "before"}, "ends_at"),
        ({"ends_at": "same"}, "ends_at"),
        ({"contacts": "elsewhere"}, "contacts"),
        ({"contacts": "theirs"}, "contacts"),
    ],
)
def test_the_service_refuses_before_writing_anything(
    request, application, recruiter, changes, field
):
    starts = in_days(3)
    arguments = {"kind": InterviewKind.VIDEO, "starts_at": starts, "contacts": [recruiter]}
    if changes.get("ends_at") == "before":
        arguments["ends_at"] = starts - dt.timedelta(hours=1)
    elif changes.get("ends_at") == "same":
        arguments["ends_at"] = starts
    elif "contacts" in changes:
        arguments["contacts"] = [recruiter, request.getfixturevalue(changes["contacts"])]
    else:
        arguments.update(changes)

    with pytest.raises(InterviewRefused) as refused:
        api.schedule_interview(application, actor="DAV calendar", **arguments)

    assert refused.value.field == field
    assert str(refused.value)
    assert isinstance(refused.value, ValueError)
    assert nothing_written(application)


def test_every_refusal_is_found_at_once(application, elsewhere):
    starts = in_days(3)
    refusals = services.interview_refusals(
        application,
        kind="duel",
        starts_at=starts,
        ends_at=starts - dt.timedelta(minutes=5),
        contacts=[elsewhere],
    )

    assert [refusal.field for refusal in refusals] == ["kind", "ends_at", "contacts"]


# ---------------------------------------------------------- refused alike: the form


def form_data(starts, **changes):
    data = {
        "kind": InterviewKind.VIDEO,
        "starts_at": timezone.localtime(starts).strftime("%Y-%m-%dT%H:%M"),
        "remind": "on",
    }
    data.update(changes)
    return data


def test_the_form_refuses_an_end_before_the_start_in_the_services_words(client, user, application):
    client.force_login(user)
    starts = in_days(3)
    before = timezone.localtime(starts - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")

    response = client.post(
        reverse("applications:interview_create", args=[application.pk]),
        form_data(starts, ends_at=before),
    )

    assert response.status_code == 200
    (refusal,) = services.interview_refusals(
        application, starts_at=starts, ends_at=starts - dt.timedelta(hours=1)
    )
    assert str(refusal) in response.content.decode()
    assert nothing_written(application)


@pytest.mark.parametrize("who", ["elsewhere", "theirs"])
def test_the_form_refuses_somebody_not_at_the_company(request, user, application, who):
    person = request.getfixturevalue(who)
    form = InterviewForm(
        form_data(in_days(3), contacts=[person.pk]), user=user, application=application
    )

    assert not form.is_valid()
    assert "contacts" in form.errors


def test_the_form_asks_the_service(monkeypatch, user, application):
    """The rule is the service's: whatever it refuses, the form shows under the field."""
    monkeypatch.setattr(
        services,
        "interview_refusals",
        lambda *args, **kwargs: [InterviewRefused("Not that one.", field="kind")],
    )
    form = InterviewForm(form_data(in_days(3)), user=user, application=application)

    assert not form.is_valid()
    assert form.errors["kind"] == ["Not that one."]


def test_the_form_still_keeps_somebody_who_has_moved_on(user, application, recruiter):
    """Changing an interview does not ask about its people (#443)."""
    interview = schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=in_days(3), contacts=[recruiter]
    )
    recruiter.company = Company.objects.create(owner=user, name="Black Mesa")
    recruiter.save()

    form = InterviewForm(
        form_data(in_days(4), contacts=[recruiter.pk]),
        user=user,
        instance=interview,
    )

    assert form.is_valid(), form.errors


# ----------------------------------------------------------- refused alike: the API


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"kind": "duel"}, "'kind' must be one of"),
        ({"ends_at": "before"}, "'ends_at' must be after 'starts_at'."),
        ({"contact_ids": "elsewhere"}, "'contact_ids' must all be people at the application's"),
        ({"contact_ids": "theirs"}, "'contact_ids' must all be people at the application's"),
    ],
)
def test_the_api_is_refused_by_the_service_in_its_own_words(
    request, client, user, application, recruiter, changes, words
):
    starts = in_days(3)
    payload = {
        "application_id": application.pk,
        "kind": "video",
        "starts_at": starts.isoformat(),
        "contact_ids": [recruiter.pk],
    }
    if changes.get("ends_at") == "before":
        payload["ends_at"] = (starts - dt.timedelta(hours=1)).isoformat()
    elif "contact_ids" in changes:
        payload["contact_ids"] = [recruiter.pk, request.getfixturevalue(changes["contact_ids"]).pk]
    else:
        payload.update(changes)

    response = post_json(client, "/api/v1/interviews", payload, **bearer(user, "write"))

    assert response.status_code == 422, response.content
    assert response.json()["detail"].startswith(words)
    assert nothing_written(application)


def test_the_api_refusal_comes_from_the_service(monkeypatch, client, user, application):
    """Nothing is checked in the router any more: the service is asked, and says no."""
    called = []
    original = services.interview_refusals

    def spy(*args, **kwargs):
        called.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "interview_refusals", spy)
    response = post_json(
        client,
        "/api/v1/interviews",
        {"application_id": application.pk, "kind": "duel", "starts_at": in_days(3).isoformat()},
        **bearer(user, "write"),
    )

    assert response.status_code == 422
    assert called and called[0]["kind"] == "duel"


# ------------------------------------------------------------ the calendar, read


@pytest.fixture
def in_auckland(user):
    user.profile.time_zone = AUCKLAND
    user.profile.save(update_fields=["time_zone"])
    return zoneinfo.ZoneInfo(AUCKLAND)


@pytest.fixture
def dated(user, application, company):
    """A deadline, a closing date nobody applied to, and an offer's answer date, next week."""
    day = timezone.localdate() + dt.timedelta(days=7)
    application.deadline = day
    application.applied_at = timezone.now()
    application.save(update_fields=["deadline", "applied_at"])
    JobPosting.objects.create(owner=user, company=company, title="Unapplied", closes_at=day)
    application.status = Status.OFFER
    application.save(update_fields=["status"])
    Offer.objects.create(owner=user, application=application, answer_by=day)
    return day


def test_whole_days_are_plain_values_with_the_feeds_entry(settings, user, dated):
    settings.POSTULO_PUBLIC_URL = PUBLIC + "/"

    read = api.calendar_items(user, dated, dated + dt.timedelta(days=1))

    assert read.withheld == ""
    by_kind = {item.kind: item for item in read}
    assert set(by_kind) == {"deadline", "closing", "answer"}
    for item in read:
        assert isinstance(item, api.CalendarItem)
        assert item.all_day and item.starts_at is None and item.ends_at is None
        assert item.day == dated
        assert isinstance(item.entry, api.DayEntry)
        assert item.entry.url == PUBLIC + item.path
        assert item.uid == item.entry.uid
        assert f"UID:{item.uid}" in "\n".join(api.day_lines(item.entry))
    deadline = by_kind["deadline"]
    assert deadline.title == "Deadline: Test Engineer"
    assert deadline.detail == "Aperture Science"
    assert deadline.over is True, "an application already sent"
    assert by_kind["closing"].over is False


def test_whole_days_are_named_as_the_feed_names_them(settings, rf, user, dated):
    """One deadline, one name, whether a calendar subscribed to the feed or a plugin wrote it."""
    settings.POSTULO_PUBLIC_URL = PUBLIC
    request = rf.get("/")  # the host the request came in on, not the public one

    fed = {entry.uid for entry in agenda.dated_days(user, request.build_absolute_uri)}
    read = {item.uid for item in api.calendar_items(user, dated, dated + dt.timedelta(days=1))}

    assert read and read <= fed


def test_no_whole_days_without_a_public_address(settings, user, application, dated):
    settings.POSTULO_PUBLIC_URL = ""
    schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=timezone.make_aware(dt.datetime.combine(dated, dt.time(10))),
    )

    read = api.calendar_items(user, dated, dated + dt.timedelta(days=1))

    assert [item.kind for item in read] == ["interview"]
    assert "POSTULO_PUBLIC_URL" in read.withheld
    assert api.calendar_items(user, dated, dated, kinds=["deadline"]).withheld


def test_moments_are_in_the_persons_zone_whoever_calls(user, application, in_auckland):
    """A scheduled sync activates nothing; the call reads the person's days anyway."""
    local_day = timezone.now().astimezone(in_auckland).date() + dt.timedelta(days=5)
    # Half past eleven at night in Auckland: the morning of the same date in UTC, and a UTC
    # reading of the day would still find it -- so the next one, half past midnight, is the
    # one that tells the zones apart: it is the day before in UTC.
    late = dt.datetime.combine(local_day, dt.time(23, 30), tzinfo=in_auckland)
    early = dt.datetime.combine(local_day, dt.time(0, 30), tzinfo=in_auckland)
    for moment in (late, early):
        schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=moment)

    with timezone.override(dt.UTC):
        read = api.calendar_items(user, local_day, local_day + dt.timedelta(days=1))

    assert [item.starts_at for item in read] == [early, late]
    for item in read:
        assert item.starts_at.tzinfo.key == AUCKLAND
        assert item.ends_at == item.starts_at + services.DEFAULT_INTERVIEW_LENGTH
        assert item.day == local_day
        assert not item.all_day and item.entry is None
        assert Interview.objects.filter(uid=item.uid).exists()


def test_a_reminder_is_a_moment_with_its_own_uid(user, application):
    reminder = services.create_reminder(
        user, summary="Send the portfolio", due=in_days(2), application=application
    )
    day = timezone.localdate(reminder.due_at)

    read = api.calendar_items(user, day, day + dt.timedelta(days=1), kinds=["reminder"])

    (item,) = read
    assert (item.kind, item.title, item.uid, item.over) == (
        "reminder",
        "Send the portfolio",
        reminder.uid,
        False,
    )
    assert item.path == application.get_absolute_url()


def test_another_persons_calendar_is_not_read(settings, other_user, dated):
    settings.POSTULO_PUBLIC_URL = PUBLIC

    assert len(api.calendar_items(other_user, dated, dated + dt.timedelta(days=1))) == 0


def test_a_kind_nobody_knows_or_a_range_too_long_is_refused(user):
    today = timezone.localdate()

    with pytest.raises(ValueError, match="not kinds"):
        api.calendar_items(user, today, today + dt.timedelta(days=1), kinds=["deadlines"])
    with pytest.raises(ValueError, match="at most"):
        api.calendar_items(user, today, today + dt.timedelta(days=agenda.AGENDA_MAX_DAYS + 1))


def test_nothing_on_the_surface_was_left_without_its_event(user, application):
    """The timeline entry a plugin's interview writes is the same one the form writes."""
    api.schedule_interview(application, kind=InterviewKind.ONSITE, starts_at=in_days(3))

    entry = ApplicationEvent.objects.get(kind=EventKind.INTERVIEW_SCHEDULED)
    assert entry.summary.startswith("On site scheduled for")
