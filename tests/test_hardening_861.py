"""The view switch sits beside the period, and Import and Export beside New reminder (#861)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def page(client, **query):
    return client.get(reverse("applications:calendar"), query).content.decode()


def test_the_switch_is_in_the_period_row_aligned_to_the_end(client, user):
    client.force_login(user)

    html = page(client)

    nav = re.search(r"<nav[^>]*data-period.*?</nav>", html, re.S)[0]
    views = re.search(
        r'<div class="button-group ms-auto[^"]*"[^>]*data-views>(.*?)</div>', nav, re.S
    )[1]
    # Each word in its label span, beside its icon, since #904.
    words = re.findall(r'<span class="btn-label">\s*([A-Za-z]+)\s*</span>\s*</a>', views)
    assert words == ["Month", "Week", "Day", "Agenda"]
    assert views.count('aria-current="page"') == 1
    assert 'aria-label="Calendar views"' in nav


def test_the_heading_row_keeps_import_export_and_the_new_buttons(client, user):
    client.force_login(user)

    html = page(client)

    head = html[: html.index("data-period")]
    assert 'data-transfers="calendar"' in head and "New reminder" in head
    assert head.index('data-transfers="calendar"') < head.index("New reminder")
    assert "Calendar views" not in head, "the switch left the heading's row"
