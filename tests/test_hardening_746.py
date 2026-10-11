"""One rule for every next address: https stays https (#746)."""

from __future__ import annotations

import pytest
from allauth.account.adapter import get_adapter
from allauth.core import context
from django.test import RequestFactory
from django.urls import reverse

pytestmark = pytest.mark.django_db


def adapter_for(secure: bool, host="testserver"):
    request = RequestFactory().get("/", secure=secure, HTTP_HOST=host)
    return get_adapter(), context.request_context(request)


@pytest.mark.parametrize(
    ("secure", "url", "expected"),
    [
        (True, "/settings/", True),
        (True, "https://testserver/settings/", True),
        (True, "http://testserver/settings/", False),
        (True, "https://evil.example/", False),
        (False, "http://testserver/settings/", True),
        (False, "https://testserver/settings/", True),
    ],
)
def test_the_scheme_follows_the_instance(secure, url, expected):
    adapter, ctx = adapter_for(secure)
    with ctx:
        assert adapter.is_safe_url(url) is expected


def test_the_sign_in_does_not_follow_an_http_next_on_an_https_instance(client, user, settings):
    settings.SECURE_SSL_REDIRECT = False
    user.set_password("a-fairly-long-password-42")
    user.save()

    response = client.post(
        reverse("account_login") + "?next=http://testserver/settings/",
        {"login": user.email, "password": "a-fairly-long-password-42"},
        secure=True,
    )

    assert response.status_code == 302
    assert not response["Location"].startswith("http://")
