"""The API throttle: wrong tokens, the scope refusal, an account's tokens together (#799)."""

from __future__ import annotations

import pytest

from postulo.api.models import ApiToken
from postulo.core import throttle

pytestmark = pytest.mark.django_db

STRANGER = {"REMOTE_ADDR": "203.0.113.50"}


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_an_address_that_keeps_guessing_is_refused_with_429(client):
    wrong = {"HTTP_AUTHORIZATION": "Bearer not-a-token", **STRANGER}
    statuses = [client.get("/api/v1/me", **wrong).status_code for _ in range(70)]
    assert statuses[0] == 401
    assert statuses[-1] == 429
    assert 429 not in statuses[: throttle.API_REFUSED.times]


def test_once_barred_even_a_right_token_from_that_address_waits(client, user):
    wrong = {"HTTP_AUTHORIZATION": "Bearer nope", **STRANGER}
    for _ in range(70):
        client.get("/api/v1/me", **wrong)
    assert client.get("/api/v1/me", **bearer(user), **STRANGER).status_code == 429


def test_another_address_is_not_barred(client, user):
    wrong = {"HTTP_AUTHORIZATION": "Bearer nope", **STRANGER}
    for _ in range(70):
        client.get("/api/v1/me", **wrong)
    assert client.get("/api/v1/me", **bearer(user), REMOTE_ADDR="203.0.113.51").status_code == 200


def test_a_token_without_the_scope_spends_its_allowance(client, user, settings):
    settings.POSTULO_API_RATE = "3/h"
    headers = bearer(user, "captures")
    codes = [client.get("/api/v1/applications", **headers).status_code for _ in range(4)]
    assert codes == [403, 403, 403, 429]


def test_one_account_cannot_multiply_its_allowance_with_more_tokens(client, user, settings):
    settings.POSTULO_API_RATE = "100/h"
    settings.POSTULO_API_ACCOUNT_RATE = "4/h"
    codes = [client.get("/api/v1/me", **bearer(user)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 429]


def test_another_account_has_its_own_account_allowance(client, user, other_user, settings):
    settings.POSTULO_API_ACCOUNT_RATE = "1/h"
    assert client.get("/api/v1/me", **bearer(user)).status_code == 200
    assert client.get("/api/v1/me", **bearer(other_user)).status_code == 200
