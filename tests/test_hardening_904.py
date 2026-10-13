"""Each calendar view button carries its icon, and is named whichever shows (#904)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.applications import agenda

pytestmark = pytest.mark.django_db

ICONS = {
    "month": ("Month", "calendar-days"),
    "week": ("Week", "calendar-range"),
    "day": ("Day", "calendar-clock"),
    "agenda": ("Agenda", "list-clock"),
}


def switcher(client, **query):
    html = client.get(reverse("applications:calendar"), query).content.decode()
    return re.search(r"<div[^>]*data-views[^>]*>.*?</div>", html, re.S)[0]


def test_every_view_has_its_icon_its_word_and_its_name(client, user):
    client.force_login(user)

    group = switcher(client)

    for view, (label, icon) in ICONS.items():
        button = re.search(rf'<a[^>]*data-view="{view}"[^>]*>.*?</a>', group, re.S)[0]
        assert f'aria-label="{label}"' in button
        assert f'data-icon="{icon}"' in button
        assert f'<span class="btn-label">{label}</span>' in button


def test_the_open_view_is_still_marked(client, user):
    client.force_login(user)

    group = switcher(client, view="week")

    week = re.search(r'<a[^>]*data-view="week"[^>]*>', group)[0]
    assert 'aria-current="page"' in week and 'data-variant="primary"' in week


def test_the_day_does_not_borrow_todays_icon():
    assert agenda.VIEW_ICONS["day"] != "calendar-1"
    assert set(agenda.VIEW_ICONS) == set(agenda.VIEWS)
