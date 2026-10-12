"""The token exchange never follows a redirect, so the client secret goes nowhere else (#794)."""

from __future__ import annotations

import httpx
import pytest

from postulo.plugins import consent, http


def client_answering(handler, seen):
    def build(**kwargs):
        seen.update(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return build


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirecting_token_endpoint_is_a_failure_and_is_not_followed(monkeypatch, status):
    asked = []

    def handler(request):
        asked.append(str(request.url))
        if "elsewhere" in str(request.url):
            return httpx.Response(200, json={"access_token": "stolen"})
        return httpx.Response(status, headers={"Location": "https://elsewhere.example/steal"})

    seen: dict = {}
    monkeypatch.setattr(http, "client", client_answering(handler, seen))
    with pytest.raises(consent.ConsentFailed):
        consent.exchange(
            "https://login.example/token",
            client_id="id",
            client_secret="very-secret",
            form={"grant_type": "refresh_token", "refresh_token": "r"},
        )
    assert asked == ["https://login.example/token"], "nothing else was asked for the secret"
    assert seen["follow_redirects"] is False


def test_an_ordinary_answer_still_works(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        http, "client", client_answering(lambda r: httpx.Response(200, json={"a": 1}), seen)
    )
    assert consent.exchange(
        "https://login.example/token", client_id="i", client_secret="s", form={}
    ) == {"a": 1}
