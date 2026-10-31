"""The calendar's bar in a browser: the view buttons on a phone (#904), and the picker each
view's name opens (#933)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

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
    picker = page.locator("#calendar-period")
    expect(picker).to_be_visible()
    name = page.locator("[data-period] h2").bounding_box()
    box = picker.bounding_box()
    assert abs((box["x"] + box["width"] / 2) - (name["x"] + name["width"] / 2)) <= 2


# ------------------------------------------------------------ a picker per view (#933)


def open_period(page: Page, live_server, address: str):
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, address)
    page.locator("[data-period] h2 button").click()
    panel = page.locator("#calendar-period")
    expect(panel).to_be_visible()
    return panel


def test_the_month_opens_the_months_of_its_year(live_server, page: Page, applicant):
    panel = open_period(page, live_server, "/applications/calendar/?month=2026-09")

    expect(panel).to_have_attribute("data-mode", "month")
    expect(panel.locator("button[data-month]")).to_have_count(12)
    expect(panel.locator('button[data-month="2026-09"]')).to_have_attribute("aria-pressed", "true")
    expect(panel.locator('button[data-month="2026-09"]')).to_be_focused()
    panel.locator("[data-date-picker-next]").click()
    panel.locator('button[data-month="2027-02"]').click()

    expect(page).to_have_url(re.compile(r"on=2027-02-01"))
    expect(page.locator("[data-period] h2")).to_contain_text("February 2027")


def test_the_week_lights_a_whole_week_and_goes_to_its_first_day(live_server, page: Page, applicant):
    panel = open_period(page, live_server, "/applications/calendar/?view=week&on=2026-09-16")

    expect(panel).to_have_attribute("data-mode", "week")
    expect(panel.locator('td[aria-selected="true"]')).to_have_count(7)
    panel.locator('button[data-date="2026-09-24"]').hover()
    lit = panel.locator("tr[data-preview] button[data-date]")
    expect(lit).to_have_count(7)
    expect(lit.first).to_have_attribute("data-date", "2026-09-21")
    panel.locator('button[data-date="2026-09-24"]').click()

    expect(page).to_have_url(re.compile(r"view=week&on=2026-09-21"))


def test_the_day_goes_to_the_day_pressed(live_server, page: Page, applicant):
    panel = open_period(page, live_server, "/applications/calendar/?view=day&on=2026-09-16")

    expect(panel).to_have_attribute("data-mode", "day")
    expect(panel.locator('button[data-date="2026-09-16"]')).to_be_focused()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    expect(page).to_have_url(re.compile(r"view=day&on=2026-09-23"))


def test_the_agenda_takes_a_first_day_and_a_last(live_server, page: Page, applicant):
    panel = open_period(page, live_server, "/applications/calendar/?view=agenda&on=2026-09-16")

    expect(panel).to_have_attribute("data-mode", "range")
    expect(panel.locator('button[data-date="2026-09-16"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(panel.locator("button[data-in-range]").first).to_be_visible()
    panel.locator('button[data-date="2026-09-20"]').click()
    expect(panel).to_be_visible()
    panel.locator('button[data-date="2026-09-08"]').hover()
    expect(panel.locator("button[data-in-range]")).to_have_count(11)
    panel.locator('button[data-date="2026-09-08"]').click()

    expect(page).to_have_url(re.compile(r"view=agenda&on=2026-09-08&until=2026-09-20"))
    expect(page.locator("[data-period] h2")).to_contain_text("8 Sep 2026 to 20 Sep 2026")
    expect(page.locator('[data-step="next"]')).to_have_attribute("aria-label", "Next 13 days")


def test_without_a_script_the_name_opens_the_form(browser: Browser, live_server, applicant):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, "/applications/calendar/?view=agenda&on=2026-09-16")
        page.locator("[data-period] h2 button").click()
        form = page.locator("#calendar-picker")
        expect(form).to_be_visible()
        form.locator("#calendar-picker-until").fill("2026-09-30")
        form.get_by_role("button", name="Go").click()
        expect(page).to_have_url(re.compile(r"on=2026-09-16&until=2026-09-30"))
        expect(page.locator("[data-period] h2")).to_contain_text("16 Sep 2026 to 30 Sep 2026")
    finally:
        context.close()


# ------------------------------------------------------------------ printing it (#342)


def test_print_beside_the_views_prints_the_view_on_screen(
    live_server, page: Page, applicant, monkeypatch
):
    """*Print* sits beside the views, opens the page that says what the print will hold for
    the view on screen, and its button hands over a PDF."""
    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", lambda html, *a, **k: b"%PDF-1.7 e2e")
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, "/applications/calendar/?view=week&on=2026-09-14")

    views = page.locator("[data-views]").bounding_box()
    button = page.get_by_role("link", name="Print")
    box = button.bounding_box()
    assert abs(box["y"] - views["y"]) <= 4 and box["x"] >= views["x"] + views["width"], (
        "on the same line as the views, after them"
    )
    button.click()

    expect(page).to_have_url(re.compile(r"/applications/calendar/print/\?view=week&on=2026-09-14"))
    expect(page.locator("[data-print-view]")).to_have_text("Week")
    with page.expect_download() as download:
        page.locator("[data-print-pdf]").click()
    assert download.value.suggested_filename == "postulo-calendar-week-2026-09-14.pdf"
