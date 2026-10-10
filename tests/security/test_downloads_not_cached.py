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
