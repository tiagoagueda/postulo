"""Choosing what the main navigation shows, and what the wordmark does when Dashboard goes.

The observation behind this: clicking "Postulo" already goes to the dashboard, so on every
page there are two controls for one destination. Hiding one is a per-person choice, off by
default — somebody seeing the instance for the first time has no way of knowing the
wordmark is a link — and when it is taken the wordmark has to do the job properly.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core import navigation

pytestmark = pytest.mark.django_db


def appearance(client, **overrides):
    values = {
        "theme": "system",
        "quiet_after_days": 14,
        "navigation": list(navigation.HIDEABLE),
    }
    values.update(overrides)
    return client.post(reverse("settings:appearance"), values, follow=True)


# ------------------------------------------------------------------- the list


def test_every_item_is_offered_and_shown_by_default(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    for item in navigation.ITEMS:
        assert f'data-nav="{item.key}"' in html, item.key
    assert user.profile.hidden_nav_items == []


def test_a_person_can_leave_items_out(client, user):
    client.force_login(user)
    keep = [key for key in navigation.HIDEABLE if key not in {"dashboard", "board"}]
    response = appearance(client, navigation=keep)
    assert response.status_code == 200

    user.profile.refresh_from_db()
    assert sorted(user.profile.hidden_nav_items) == ["board", "dashboard"]

    html = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="dashboard"' not in html and 'data-nav="board"' not in html
    assert 'data-nav="applications"' in html
    # Hidden from the row, still perfectly reachable.
    assert client.get(reverse("applications:board")).status_code == 200


def test_the_settings_page_shows_what_is_on(client, user):
    client.force_login(user)
    appearance(client, navigation=["listings", "applications"])
    html = client.get(reverse("settings:appearance")).content.decode()
    import re

    checked = set(re.findall(r'value="(\w+)"[^>]*checked', html))
    assert {"listings", "applications"} <= checked
    assert "dashboard" not in checked
    assert "Show in the navigation" in html


def test_the_stored_value_is_what_is_hidden_so_a_new_item_appears(client, user):
    """Recording the hidden ones is what makes an upgrade show its new item to everybody."""
    client.force_login(user)
    appearance(client, navigation=["dashboard"])
    user.profile.refresh_from_db()
    assert "dashboard" not in user.profile.hidden_nav_items
    assert "listings" in user.profile.hidden_nav_items

    # A person who has never touched the setting has nothing hidden at all.
    other = navigation.visible_items(None)
    assert [item.key for item in other] == list(navigation.HIDEABLE)


# ---------------------------------------------------------------- the wordmark


def test_the_wordmark_says_where_it_goes_once_dashboard_is_hidden(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert "— dashboard" not in html, "with the link there, the wordmark is just a name"

    keep = [key for key in navigation.HIDEABLE if key != "dashboard"]
    appearance(client, navigation=keep)

    html = client.get(reverse("core:home")).content.decode()
    assert 'aria-label="Postulo — dashboard"' in html
    assert "nav-link-active" in html, "and it carries the active style on the dashboard"

    # Somewhere else, it is a link like any other.
    html = client.get(reverse("applications:list")).content.decode()
    assert 'aria-label="Postulo — dashboard"' in html
    assert html.count("nav-link-active") == 1, "only the page you are on is marked"


def test_a_visitor_who_is_not_signed_in_sees_the_plain_wordmark(client):
    html = client.get(reverse("core:home")).content.decode()
    assert "— dashboard" not in html
    assert 'data-nav="dashboard"' not in html


# ------------------------------------------------------------------ the model


def test_the_items_are_the_navigation_and_nothing_else():
    assert navigation.ITEMS[0].key == "dashboard", "first, because it is the one to hide"
    assert set(navigation.BY_KEY) == set(navigation.HIDEABLE)
    for item in navigation.ITEMS:
        assert item.active_names[0] == item.url_name
    assert [key for key, _label in navigation.choices()] == list(navigation.HIDEABLE)
