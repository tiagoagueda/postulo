"""Insights, computed from the timeline rather than from current statuses."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import analytics
from postulo.applications.models import Application, Status
from postulo.applications.services import change_status
from postulo.jobs.models import Company, JobPosting


@pytest.fixture
def company(db, user):
    return Company.objects.create(owner=user, name="Aperture Science")


def make_application(user, company, *, title="A role", source="", applied_days_ago=None):
    posting = JobPosting.objects.create(owner=user, company=company, title=title, source=source)
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    if applied_days_ago is not None:
        when = timezone.now() - dt.timedelta(days=applied_days_ago)
        change_status(application, Status.APPLIED, occurred_at=when)
        application.refresh_from_db()
    return application


# ------------------------------------------------------------------- the funnel


def test_a_stage_counts_applications_that_ever_reached_it(user, company):
    """The whole reason the event log exists.

    An application that interviewed and was then rejected has a current status of
    "rejected". Counting current statuses would report no interviews at all, which is
    both wrong and demoralising.
    """
    application = make_application(user, company, applied_days_ago=30)
    change_status(application, Status.INTERVIEWING)
    change_status(application, Status.REJECTED)

    insights = analytics.build(user)
    stages = {stage.status: stage.count for stage in insights.funnel}

    assert stages[Status.INTERVIEWING] == 1, "the interview happened, whatever came after"
    assert stages[Status.APPLIED] == 1


def test_the_funnel_narrows(user, company):
    for index in range(4):
        application = make_application(user, company, title=f"Role {index}", applied_days_ago=20)
        if index < 2:
            change_status(application, Status.INTERVIEWING)
        if index < 1:
            change_status(application, Status.OFFER)

    stages = {stage.status: stage.count for stage in analytics.build(user).funnel}

    assert stages[Status.APPLIED] == 4
    assert stages[Status.INTERVIEWING] == 2
    assert stages[Status.OFFER] == 1


def test_a_draft_is_not_counted_as_sent(user, company):
    make_application(user, company)  # never applied

    insights = analytics.build(user)

    assert insights.total == 1
    assert insights.applied == 0


# ---------------------------------------------------------------- response rate


def test_a_rejection_counts_as_a_reply(user, company):
    """Being turned down is an answer. Only silence is not."""
    application = make_application(user, company, applied_days_ago=20)
    change_status(application, Status.REJECTED)

    insights = analytics.build(user)

    assert insights.responded == 1
    assert insights.response_rate == 100


def test_being_ghosted_does_not_count_as_a_reply(user, company):
    application = make_application(user, company, applied_days_ago=60)
    change_status(application, Status.GHOSTED)

    insights = analytics.build(user)

    assert insights.responded == 0
    assert insights.ghosted == 1
    assert insights.response_rate == 0


def test_the_response_rate_is_a_share_of_what_was_sent(user, company):
    for index in range(4):
        application = make_application(user, company, title=f"Role {index}", applied_days_ago=20)
        if index < 1:
            change_status(application, Status.ACKNOWLEDGED)

    assert analytics.build(user).response_rate == 25


def test_a_small_sample_says_so(user, company):
    make_application(user, company, applied_days_ago=5)

    insights = analytics.build(user)

    assert insights.sample_is_small, "one application is a story, not a rate"


# ------------------------------------------------------------------ reply times


def test_the_median_reply_time_is_measured_from_applying(user, company):
    for days_taken in (2, 10, 30):
        application = make_application(
            user, company, title=f"Role {days_taken}", applied_days_ago=60
        )
        change_status(
            application,
            Status.ACKNOWLEDGED,
            occurred_at=application.applied_at + dt.timedelta(days=days_taken),
        )

    insights = analytics.build(user)

    assert insights.median_days_to_reply == 10
    assert insights.fastest_reply_days == 2
    assert insights.slowest_reply_days == 30


def test_a_median_is_used_rather_than_an_average(user, company):
    """One employer taking a year should not move the number everyone else sets."""
    for days_taken in (3, 4, 5, 365):
        application = make_application(
            user, company, title=f"Role {days_taken}", applied_days_ago=400
        )
        change_status(
            application,
            Status.ACKNOWLEDGED,
            occurred_at=application.applied_at + dt.timedelta(days=days_taken),
        )

    assert analytics.build(user).median_days_to_reply == 4.5


def test_applications_still_waiting_are_counted_separately(user, company):
    make_application(user, company, title="Silent", applied_days_ago=40)
    answered = make_application(user, company, title="Answered", applied_days_ago=40)
    change_status(answered, Status.ACKNOWLEDGED)

    insights = analytics.build(user)

    assert insights.still_waiting == 1


# ---------------------------------------------------------------------- sources


def test_sources_are_compared_on_what_they_produced(user, company):
    referral = make_application(
        user, company, title="Via a friend", source="Referral", applied_days_ago=30
    )
    change_status(referral, Status.INTERVIEWING)
    make_application(user, company, title="From a board", source="Job board", applied_days_ago=30)

    rows = {row.name: row for row in analytics.build(user).sources}

    assert rows["Referral"].applied == 1
    assert rows["Referral"].interviewed == 1
    assert rows["Referral"].response_rate == 100
    assert rows["Job board"].interviewed == 0
    assert rows["Job board"].response_rate == 0


def test_an_unrecorded_source_is_named_rather_than_dropped(user, company):
    make_application(user, company, applied_days_ago=10)

    names = [row.name for row in analytics.build(user).sources]

    assert names == ["Not recorded"]


# --------------------------------------------------------------------- the page


def test_the_page_renders_with_no_data_at_all(client, user):
    client.force_login(user)
    response = client.get(reverse("applications:insights"))

    assert response.status_code == 200
    assert response.context["insights"].applied == 0


def test_the_page_renders_with_data(client, user, company):
    application = make_application(user, company, applied_days_ago=20)
    change_status(application, Status.INTERVIEWING)
    client.force_login(user)

    response = client.get(reverse("applications:insights"))

    assert response.status_code == 200
    assert response.context["insights"].applied == 1


def test_insights_never_include_another_account(user, other_user, company):
    make_application(user, company, applied_days_ago=10)

    assert analytics.build(other_user).total == 0
