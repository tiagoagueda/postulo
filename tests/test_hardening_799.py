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


# ----------------------------------------------- the paths that are not an operation (#951)

OFF_THE_OPERATIONS = [
    ("get", "/api/v1/openapi.json"),
    ("get", "/api/v1/no/such/address"),
    ("delete", "/api/v1/me"),
]


@pytest.mark.parametrize(("method", "path"), OFF_THE_OPERATIONS)
def test_a_barred_address_is_refused_on_the_paths_that_are_not_an_operation(client, method, path):
    wrong = {"HTTP_AUTHORIZATION": "Bearer nope", **STRANGER}
    for _ in range(70):
        client.get("/api/v1/me", **wrong)

    response = getattr(client, method)(path, **wrong)

    assert response.status_code == 429
    assert response["Content-Type"] == "application/problem+json"
    assert int(response["Retry-After"]) > 0
    assert response.json()["type"].endswith("rate-limited")


@pytest.mark.parametrize(("method", "path"), OFF_THE_OPERATIONS)
def test_a_guess_made_on_those_paths_is_counted_and_bars_the_address(client, user, method, path):
    wrong = {"HTTP_AUTHORIZATION": "Bearer nope", **STRANGER}
    statuses = [getattr(client, method)(path, **wrong).status_code for _ in range(70)]

    assert statuses[0] == 401
    assert statuses[-1] == 429
    assert 429 not in statuses[: throttle.API_REFUSED.times]
    # The count is the address's, so a right token from it now waits on an operation as well.
    assert client.get("/api/v1/me", **bearer(user), **STRANGER).status_code == 429


@pytest.mark.parametrize(("method", "path"), OFF_THE_OPERATIONS)
def test_a_live_token_still_gets_its_answer_on_those_paths(client, user, method, path):
    answer = getattr(client, method)(path, **bearer(user), **STRANGER)

    assert answer.status_code in (200, 404, 405), path
    assert answer.status_code != 401


def test_no_token_at_all_is_not_counted_as_a_wrong_one(client):
    for _ in range(70):
        assert client.get("/api/v1/openapi.json", **STRANGER).status_code == 401
    assert client.get("/api/v1/openapi.json", **STRANGER).status_code == 401
