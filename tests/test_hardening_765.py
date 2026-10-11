"""The write scope says plainly that it can read what it touches (#765)."""

from __future__ import annotations

import json

import pytest

from postulo.api.models import SCOPES, ApiToken
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def test_the_scope_label_and_the_api_description_say_so(client, user):
    assert "whole record" in str(SCOPES["write"])
    _record, raw = ApiToken.issue(user, "docs", scopes=["read"])
    schema = client.get("/api/v1/openapi.json", headers={"Authorization": f"Bearer {raw}"}).json()
    description = schema["info"]["description"]

    assert "can read every record it can touch" in description
    assert "whether or not the call changed anything" in description


def test_the_behaviour_the_words_describe_is_the_behaviour(client, user):
    """A write-only token cannot GET a company but is answered with its contacts by a PATCH {}."""
    company = Company.objects.create(owner=user, name="Aperture")
    _record, raw = ApiToken.issue(user, "writer", scopes=["write"])
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    assert client.get(f"/api/v1/companies/{company.pk}", **headers).status_code == 403
    patched = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps({}),
        content_type="application/json",
        **headers,
    )

    assert patched.status_code == 200 and "contacts" in patched.json()
