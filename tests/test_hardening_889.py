"""A connection's card may offer links to the service's own space (#889)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.plugins import registry, views
from postulo.plugins.models import Connection

from .test_syncs import MirrorSync, a_sync, mirror  # noqa: F401  (registers the sync plugin)

pytestmark = pytest.mark.django_db


@pytest.fixture
def offering(monkeypatch):
    """The test sync plugin, now with a ``links`` method that says what it is told."""
    offered: list = []
    monkeypatch.setattr(MirrorSync, "links", lambda self, config: offered, raising=False)
    return offered


def test_the_links_a_plugin_offers_are_drawn_as_external_links(client, user, offering):
    connection = a_sync(user)
    offering.extend(
        [("My space", "https://example.org/me"), ("Messages", "https://example.org/messages")]
    )

    client.force_login(user)
    html = client.get(reverse("connections:list")).content.decode()

    assert 'href="https://example.org/me"' in html
    assert 'rel="noopener noreferrer external"' in html and 'target="_blank"' in html
    assert "(opens in a new tab)" in html
    assert connection.pk


def test_only_https_pages_with_a_label_are_kept_and_at_most_six(user, offering):
    connection = a_sync(user)
    offering.extend(
        [
            ("Plain", "http://example.org/"),
            ("Script", "javascript:alert(1)"),
            ("No scheme", "example.org/page"),
            ("", "https://example.org/unnamed"),
            ("Break", "https://example.org/a\nb"),
            ("Not a pair",),
            ("Ok 1", "https://example.org/1"),
            ("Ok 2", "https://example.org/2"),
            ("Ok 3", "https://example.org/3"),
            ("Ok 4", "https://example.org/4"),
            ("Ok 5", "https://example.org/5"),
            ("Ok 6", "https://example.org/6"),
            ("Ok 7", "https://example.org/7"),
        ]
    )

    links = views._links(connection.plugin_instance, connection)

    assert [label for label, _address in links] == [f"Ok {n}" for n in range(1, 7)]
    assert all(address.startswith("https://") for _label, address in links)


def test_a_plugin_whose_links_fail_costs_the_card_its_links_and_not_the_page(
    client, user, monkeypatch
):
    a_sync(user)

    def broken(self, config):
        raise RuntimeError("no")

    monkeypatch.setattr(MirrorSync, "links", broken, raising=False)
    client.force_login(user)

    assert client.get(reverse("connections:list")).status_code == 200


def test_a_plugin_with_no_links_method_draws_none(client, user):
    a_sync(user)
    client.force_login(user)

    html = client.get(reverse("connections:list")).content.decode()

    assert "data-connection-links" not in html
    assert registry and Connection
