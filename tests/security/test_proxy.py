"""What Postulo believes from whatever sits in front of it.

``X-Forwarded-Proto`` and ``X-Forwarded-For`` are ordinary request headers. Setting
``SECURE_PROXY_SSL_HEADER`` tells Django to believe the first one, and Django's own
documentation warns that this is only safe when a proxy is guaranteed to have stripped
whatever the client sent. Postulo cannot guarantee that — the Compose file publishes a
port, and an instance reached directly is a normal thing — so the headers are believed
from a proxy and nowhere else, and a proxy is something at an address the operator has
said is one.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core import proxy

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- the address test


def test_a_private_address_is_a_proxy_and_a_public_one_is_not():
    assert proxy.is_trusted("127.0.0.1")
    assert proxy.is_trusted("172.18.0.5"), "a container on a Compose network"
    assert proxy.is_trusted("192.168.1.10"), "a proxy on the same LAN"
    assert proxy.is_trusted("::1")

    assert not proxy.is_trusted("203.0.113.7"), "straight off the internet"
    assert not proxy.is_trusted("2001:db8::1")
    assert not proxy.is_trusted(""), "no address at all is not a proxy"
    assert not proxy.is_trusted("not-an-address")


def test_the_ranges_can_be_narrowed_or_widened(settings):
    settings.POSTULO_TRUSTED_PROXIES = ["203.0.113.7/32"]
    assert proxy.is_trusted("203.0.113.7"), "a proxy on its own public host"
    assert not proxy.is_trusted("192.168.1.10"), "and nothing else, once said explicitly"

    settings.POSTULO_TRUSTED_PROXIES = []
    assert not proxy.is_trusted("127.0.0.1"), "an empty list trusts nobody"


def test_an_unparseable_range_is_ignored_rather_than_widening_anything(settings):
    settings.POSTULO_TRUSTED_PROXIES = ["10.0.0.0/8", "not a network", "definitely/not"]
    assert proxy.is_trusted("10.1.2.3")
    assert not proxy.is_trusted("203.0.113.7")


# --------------------------------------------------------------- stripping the lie


def test_a_direct_request_cannot_claim_it_arrived_over_https(client, settings):
    """The bug: anybody could send the header and be counted as secure."""
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    response = client.get(
        reverse("account_login"),
        HTTP_X_FORWARDED_PROTO="https",
        REMOTE_ADDR="203.0.113.7",
    )
    assert response.wsgi_request.META.get("HTTP_X_FORWARDED_PROTO") is None
    assert not response.wsgi_request.is_secure()


def test_a_proxy_saying_the_same_thing_is_believed(client, settings):
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    response = client.get(
        reverse("account_login"),
        HTTP_X_FORWARDED_PROTO="https",
        REMOTE_ADDR="172.18.0.5",
    )
    assert response.wsgi_request.is_secure(), "a reverse proxy is the whole reason this exists"


def test_every_forwarding_header_goes_together(client):
    response = client.get(
        reverse("account_login"),
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_PROTO="https",
        HTTP_X_FORWARDED_FOR="10.0.0.1",
        HTTP_X_FORWARDED_HOST="somewhere.else.example",
        HTTP_X_FORWARDED_PORT="443",
        HTTP_FORWARDED="proto=https",
    )
    meta = response.wsgi_request.META
    for header in proxy.FORWARDING_HEADERS:
        assert header not in meta, header


# ------------------------------------------------------- who the request is from


def test_behind_a_proxy_the_client_address_is_the_client_not_the_proxy(client):
    """Rate limits key on REMOTE_ADDR, and behind a proxy that was the proxy for everybody.

    A limit meant to slow one persistent stranger down would then be shared by every
    person on the instance, and that stranger could exhaust it for all of them.
    """
    response = client.get(
        reverse("account_login"),
        REMOTE_ADDR="172.18.0.5",
        HTTP_X_FORWARDED_FOR="203.0.113.7",
    )
    assert response.wsgi_request.META["REMOTE_ADDR"] == "203.0.113.7"
    assert response.wsgi_request.META["POSTULO_PROXY_ADDR"] == "172.18.0.5"


def test_a_client_cannot_choose_its_own_address_by_prefixing_the_list():
    """The list grows on the left, so only the right-hand end can be relied on."""
    # Two proxies, then the client: the entry to trust is the last untrusted one.
    assert proxy.client_address("172.18.0.5", "203.0.113.7, 10.0.0.9") == "203.0.113.7"
    # A client that invents a prefix does not get to be believed about it.
    assert proxy.client_address("172.18.0.5", "1.1.1.1, 203.0.113.7") == "203.0.113.7"
    # Nothing usable in the header leaves the peer address standing.
    assert proxy.client_address("172.18.0.5", "nonsense") == "172.18.0.5"
    assert proxy.client_address("172.18.0.5", "") == "172.18.0.5"
    # Every hop trusted means the request never came from anywhere else.
    assert proxy.client_address("172.18.0.5", "10.0.0.9, 10.0.0.8") == "172.18.0.5"


def test_a_port_on_the_forwarded_address_is_dropped():
    assert proxy.client_address("172.18.0.5", "203.0.113.7:51234") == "203.0.113.7"
    assert proxy.client_address("172.18.0.5", "[2001:db8::1]:51234") == "2001:db8::1"


def test_a_direct_request_keeps_its_own_address(client):
    response = client.get(
        reverse("account_login"),
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_FOR="10.0.0.1",
    )
    assert response.wsgi_request.META["REMOTE_ADDR"] == "203.0.113.7"
