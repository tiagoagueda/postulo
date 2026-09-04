"""Applications: intake, status transitions, the timeline, and filtering."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import (
    Application,
    ApplicationEvent,
    EventKind,
    Reminder,
    Status,
)
from postulo.applications.services import (
    change_status,
    create_application,
    get_or_create_company,
    record_event,
)
from postulo.jobs.models import Company, JobPosting


@pytest.fixture
def company(db, user):
    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def application(db, user, company):
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Senior Backend Engineer", location="Paris"
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)


# ------------------------------------------------------------------ transitions


def test_moving_to_applied_stamps_the_date(application):
    assert application.applied_at is None

    change_status(application, Status.APPLIED)
    application.refresh_from_db()

    assert application.status == Status.APPLIED
    assert application.applied_at is not None


def test_the_applied_date_is_never_moved_by_later_transitions(application):
    """It is the date you applied, which is what response times are measured from."""
    change_status(application, Status.APPLIED)
    application.refresh_from_db()
    first = application.applied_at

    change_status(application, Status.INTERVIEWING)
    change_status(application, Status.REJECTED)
    application.refresh_from_db()

    assert application.applied_at == first


def test_a_transition_is_recorded_on_the_timeline(application):
    event = change_status(application, Status.APPLIED, note="Sent through the careers page")

    assert event.kind == EventKind.STATUS_CHANGE
    assert event.from_status == Status.DRAFT
    assert event.to_status == Status.APPLIED
    assert event.body == "Sent through the careers page"


def test_restating_the_same_status_records_nothing(application):
    change_status(application, Status.APPLIED)
    before = application.events.count()

    assert change_status(application, Status.APPLIED) is None
    assert application.events.count() == before


def test_a_settled_outcome_closes_the_application(application):
    change_status(application, Status.APPLIED)
    change_status(application, Status.REJECTED)
    application.refresh_from_db()

    assert not application.is_open
    assert application.closed_at is not None


def test_reopening_clears_the_closing_date(application):
    """Employers do come back weeks after a rejection; the record has to allow it."""
    change_status(application, Status.REJECTED)
    change_status(application, Status.INTERVIEWING)
    application.refresh_from_db()

    assert application.is_open
    assert application.closed_at is None


def test_ghosted_is_distinct_from_rejected(application):
    change_status(application, Status.GHOSTED)
    application.refresh_from_db()

    assert application.status == Status.GHOSTED
    assert not application.is_open
    assert Application.objects.for_user(application.owner).closed().count() == 1


# ---------------------------------------------------------------------- intake


def test_intake_creates_company_posting_and_application(client, user):
    client.force_login(user)
    response = client.post(
        reverse("applications:create"),
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "status": Status.APPLIED,
            "priority": "2",
            "salary_currency": "EUR",
            "salary_period": "year",
        },
    )

    assert response.status_code == 302
    application = Application.objects.for_user(user).get()
    assert application.posting.title == "Research Engineer"
    assert application.posting.company.name == "Black Mesa"
    assert application.status == Status.APPLIED
    assert application.applied_at is not None


def test_intake_reuses_a_company_regardless_of_capitals(db, user, company):
    """Otherwise a few weeks of typing leaves you with Acme, acme and ACME."""
    found = get_or_create_company(user, "aperture science")

    assert found == company
    assert Company.objects.for_user(user).count() == 1


def test_intake_records_the_creation_on_the_timeline(db, user, company):
    application = create_application(
        user,
        company=company,
        posting_data={"title": "Research Engineer"},
        application_data={"status": Status.APPLIED},
    )
    kinds = list(application.events.values_list("kind", flat=True))

    assert EventKind.NOTE in kinds, "the application's own creation should be in the log"
    assert EventKind.STATUS_CHANGE in kinds


def test_intake_rejects_an_upside_down_salary_range(client, user):
    client.force_login(user)
    response = client.post(
        reverse("applications:create"),
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "status": Status.DRAFT,
            "priority": "2",
            "salary_min": "90000",
            "salary_max": "40000",
            "salary_currency": "EUR",
            "salary_period": "year",
        },
    )

    assert response.status_code == 200
    assert not Application.objects.for_user(user).exists()


# -------------------------------------------------------------------- timeline


def test_an_entry_can_be_added_to_the_timeline(client, user, application):
    client.force_login(user)
    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {
            "kind": EventKind.CALL,
            "occurred_at": "2026-09-01T10:30",
            "summary": "Spoke to the recruiter",
            "body": "Twenty minutes, mostly about the team.",
        },
    )

    assert response.status_code == 302
    event = application.events.get(kind=EventKind.CALL)
    assert event.summary == "Spoke to the recruiter"


def test_status_changes_cannot_be_typed_by_hand(client, user, application):
    """The log would otherwise be able to contradict the field it is meant to explain."""
    client.force_login(user)
    client.post(
        reverse("applications:event_create", args=[application.pk]),
        {
            "kind": EventKind.STATUS_CHANGE,
            "occurred_at": "2026-09-01T10:30",
            "summary": "Pretending to be an offer",
        },
    )

    assert not application.events.filter(kind=EventKind.STATUS_CHANGE).exists()


def test_the_timeline_reads_newest_first(application):
    now = timezone.now()
    record_event(application, summary="Older", occurred_at=now - timedelta(days=2))
    record_event(application, summary="Newer", occurred_at=now)

    assert [event.summary for event in application.events.all()][:2] == ["Newer", "Older"]


# ------------------------------------------------------------- views and filters


def test_the_quick_status_action_moves_an_application(client, user, application):
    client.force_login(user)
    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.INTERVIEWING, "note": ""},
    )
    application.refresh_from_db()

    assert response.status_code == 302
    assert application.status == Status.INTERVIEWING


def test_an_unrecognised_status_changes_nothing(client, user, application):
    client.force_login(user)
    client.post(reverse("applications:status", args=[application.pk]), {"status": "promoted"})
    application.refresh_from_db()

    assert application.status == Status.DRAFT


def test_editing_an_application_still_records_the_status_move(client, user, application):
    """The edit form is a convenience; it must not become a way to skip the log."""
    client.force_login(user)
    client.post(
        reverse("applications:update", args=[application.pk]),
        {"status": Status.SCREENING, "priority": "2", "tags": []},
    )
    application.refresh_from_db()

    assert application.status == Status.SCREENING
    assert application.events.filter(to_status=Status.SCREENING).exists()


def test_the_board_shows_only_live_columns(client, user, application):
    change_status(application, Status.REJECTED)
    client.force_login(user)

    response = client.get(reverse("applications:board"))
    statuses = [column["status"] for column in response.context["columns"]]

    assert Status.REJECTED not in statuses
    assert Status.APPLIED in statuses


def test_the_list_can_be_filtered_by_status(client, user, application, company):
    other_posting = JobPosting.objects.create(owner=user, company=company, title="Another role")
    Application.objects.create(owner=user, posting=other_posting, status=Status.OFFER)
    client.force_login(user)

    response = client.get(reverse("applications:list"), {"status": Status.OFFER})

    assert [a.status for a in response.context["applications"]] == [Status.OFFER]


def test_the_list_can_be_searched_by_company(client, user, application):
    client.force_login(user)

    hit = client.get(reverse("applications:list"), {"q": "Aperture"})
    miss = client.get(reverse("applications:list"), {"q": "Umbrella"})

    assert len(hit.context["applications"]) == 1
    assert len(miss.context["applications"]) == 0


# ------------------------------------------------------------------- reminders


def test_a_reminder_becomes_due_when_its_moment_arrives(db, user, application):
    past = Reminder.objects.create(
        owner=user,
        application=application,
        summary="Chase",
        due_at=timezone.now() - timedelta(hours=1),
    )
    Reminder.objects.create(
        owner=user,
        application=application,
        summary="Later",
        due_at=timezone.now() + timedelta(days=3),
    )

    assert list(Reminder.objects.for_user(user).due()) == [past]
    assert past.is_overdue


def test_completing_a_reminder_takes_it_out_of_the_list(client, user, application):
    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase", due_at=timezone.now()
    )
    client.force_login(user)

    client.post(reverse("applications:reminder_complete", args=[reminder.pk]))
    reminder.refresh_from_db()

    assert reminder.is_done
    assert not Reminder.objects.for_user(user).due().exists()


def test_a_completed_reminder_is_not_completed_twice(db, user, application):
    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase", due_at=timezone.now()
    )
    reminder.complete()
    first = reminder.done_at

    reminder.complete()

    assert reminder.done_at == first


# ----------------------------------------------------------------- the dashboard


def test_the_dashboard_suggests_chasing_a_stale_application(client, user, application):
    change_status(application, Status.APPLIED)
    application.applied_at = timezone.now() - timedelta(days=30)
    application.save(update_fields=["applied_at"])
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert application in list(response.context["awaiting_reply"])


def test_the_dashboard_leaves_a_fresh_application_alone(client, user, application):
    change_status(application, Status.APPLIED)
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert list(response.context["awaiting_reply"]) == []


def test_an_event_belonging_to_someone_else_is_not_visible(db, user, other_user, application):
    record_event(application, summary="Private detail")

    assert ApplicationEvent.objects.for_user(user).count() == 1
    assert ApplicationEvent.objects.for_user(other_user).count() == 0
