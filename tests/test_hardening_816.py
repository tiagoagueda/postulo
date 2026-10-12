"""Multicast, reserved and unspecified addresses are not public destinations (#816)."""

from __future__ import annotations

import ipaddress

import pytest

from postulo.core.destinations import is_public


@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "239.255.255.250",
        "233.252.0.1",
        "ff02::1",
        "ff0e::1",
        "ff05::2",
        "240.0.0.1",
        "255.255.255.255",
        "0.0.0.0",  # noqa: S104 - the address under test, not a bind
        "::",
        "::ffff:224.0.0.1",
    ],
)
def test_it_is_refused(address):
    assert not is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_an_ordinary_public_address_still_is(address):
    assert is_public(ipaddress.ip_address(address))
