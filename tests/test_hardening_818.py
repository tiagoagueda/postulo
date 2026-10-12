"""The host a board recipe is chosen by is the host that is fetched (#818)."""

from __future__ import annotations

import pytest

from postulo.core.addresses import page_address
from postulo.plugins.builtin.boards import recipe_for


@pytest.mark.parametrize(
    "address",
    [
        "https://boards.greenhouse.io@evil.example/jobs/1",
        "https://boards.greenhouse.io:pw@evil.example/jobs/1",
        "https://user@jobs.lever.co/x",
    ],
)
def test_an_address_with_a_user_name_is_not_a_page_address(address):
    with pytest.raises(ValueError, match="user name"):
        page_address(address)


def test_the_recipe_follows_the_host_that_would_be_fetched():
    assert recipe_for("https://boards.greenhouse.io@evil.example/jobs/1") is None
    assert recipe_for("https://evil.example/boards.greenhouse.io/jobs/1") is None


def test_an_ordinary_board_address_still_has_its_recipe():
    assert recipe_for("https://boards.greenhouse.io/acme/jobs/1") is not None


def test_a_port_does_not_hide_the_host():
    assert recipe_for("https://boards.greenhouse.io:443/acme/jobs/1") is not None


def test_ordinary_addresses_are_unchanged():
    assert (
        page_address("https://example.org:8443/jobs/1?x=1") == "https://example.org:8443/jobs/1?x=1"
    )
