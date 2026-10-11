"""The week is one card of seven days with no gaps (#860)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def page(client, **query):
    return client.get(reverse("applications:calendar"), query).content.decode()


def test_the_week_is_one_card_divided_by_borders(client, user):
    client.force_login(user)

    html = page(client, view="week", on="2026-09-15")

    grid = re.search(r'<div class="grid[^"]*"[^>]*data-calendar="week"', html)[0]
    assert "gap-3" not in grid and "divide-x" in grid and "divide-y" in grid
    assert html.count("<section") >= 7
    days = re.findall(r'<section class="([^"]*)" aria-labelledby="day-', html)
    assert len(days) == 7 and all("card" not in c.split() for c in days), "no card per day"
    assert 'class="card overflow-clip p-0"' in html
