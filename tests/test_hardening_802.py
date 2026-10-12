"""API validation: corrections do not echo input, and enumerated fields are checked (#802)."""

from __future__ import annotations

import json

import pytest

from postulo.api.models import ApiToken

pytestmark = pytest.mark.django_db


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("write", "read"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


LISTING = {"company_name": "Aperture", "title": "Engineer"}


@pytest.mark.parametrize(
    ("field", "value"),
    [("remote_type", "sometimes"), ("employment_type", "forever"), ("salary_period", "fortnight")],
)
def test_an_enumerated_field_outside_its_choices_is_a_422(client, user, field, value):
    response = post(client, "/api/v1/listings", {**LISTING, field: value}, **bearer(user))
    assert response.status_code == 422, response.content


@pytest.mark.parametrize(
    ("field", "value"),
    [("remote_type", "hybrid"), ("employment_type", "contract"), ("salary_period", "month")],
)
def test_a_valid_choice_is_stored_as_sent(client, user, field, value):
    response = post(client, "/api/v1/listings", {**LISTING, field: value}, **bearer(user))
    assert response.status_code in (200, 201), response.content


def test_an_email_that_is_not_one_is_a_422(client, user):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=user, name="Aperture")
    response = post(
        client,
        f"/api/v1/companies/{company.pk}/contacts",
        {"name": "Cave", "email": "not an address"},
        **bearer(user),
    )
    assert response.status_code == 422, response.content


def test_a_refused_correction_does_not_echo_what_was_sent(client, user):
    """The refusal names the field and the rule, not the value somebody typed (#802)."""
    from tests.test_capture_api import PAGE

    secret = "javascript:steal-the-session"
    response = post(
        client,
        "/api/v1/captures",
        {"url": "https://example.org/j/7", "html": PAGE, "data": {"url": secret}},
        **bearer(user, "captures"),
    )
    assert response.status_code == 422, response.content
    assert secret not in response.content.decode()
