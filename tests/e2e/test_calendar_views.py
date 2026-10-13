"""The calendar's bar in a browser: the view buttons on a phone (#904)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

VIEWS = ("month", "week", "day", "agenda")


def test_on_a_phone_the_view_buttons_are_icons_still_named(live_server, page: Page, applicant):
    page.set_viewport_size({"width": 320, "height": 700})
    sign_in(page, live_server.url, "/applications/calendar/")

    group = page.locator("[data-views]")
    box = group.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320, "the four fit within 320 pixels"
    for name in ("Month", "Week", "Day", "Agenda"):
        button = group.get_by_role("link", name=name)
        expect(button).to_be_visible()
        expect(button.locator("svg[data-icon]")).to_be_visible()
        label = button.locator(".btn-label")
        assert label.evaluate("el => getComputedStyle(el).position") == "absolute", (
            f"{name}: the word is hidden below sm"
        )


def test_on_a_wide_screen_the_view_buttons_show_icon_and_word(live_server, page: Page, applicant):
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, "/applications/calendar/")

    month = page.locator("[data-views]").get_by_role("link", name="Month")
    expect(month.locator(".btn-label")).to_be_visible()
    expect(month.locator("svg[data-icon='calendar-days']")).to_be_visible()
