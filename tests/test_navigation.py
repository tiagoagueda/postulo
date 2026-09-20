"""Choosing what the main navigation shows, and what the wordmark does when Dashboard goes.

The observation behind this: clicking "Postulo" already goes to the dashboard, so on every
page there are two controls for one destination. Hiding one is a per-person choice, off by
default — somebody seeing the instance for the first time has no way of knowing the
wordmark is a link — and when it is taken the wordmark has to do the job properly.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import navigation

pytestmark = pytest.mark.django_db


def appearance(client, **overrides):
    values = {
        "theme": "system",
        # A radio group with a default is always posted by the page that draws it (#292).
        "density": "comfortable",
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
    keep = [key for key in navigation.HIDEABLE if key not in {"dashboard", "companies"}]
    response = appearance(client, navigation=keep)
    assert response.status_code == 200

    user.profile.refresh_from_db()
    assert sorted(user.profile.hidden_nav_items) == ["companies", "dashboard"]

    html = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="dashboard"' not in html and 'data-nav="companies"' not in html
    assert 'data-nav="applications"' in html
    # Hidden from the row, still perfectly reachable.
    assert client.get(reverse("jobs:company_list")).status_code == 200


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

    # Somewhere else, it is a link like any other. The navigation is written twice into the
    # page -- a row for wide screens, a menu for narrow, only ever one of them in the layout
    # (#113) -- so counting the marker across the document counts renderings rather than
    # items. What has to hold is that one item is marked, whatever it is rendered into.
    html = client.get(reverse("applications:list")).content.decode()
    assert 'aria-label="Postulo — dashboard"' in html
    marked = set(re.findall(r'nav-link-active[^>]*data-nav="([^"]+)"', html))
    assert marked == {"applications"}, f"only the page you are on is marked, got {marked}"
    # And marked for a screen reader as well as by the tint (#274).
    assert re.search(r'nav-link-active[^>]*aria-current="page"', html)
    assert html.count('aria-current="page"') == html.count("nav-link-active")


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


# ------------------------------------------- the underline on the current link (#289)


def current_link(html: str) -> str:
    """The masthead's link for the page being shown."""
    found = re.search(r"<a[^>]*nav-link-active[^>]*>", html)
    assert found, "no current navigation link on the page"
    return found.group(0)


def test_the_underline_is_on_by_default(client, user):
    """The default is what the conformance claim rests on, so it stays the marked one."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert "data-nav-underline" not in html, "nothing to say while it is on"
    assert 'aria-current="page"' in current_link(html)
    assert Profile.objects.get(user=user).nav_underline is True


def test_turning_it_off_is_written_onto_the_body(client, user):
    profile = Profile.objects.get(user=user)
    profile.nav_underline = False
    profile.save(update_fields=["nav_underline"])
    client.force_login(user)

    html = client.get(reverse("core:home")).content.decode()

    assert 'data-nav-underline="off"' in html
    # The row is unchanged: only the stylesheet reads the attribute, and the link keeps
    # every other cue it had.
    link = current_link(html)
    assert "nav-link-active" in link and 'aria-current="page"' in link


def test_a_stranger_gets_the_default(client, db):
    """No profile to ask, and the pages a stranger sees keep the marked default."""
    assert "data-nav-underline" not in client.get(reverse("account_login")).content.decode()


def test_accessibility_offers_the_switch_and_saves_it(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:accessibility")).content.decode()
    assert 'name="nav_underline"' in page and "Underline the page you are on" in page

    # A checkbox left unticked posts nothing at all, which is how "off" arrives.
    response = client.post(reverse("settings:accessibility"), {})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).nav_underline is False

    response = client.post(reverse("settings:accessibility"), {"nav_underline": "on"})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).nav_underline is True
