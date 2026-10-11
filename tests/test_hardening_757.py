"""A bar on an untrusted forwarder says which setting to change (#757)."""

from __future__ import annotations

import logging

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from postulo.core import throttle

pytestmark = pytest.mark.django_db

PROXY = "10.0.0.5"


@pytest.fixture(autouse=True)
def _fresh():
    cache.clear()


def guess(client, **extra):
    return client.get(
        reverse("core:metrics"),
        headers={"Authorization": "Bearer nope"},
        REMOTE_ADDR=PROXY,
        **extra,
    )


@override_settings(POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="a-token")
def test_an_untrusted_forwarder_that_has_been_barred_is_named_in_the_log_once(client, caplog):
    forwarding = {"HTTP_X_FORWARDED_FOR": "203.0.113.9"}
    with caplog.at_level(logging.WARNING, logger="postulo.core.throttle"):
        for _ in range(throttle.REFUSED.times + 1):
            guess(client, **forwarding)
        assert guess(client, **forwarding).status_code == 429
        assert guess(client, **forwarding).status_code == 429

    said = [r for r in caplog.records if "POSTULO_TRUSTED_PROXIES" in r.getMessage()]
    assert len(said) == 1 and PROXY in said[0].getMessage()


@override_settings(POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="a-token")
def test_a_direct_client_is_not_accused_of_anything(client, caplog):
    with caplog.at_level(logging.WARNING, logger="postulo.core.throttle"):
        for _ in range(throttle.REFUSED.times + 2):
            guess(client)

    assert not [r for r in caplog.records if "POSTULO_TRUSTED_PROXIES" in r.getMessage()]


def test_the_middleware_remembers_a_forwarder_it_does_not_trust():
    from django.test import RequestFactory

    from postulo.core import proxy

    request = RequestFactory().get("/", REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR="203.0.113.9")
    seen = {}

    def get_response(req):
        seen.update(
            forwarder=req.META.get(proxy.UNTRUSTED_FORWARDER),
            header=req.META.get("HTTP_X_FORWARDED_FOR"),
        )
        from django.http import HttpResponse

        return HttpResponse()

    proxy.TrustedProxyMiddleware(get_response)(request)

    assert seen == {"forwarder": PROXY, "header": None}
