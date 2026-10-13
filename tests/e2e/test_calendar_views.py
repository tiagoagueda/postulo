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


# ---------------------------------------------------------------- the period, centred (#900)


def centre_gap(page: Page) -> float:
    """How far the period's name sits from the middle of the bar, in pixels."""
    nav = page.locator("[data-period]").bounding_box()
    name = page.locator("[data-period] h2").bounding_box()
    return abs((name["x"] + name["width"] / 2) - (nav["x"] + nav["width"] / 2))


@pytest.mark.parametrize("width", [1440, 1280, 390])
@pytest.mark.parametrize("view", VIEWS)
def test_the_period_is_centred_in_every_view(live_server, page: Page, applicant, view, width):
    page.set_viewport_size({"width": width, "height": 800})
    sign_in(page, live_server.url, f"/applications/calendar/?view={view}")

    assert centre_gap(page) <= 2, f"{view} at {width}"


def test_the_period_is_centred_right_to_left(live_server, page: Page, applicant):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    page.set_viewport_size({"width": 1440, "height": 800})
    for view in VIEWS:
        sign_in(page, live_server.url, f"/applications/calendar/?view={view}")
        assert centre_gap(page) <= 2, view


def test_the_picker_opens_under_the_middle_of_the_name(live_server, page: Page, applicant):
    page.set_viewport_size({"width": 1440, "height": 800})
    sign_in(page, live_server.url, "/applications/calendar/")

    page.locator("[data-period] h2 button").click()
    picker = page.locator("#calendar-picker")
    expect(picker).to_be_visible()
    name = page.locator("[data-period] h2").bounding_box()
    box = picker.bounding_box()
    assert abs((box["x"] + box["width"] / 2) - (name["x"] + name["width"] / 2)) <= 2
