"""The kinds of entry, key and filter, come at the end of the calendar (#903)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

VIEWS = ("month", "week", "day", "agenda")


def calendar(client, **query) -> str:
    return client.get(reverse("applications:calendar"), query).content.decode()


@pytest.mark.parametrize("view", VIEWS)
def test_the_key_comes_after_the_calendar(client, user, view):
    client.force_login(user)

    html = calendar(client, view=view)

    assert html.index("data-legend") > html.index(f'data-calendar="{view}"'), view
    assert html.index("data-legend") < html.index("</main>")


def test_the_search_note_stays_above_the_calendar(client, user):
    client.force_login(user)

    html = calendar(client, q="interview")

    assert html.index("data-search-note") < html.index('data-calendar="month"')


def test_a_kind_still_narrows_from_there(client, user):
    client.force_login(user)

    html = calendar(client, kinds="interview")

    legend = html[html.index("data-legend") :]
    assert "Show everything" in legend
    assert "kinds=" in legend
