"""One account cannot make the server ask Gravatar without limit (#796)."""

from __future__ import annotations

import httpx
import pytest

from postulo.accounts import avatars
from postulo.plugins import http

pytestmark = pytest.mark.django_db


def test_the_sixth_ask_in_an_hour_is_refused_before_anything_is_requested(monkeypatch, user):
    asked = []

    def handler(request):
        asked.append(1)
        return httpx.Response(404)

    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    profile = user.profile
    outcomes = [avatars.fetch_gravatar(profile) for _ in range(avatars.GRAVATAR_RATE.times + 1)]
    assert outcomes[:-1] == ["none"] * avatars.GRAVATAR_RATE.times
    assert outcomes[-1] == "throttled"
    assert len(asked) == avatars.GRAVATAR_RATE.times


def test_another_account_has_its_own_allowance(monkeypatch, user, other_user):
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    for _ in range(avatars.GRAVATAR_RATE.times + 1):
        avatars.fetch_gravatar(user.profile)
    assert avatars.fetch_gravatar(other_user.profile) == "none"
