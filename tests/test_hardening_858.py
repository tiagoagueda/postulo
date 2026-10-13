"""Previous, this period and Next are one group, and the period opens a picker (#858)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

UNITS = {
    "month": ("Previous month", "This month", "Next month"),
    "week": ("Previous week", "This week", "Next week"),
    "day": ("Previous day", "Today", "Next day"),
    "agenda": ("Previous 30 days", "Today", "Next 30 days"),
}


def period(client, **query):
    html = client.get(reverse("applications:calendar"), query).content.decode()
    return re.search(r"<nav[^>]*data-period.*?</nav>", html, re.S)[0]


@pytest.mark.parametrize("view", list(UNITS))
def test_every_view_has_the_group_named_for_its_unit(client, user, view):
    client.force_login(user)

    nav = period(client, view=view, on="2026-09-15")

    previous, middle, following = UNITS[view]
    for name in (previous, middle, following):
        assert f'aria-label="{name}"' in nav, name
    assert nav.count('aria-label="Move the calendar"') == 1, "one step group"
    assert 'data-icon="chevron-left"' in nav and 'data-icon="calendar-1"' in nav
    assert 'data-icon="chevron-right"' in nav
    assert ">Previous</span>" in nav and ">Next</span>" in nav


def test_the_period_is_a_button_that_opens_a_date_picker_for_the_view(client, user):
    client.force_login(user)

    nav = period(client, view="week", on="2026-09-15")

    assert 'popovertarget="calendar-picker"' in nav and "<h2" in nav
    assert 'type="date"' in nav and 'name="on"' in nav and 'value="2026-09-15"' in nav
    assert 'name="view" value="week"' in nav
    assert 'name="kinds"' not in nav, "every kind showing is the default and says nothing"


def test_a_narrowed_calendar_keeps_its_kinds_through_the_picker(client, user):
    client.force_login(user)

    nav = period(client, kinds="interview")

    assert 'name="kinds" value="interview"' in nav
    assert 'name="view"' not in nav, "the month needs no view"


def test_the_pickers_day_takes_any_view_to_the_period_holding_it(client, user):
    client.force_login(user)

    page = client.get(
        reverse("applications:calendar"), {"view": "week", "on": "2026-03-04"}
    ).content.decode()

    assert "Week of 2 Mar 2026" in page
