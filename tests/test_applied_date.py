"""When an application was sent, and every door that has to be able to say so (#222).

`applied_at` was written only when an application passed through the literal *Applied*.
Recording a reply you already had — straight to *Interviewing*, or to *Rejected* — left it
null, and everything measured from that date then ignored the application: the sent figure,
reply and interview times, sources, industries, by-month, "sent recently", the API's `since`
filter, and the report handed to an employment office.

The second half is that nothing could say *when*. Everything assumed today, which is wrong
for the case the wiki calls common: recording a search already under way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, EventKind, Status
from postulo.applications.services import change_status, moment_for
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

LAST_MONTH = dt.date(2026, 8, 12)


@pytest.fixture
def draft(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)


# ------------------------------------------------------- reaching a status that means sent


@pytest.mark.parametrize(
    "status",
    [Status.APPLIED, Status.ACKNOWLEDGED, Status.INTERVIEWING, Status.REJECTED, Status.GHOSTED],
)
def test_any_status_that_means_sent_stamps_the_date(draft, status):
    change_status(draft, status)

    draft.refresh_from_db()
    assert draft.applied_at is not None, f"{status} means it went out"


def test_a_draft_withdrawn_was_never_sent(draft):
    change_status(draft, Status.WITHDRAWN)

    draft.refresh_from_db()
    assert draft.applied_at is None, "abandoning a draft does not invent an application"


def test_withdrawing_something_sent_keeps_the_date_it_had(draft):
    change_status(draft, Status.APPLIED, occurred_at=moment_for(LAST_MONTH))
    draft.refresh_from_db()
    sent_on = draft.applied_at

    change_status(draft, Status.WITHDRAWN)

    draft.refresh_from_db()
    assert draft.applied_at == sent_on


def test_the_date_is_the_one_given_not_today(draft):
    change_status(draft, Status.INTERVIEWING, occurred_at=moment_for(LAST_MONTH))

    draft.refresh_from_db()
    assert timezone.localtime(draft.applied_at).date() == LAST_MONTH
    event = draft.events.filter(to_status=Status.INTERVIEWING).get()
    assert timezone.localtime(event.occurred_at).date() == LAST_MONTH, "the timeline agrees"


def test_a_date_keeps_its_day_whatever_the_time_zone(settings):
    """Midday, so a date does not slip to the day before for anybody west of the server."""
    settings.TIME_ZONE = "Pacific/Auckland"
    moment = moment_for(LAST_MONTH)

    assert timezone.localtime(moment).date() == LAST_MONTH


# --------------------------------------------------------------------- every door in turn


def test_the_intake_form_takes_the_date_it_was_sent(client, user):
    client.force_login(user)

    response = client.post(
        reverse("applications:create"),
        {
            "company_name": "Aperture Science",
            "title": "Test Engineer",
            "status": Status.INTERVIEWING,
            "applied_on": LAST_MONTH.isoformat(),
            "priority": 2,
        },
    )

    assert response.status_code == 302, response.context["form"].errors if response.context else ""
    application = Application.objects.get(owner=user)
    assert application.status == Status.INTERVIEWING
    assert timezone.localtime(application.applied_at).date() == LAST_MONTH


def test_applying_to_a_listing_takes_the_date(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    listing = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    client.force_login(user)

    client.post(
        reverse("listings:apply", args=[listing.pk]),
        {"status": Status.APPLIED, "applied_on": LAST_MONTH.isoformat(), "priority": 2},
    )

    application = Application.objects.get(owner=user)
    assert timezone.localtime(application.applied_at).date() == LAST_MONTH


def test_the_api_takes_the_date(client, user):
    from postulo.api.models import ApiToken

    token = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    response = client.post(
        "/api/v1/applications",
        data={
            "company_name": "Aperture Science",
            "title": "Test Engineer",
            "status": "rejected",
            "applied_on": LAST_MONTH.isoformat(),
        },
        content_type="application/json",
        headers={"authorization": f"Bearer {token[1]}"},
    )

    assert response.status_code in (200, 201), response.content
    application = Application.objects.get(owner=user)
    assert application.status == "rejected"
    assert timezone.localtime(application.applied_at).date() == LAST_MONTH


def test_nothing_given_still_means_today(client, user):
    client.force_login(user)

    client.post(
        reverse("applications:create"),
        {
            "company_name": "Aperture Science",
            "title": "Test Engineer",
            "status": Status.APPLIED,
            "priority": 2,
        },
    )

    application = Application.objects.get(owner=user)
    assert timezone.localtime(application.applied_at).date() == timezone.localdate()


# ------------------------------------------------------------------ a spreadsheet's rows


MAPPING = ["company", "role", "status", "applied_at"]


def import_rows(user, rows):
    """Parse a little sheet and import it, handing back both halves to assert on."""
    from postulo.core.csv_import import Sheet, parse_rows, perform

    sheet = Sheet(
        headers=["Company", "Role", "Status", "Applied"],
        rows=rows,
        filename="search.csv",
        delimiter=",",
        encoding="utf-8",
    )
    parsed = parse_rows(sheet, MAPPING)
    return parsed, perform(user, sheet, MAPPING)


def test_a_status_without_a_date_keeps_the_status(user):
    parsed, report = import_rows(user, [["Black Mesa", "Research Engineer", "Rejected", ""]])

    assert parsed[0].becomes == "application", "it happened; only the date is missing"
    application = Application.objects.get(owner=user)
    assert application.status == Status.REJECTED, "the status is not thrown away"
    assert application.applied_at is None, "and today is not invented for it"
    assert application.events.filter(kind=EventKind.OTHER, summary__icontains="unknown").exists()
    assert report.applications == 1


def test_a_row_that_says_draft_stays_a_listing(user):
    parsed, report = import_rows(user, [["Black Mesa", "Research Engineer", "Draft", "2026-08-12"]])

    assert parsed[0].becomes == "listing", "said plainly: not sent"
    assert not Application.objects.filter(owner=user).exists()
    assert report.listings == 1


def test_a_dated_row_with_no_status_is_an_application_as_before(user):
    _parsed, report = import_rows(user, [["Black Mesa", "Research Engineer", "", "2026-08-12"]])

    application = Application.objects.get(owner=user)
    assert application.status == Status.APPLIED
    assert timezone.localtime(application.applied_at).date() == LAST_MONTH
    assert report.applications == 1
