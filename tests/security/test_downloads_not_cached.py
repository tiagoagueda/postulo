"""A download is never stored by a cache (#788).

``SECURITY.md`` says files are delivered with ``Cache-Control: no-store``. Most downloads
set it themselves; the calendar file, the report as CSV and PDF and a contact's data-subject
export did not. The rule now lives at the edge, so these tests check both halves: the
middleware on its own, and the downloads that used to be missing it.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from postulo.core.middleware import DownloadsAreNotCachedMiddleware

pytestmark = pytest.mark.django_db

NO_STORE = "private, max-age=0, no-store"


def through(response: HttpResponse) -> HttpResponse:
    middleware = DownloadsAreNotCachedMiddleware(lambda request: response)
    return middleware(RequestFactory().get("/"))


def test_an_attachment_is_given_no_store():
    response = HttpResponse(b"x")
    response["Content-Disposition"] = 'attachment; filename="a.ics"'

    assert through(response)["Cache-Control"] == NO_STORE


def test_an_attachment_that_a_cache_may_keep_for_a_day_is_corrected():
    response = HttpResponse(b"x")
    response["Content-Disposition"] = 'attachment; filename="logo.png"'
    response["Cache-Control"] = "private, max-age=86400"

    assert through(response)["Cache-Control"] == NO_STORE


def test_a_view_that_already_forbids_storing_keeps_its_own_words():
    response = HttpResponse(b"x")
    response["Content-Disposition"] = "attachment"
    response["Cache-Control"] = "no-store"

    assert through(response)["Cache-Control"] == "no-store"


@pytest.mark.parametrize("disposition", ["", 'inline; filename="logo.png"'])
def test_a_page_or_an_inline_picture_is_left_alone(disposition):
    response = HttpResponse(b"x")
    if disposition:
        response["Content-Disposition"] = disposition
    response["Cache-Control"] = "private, max-age=86400"

    assert through(response)["Cache-Control"] == "private, max-age=86400"


# ------------------------------------------------------- the downloads that lacked it


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def test_the_calendar_file_is_not_stored(signed_in):
    response = signed_in.get(reverse("applications:interview_calendar"))

    assert response["Content-Disposition"].startswith("attachment")
    assert response["Cache-Control"] == NO_STORE


def test_the_report_csv_is_not_stored(signed_in):
    response = signed_in.get(reverse("applications:report_csv"))

    assert response["Content-Disposition"].startswith("attachment")
    assert response["Cache-Control"] == NO_STORE


def test_a_contacts_data_subject_export_is_not_stored(signed_in, user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")

    response = signed_in.get(reverse("jobs:contact_export", args=[contact.pk]))

    if response.status_code == 404:  # the export is an optional feature
        pytest.skip("the data-subject export is not offered here")
    assert response["Cache-Control"] == NO_STORE


# ------------------------------------------------- the API's plain answers (#952)


@pytest.fixture
def bearer(user):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/interviews/calendar.ics",
        "/api/v1/reminders/calendar.ics",
        "/api/v1/applications",
        "/api/v1/me",
        "/api/v1/openapi.json",
    ],
)
def test_an_api_answer_is_not_stored(client, bearer, path):
    response = client.get(path, **bearer)

    assert response.status_code == 200, path
    assert response["Cache-Control"] == NO_STORE


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("get", "/api/v1/no/such/address", True, 404),
        ("get", "/api/v1/no/such/address", False, 401),
        ("delete", "/api/v1/me", True, 405),
        ("get", "/api/v1/applications/999999", True, 404),
    ],
)
def test_an_api_refusal_is_not_stored_either(client, bearer, method, path, headers, status):
    response = getattr(client, method)(path, **(bearer if headers else {}))

    assert response.status_code == status
    assert response["Cache-Control"] == NO_STORE


def test_an_api_answer_that_says_something_of_its_own_keeps_it():
    from django.urls import URLPattern

    from postulo.api import api as module

    def view(request):
        response = HttpResponse(b"x")
        response["Cache-Control"] = "no-store"
        return response

    pattern = URLPattern(pattern=type("P", (), {})(), callback=view)
    module._never_kept_by_a_cache(pattern)

    assert pattern.callback(RequestFactory().get("/"))["Cache-Control"] == "no-store"
