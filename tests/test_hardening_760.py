"""The API does not tell a stranger which methods an address allows (#760)."""

from __future__ import annotations

import pytest

from postulo.api.models import ApiToken

pytestmark = pytest.mark.django_db


@pytest.fixture
def token(user):
    ApiToken(owner=user, name="script", token_hash=ApiToken.hash_token("a-real-token")).save()
    return "a-real-token"


@pytest.mark.parametrize("method", ["delete", "put", "patch"])
@pytest.mark.parametrize("path", ["/api/v1/applications", "/api/v1/me", "/api/v1/captures/1"])
def test_a_wrong_method_without_a_token_is_a_401_with_no_allow_header(client, method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 401
    assert "Allow" not in response


def test_options_and_head_say_nothing_either(client):
    assert client.options("/api/v1/applications").status_code == 401
    assert "Allow" not in client.options("/api/v1/applications")
    assert client.head("/api/v1/me").status_code in (401, 405) and "Allow" not in client.head(
        "/api/v1/me"
    )


def test_with_a_token_the_405_and_its_allow_header_are_what_they_were(client, token):
    response = client.delete("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 405
    assert "GET" in response["Allow"]


def test_a_stranger_gets_the_same_refusal_for_a_real_address_and_a_made_up_one(client):
    real = client.delete("/api/v1/applications")
    made_up = client.delete("/api/v1/no-such-thing")

    assert real.status_code == made_up.status_code == 401
    assert real.json()["status"] == made_up.json()["status"]


def test_the_root_of_the_api_is_a_401_without_a_token(client):
    assert client.get("/api/v1/").status_code == 401
    assert client.get("/api/v1").status_code in (401, 404, 301)
