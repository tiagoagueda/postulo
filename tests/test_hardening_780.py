"""What Postulo fetches from strangers is fetched from the web's ports only (#780)."""

from __future__ import annotations

import ipaddress
from unittest import mock

import pytest

from postulo.plugins import http, public_addresses
from postulo.plugins.public_addresses import UnsafeURL, require_web_port, validate_public_url

PUBLIC = [ipaddress.ip_address("93.184.216.34")]


@pytest.fixture(autouse=True)
def a_public_host(settings):
    settings.POSTULO_FETCH_PORTS = (80, 443, 8080, 8443)
    with mock.patch.object(public_addresses, "_addresses_for", return_value=PUBLIC):
        yield


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/",
        "https://example.org:443/x",
        "http://example.org:80/",
        "http://example.org:8080/",
        "https://example.org:8443/",
    ],
)
def test_the_ordinary_web_ports_are_fetched(url):
    require_web_port(url)
    assert validate_public_url(url)


@pytest.mark.parametrize("port", [22, 25, 3306, 5432, 6379, 8000, 9100, 65535])
def test_any_other_port_of_a_public_host_is_refused_for_a_stranger_fetch(port):
    with pytest.raises(UnsafeURL, match="ordinary web ports"):
        require_web_port(f"https://example.org:{port}/")


def test_a_connection_may_still_use_any_port():
    assert validate_public_url("https://paperless.example.org:8000/")
    http.check_destination("https://paperless.example.org:8000/")


def test_a_star_lifts_the_restriction(settings):
    settings.POSTULO_FETCH_PORTS = "*"

    require_web_port("https://example.org:6379/")


def test_the_operator_can_widen_the_list(settings):
    settings.POSTULO_FETCH_PORTS = (443, 8000)

    require_web_port("https://example.org:8000/")
    with pytest.raises(UnsafeURL):
        require_web_port("https://example.org:80/")


def test_a_redirect_to_an_odd_port_is_refused_by_the_public_only_client():
    request = http.httpx.Request("GET", "https://example.org:6379/")

    with pytest.raises(http.DestinationRefused):
        http._public_only(request)
