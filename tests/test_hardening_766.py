"""Each extreme number or moment is a 422 from the edge, not a 500 from the database (#766)."""

from __future__ import annotations

import json

import pytest

from postulo.api.models import ApiToken
from postulo.applications.models import Application
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

BIG = 2**63
LISTS = [
    "applications",
    "captures",
    "companies",
    "cvs",
    "documents",
    "interviews",
    "letters",
    "listings",
    "offers",
    "reminders",
]


@pytest.fixture
def headers(user):
    _record, raw = ApiToken.issue(
        user, "all", scopes=["captures", "read", "write", "documents:read", "listings:bind"]
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture")
    posting = JobPosting.objects.create(owner=user, company=company, title="Tester")
    return Application.objects.create(owner=user, posting=posting)


def send(client, method, path, headers, body=None):
    kwargs = {"content_type": "application/json", **headers}
    if body is not None:
        kwargs["data"] = json.dumps(body)
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("name", LISTS)
def test_an_offset_past_what_the_database_holds_is_refused(client, headers, name):
    assert client.get(f"/api/v1/{name}", {"offset": BIG}, **headers).status_code == 422


@pytest.mark.parametrize("name", LISTS)
def test_a_moment_the_calendar_cannot_hold_is_refused(client, headers, name):
    response = client.get(
        f"/api/v1/{name}", {"updated_since": "0001-01-01T00:00:00+14:00"}, **headers
    )

    assert response.status_code in (422, 404)  # a list with no cursor says 422 or has none


@pytest.mark.parametrize(
    ("path", "param"),
    [
        ("applications", "company"),
        ("listings", "company"),
        ("interviews", "application"),
        ("offers", "application"),
        ("applications", "after_id"),
    ],
)
def test_an_id_filter_past_what_the_database_holds_is_refused(client, headers, path, param):
    assert client.get(f"/api/v1/{path}", {param: BIG}, **headers).status_code == 422


def test_interviews_since_a_moment_out_of_range_is_refused(client, headers):
    response = client.get("/api/v1/interviews", {"since": "0001-01-01T00:00:00+14:00"}, **headers)

    assert response.status_code == 422


@pytest.mark.parametrize("amount", ["1e30", "1e400", "-5", "12345678901234.5"])
def test_a_salary_the_column_cannot_hold_is_refused(client, headers, amount):
    response = send(
        client,
        "post",
        "/api/v1/listings",
        headers,
        {"company_name": "Aperture", "title": "Tester", "salary_min": amount},
    )

    assert response.status_code == 422


def test_an_interview_with_an_id_or_a_moment_out_of_range_is_refused(client, headers, application):
    base = {"application_id": application.pk, "starts_at": "2030-01-01T10:00:00Z"}

    far = send(
        client, "post", "/api/v1/interviews", headers, {**base, "starts_at": "9999-12-31T23:59:59Z"}
    )
    contact = send(client, "post", "/api/v1/interviews", headers, {**base, "contact_ids": [BIG]})
    early = send(
        client,
        "post",
        "/api/v1/interviews",
        headers,
        {**base, "ends_at": "0001-01-01T00:00:00+14:00"},
    )

    assert far.status_code == contact.status_code == early.status_code == 422


def test_a_reminder_or_an_event_with_a_moment_out_of_range_is_refused(client, headers, application):
    reminder = send(
        client,
        "post",
        "/api/v1/reminders",
        headers,
        {"application_id": application.pk, "summary": "x", "due_at": "0001-01-01T00:00:00+14:00"},
    )

    assert reminder.status_code == 422


def test_ordinary_values_still_go_through(client, headers, application):
    ok = send(
        client,
        "post",
        "/api/v1/reminders",
        headers,
        {"application_id": application.pk, "summary": "x", "due_at": "2030-01-01T10:00:00Z"},
    )
    page = client.get("/api/v1/applications", {"offset": 0, "limit": 5}, **headers)

    assert ok.status_code == 201 and page.status_code == 200
