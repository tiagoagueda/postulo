"""A calendar beside the date of birth's boxes, which stay the form (#863)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

PROFILE = "/accounts/profile/"


def open_calendar(page: Page, live_server, applicant):
    sign_in(page, live_server.url, PROFILE)
    page.locator("[data-birth-picker]").click()
    panel = page.locator("#birth-picker")
    expect(panel).to_be_visible()
    return panel


def test_with_a_script_the_calendar_fills_the_three_boxes(page: Page, live_server, applicant):
    sign_in(page, live_server.url, PROFILE)
    page.locator("[data-birth-picker]").click()
    panel = page.locator("#birth-picker")
    panel.locator("[data-date-picker-year]").select_option("1990")
    panel.locator("[data-date-picker-month]").select_option("2")  # March
    panel.locator('button[data-date="1990-03-07"]').click()

    expect(page.locator("#id_birth_year")).to_have_value("1990")
    expect(page.locator("#id_birth_month")).to_have_value("3")
    expect(page.locator("#id_birth_day")).to_have_value("7")
    expect(panel).to_be_hidden()
    expect(page.locator("[data-birth-picker]")).to_be_focused()


def test_the_boxes_still_take_a_year_alone_and_the_calendar_opens_on_a_whole_date(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url, PROFILE)
    page.locator("#id_birth_year").fill("1985")
    page.locator("#id_birth_month").fill("11")
    page.locator("#id_birth_day").fill("2")

    page.locator("[data-birth-picker]").click()

    panel = page.locator("#birth-picker")
    expect(panel.locator("[data-date-picker-year]")).to_have_value("1985")
    expect(panel.locator("[data-date-picker-month]")).to_have_value("10")
    chosen = panel.locator('button[data-date="1985-11-02"]')
    expect(chosen).to_have_attribute("aria-pressed", "true")
    expect(chosen).to_be_focused()


@pytest.mark.parametrize("width", [320, 360, 1280])
def test_the_boxes_and_the_button_stay_on_one_line_level_with_each_other(
    page: Page, live_server, applicant, width
):
    page.set_viewport_size({"width": width, "height": 800})
    sign_in(page, live_server.url, PROFILE)
    inputs = [page.locator(f"#id_birth_{name}").bounding_box() for name in ("day", "month", "year")]
    button = page.locator("[data-birth-picker]").bounding_box()

    assert all(box and box["width"] > 40 for box in inputs), inputs
    row = [*inputs, button]
    assert max(box["y"] + box["height"] for box in row) - min(box["y"] for box in row) < 80
    for box in inputs:
        assert abs((box["y"] + box["height"] / 2) - (button["y"] + button["height"] / 2)) <= 2
    right = max(box["x"] + box["width"] for box in row)
    assert right <= width, "nothing runs off the screen"


def test_the_keyboard_moves_by_day_week_month_and_week_edge(page: Page, live_server, applicant):
    sign_in(page, live_server.url, PROFILE)
    page.locator("#id_birth_year").fill("1990")
    page.locator("#id_birth_month").fill("3")
    page.locator("#id_birth_day").fill("14")
    page.locator("[data-birth-picker]").click()
    panel = page.locator("#birth-picker")

    def focused_day() -> str:
        return page.evaluate("document.activeElement.getAttribute('data-date')")

    assert focused_day() == "1990-03-14"
    page.keyboard.press("ArrowRight")
    assert focused_day() == "1990-03-15"
    page.keyboard.press("ArrowDown")
    assert focused_day() == "1990-03-22"
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowUp")
    assert focused_day() == "1990-03-14"
    page.keyboard.press("PageDown")
    assert focused_day() == "1990-04-14"
    page.keyboard.press("PageUp")
    page.keyboard.press("PageUp")
    assert focused_day() == "1990-02-14"
    page.keyboard.press("Home")
    start = focused_day()
    page.keyboard.press("End")
    end = focused_day()
    assert start < "1990-02-14" <= end or start <= "1990-02-14" < end
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Enter")

    expect(panel).to_be_hidden()
    expect(page.locator("#id_birth_year")).to_have_value("1990")
    expect(page.locator("#id_birth_month")).to_have_value("2")


def test_escape_closes_the_calendar_and_returns_to_its_button(page: Page, live_server, applicant):
    panel = open_calendar(page, live_server, applicant)

    page.keyboard.press("Escape")

    expect(panel).to_be_hidden()
    expect(page.locator("[data-birth-picker]")).to_be_focused()


def test_today_is_named_and_every_day_is_a_target_of_at_least_24_pixels(
    page: Page, live_server, applicant
):
    panel = open_calendar(page, live_server, applicant)

    expect(panel.locator('button[aria-current="date"]')).to_have_count(1)
    for box in panel.locator("button[data-date]").evaluate_all(
        "(buttons) => buttons.map((b) => {"
        " const r = b.getBoundingClientRect(); return [r.width, r.height]; })"
    ):
        assert min(box) >= 24, box
    assert "Previous month" in panel.locator("[data-date-picker-previous]").get_attribute(
        "aria-label"
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_open_calendar_has_no_accessibility_violations(
    page: Page, live_server, applicant, scheme
):
    from tests.e2e.test_accessibility import AXE, describe, violations_on

    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    page.emulate_media(color_scheme=scheme)
    open_calendar(page, live_server, applicant)

    found = violations_on(page, AXE.read_text(encoding="utf-8"))

    assert not found, describe(f"the calendar ({scheme})", found)


def test_without_a_script_there_is_no_button_and_the_boxes_are_the_form(
    browser: Browser, live_server, applicant
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, PROFILE)
        expect(page.locator("[data-birth-picker]")).to_be_hidden()
        expect(page.locator("#id_birth_year")).to_be_visible()
    finally:
        context.close()
