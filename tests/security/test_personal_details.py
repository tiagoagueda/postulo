"""The most identifying things a profile holds: a date and a place of birth (#679).

They are named beside the home address in `docs/THREAT-MODEL.md`, with the same rules:
owner-scoped, never in a list, a log, a search result or an error message, printed only
where a CV says so, and removed with the account. The API's profile call has no id in its
address, so nothing names another person's by one; what is held here is the rest.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.documents import rendering
from postulo.documents.models import CV

pytestmark = pytest.mark.django_db

SECRET_DATE = "1987-06-05"
SECRET_PLACE = "Zanzibarville"


def issue(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


@pytest.fixture
def born(user):
    user.profile.birth_date = SECRET_DATE
    user.profile.birth_place = SECRET_PLACE
    user.profile.birth_country = "PT"
    user.profile.save()
    return user


def test_a_capture_token_reaches_none_of_it(client, born):
    token = issue(born, "captures")
    assert client.get("/api/v1/profile", **token).status_code == 403
    assert patch(client, {"birth_date": "2000"}, **token).status_code == 403
    born.profile.refresh_from_db()
    assert born.profile.birth_date == SECRET_DATE


def test_reading_needs_read_and_changing_needs_write(client, born):
    assert patch(client, {"birth_date": "2000"}, **issue(born, "read")).status_code == 403
    assert client.get("/api/v1/profile", **issue(born, "write")).status_code == 403
    born.profile.refresh_from_db()
    assert born.profile.birth_date == SECRET_DATE
    body = client.get("/api/v1/profile", **issue(born, "read")).json()
    assert body["birth_date"] == SECRET_DATE and body["birth_place"] == SECRET_PLACE


def test_one_person_never_reads_or_changes_anothers(client, born, other_user):
    body = client.get("/api/v1/profile", **issue(other_user, "read")).json()
    assert SECRET_DATE not in json.dumps(body) and SECRET_PLACE not in json.dumps(body)
    assert body["birth_date"] == "" and body["birth_country"] == ""
    response = patch(client, {"birth_date": "2001"}, **issue(other_user, "write"))
    assert response.status_code == 200
    born.profile.refresh_from_db()
    assert born.profile.birth_date == SECRET_DATE


def test_no_list_endpoint_carries_them(client, born):
    CV.objects.create(owner=born, name="Main", show_birth_date=True, show_birth_place=True)
    token = issue(born, "read")
    for path in ("/api/v1/cvs", "/api/v1/companies", "/api/v1/listings", "/api/v1/applications"):
        written = client.get(path, **token).content.decode()
        assert SECRET_DATE not in written and SECRET_PLACE not in written, path
    found = client.get("/api/v1/search?q=Zanzibarville", **token).content.decode()
    assert SECRET_PLACE not in found
    assert SECRET_DATE not in client.get("/api/v1/search?q=1987-06-05", **token).content.decode()


def test_a_refusal_does_not_say_what_was_sent_back_to_a_log_or_a_reader(client, born, caplog):
    caplog.set_level(logging.DEBUG)
    response = patch(client, {"birth_date": "1987-02-30"}, **issue(born, "write"))
    assert response.status_code == 422
    assert SECRET_DATE not in response.content.decode()
    assert SECRET_PLACE not in response.content.decode()
    assert SECRET_DATE not in caplog.text and SECRET_PLACE not in caplog.text


def test_the_page_refusing_a_date_does_not_put_it_in_a_message(client, born):
    client.force_login(born)
    html = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "birth_day": "30",
            "birth_month": "2",
            "birth_year": "1987",
        },
    ).content.decode()
    assert "data-invalid" in html and "1987-02-30" not in html


def test_the_pages_that_are_not_the_owners_never_show_them(client, born, other_user):
    client.force_login(other_user)
    for name in ("accounts:profile", "core:home", "documents:cv_list"):
        html = client.get(reverse(name)).content.decode()
        assert SECRET_PLACE not in html and SECRET_DATE not in html, name


def test_nothing_is_printed_by_a_document_that_did_not_choose(born):
    cv = CV.objects.create(owner=born, name="Main")
    assert SECRET_PLACE not in rendering.render_cv_html(cv)
    assert SECRET_PLACE not in rendering.cv_text(cv)
    assert "1987" not in rendering.cv_text(cv)
    cv.show_birth_place = True
    cv.save()
    assert SECRET_PLACE in rendering.cv_text(cv) and "1987" not in rendering.cv_text(cv)
