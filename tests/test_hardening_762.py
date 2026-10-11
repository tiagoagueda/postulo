"""Only a token that may read is told whose account it is (#762)."""

from __future__ import annotations

import pytest

from postulo.api.models import ApiToken

pytestmark = pytest.mark.django_db


def me(client, user, *scopes):
    _record, raw = ApiToken.issue(user, "script", scopes=scopes)
    return client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"})


def test_a_captures_only_token_still_checks_out_and_is_not_told_the_address(client, user):
    response = me(client, user, "captures")

    assert response.status_code == 200
    body = response.json()
    assert body["owner"] == "" and user.email not in response.content.decode()
    assert body["scopes"] == ["captures"] and body["name"] == "script"


def test_a_token_that_reads_is_told(client, user):
    assert me(client, user, "captures", "read").json()["owner"] == user.email
