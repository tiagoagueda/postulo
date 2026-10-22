"""The date pickers in a browser (#933): every mode by keyboard and pointer, the boxes they
fill, a phone's sheet, right to left, the server's first weekday and week numbers, axe in
both themes, and each box still the form with scripts off.

The calendar's own pickers, one per view, are in `test_calendar_views.py`; the date of
birth's in `test_birth_picker.py`.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_application_filters import TABLE, WIDE, asked, open_header, search, shows

pytestmark = pytest.mark.e2e

__all__ = ["search"]  # the fixture, imported for the table filter below

NEW = "/applications/new/"
REPORT = "/applications/report/"


def opener_of(page: Page, box: str):
    """The calendar button beside a box, found through the panel that names the box."""
    panel = page.locator(f'[data-date-picker][data-for="{box}"]')
    return page.locator(f'[data-date-opener][popovertarget="{panel.get_attribute("id")}"]'), panel


def iso(day: dt.date) -> str:
    return f"{day:%Y-%m-%d}"


# ------------------------------------------------------------------ a date in a form


def test_a_date_box_is_filled_from_its_calendar(page: Page, live_server, applicant):
    page.set_viewport_size(WIDE)
    sign_in(page, live_server.url, NEW)
    button, panel = opener_of(page, "id_closes_at")

    expect(button).to_be_visible()
    page.locator("#id_closes_at").fill("2031-05-04")
    button.click()
    expect(panel).to_be_visible()
    expect(panel).to_have_attribute("aria-label", "Closing date")
    chosen = panel.locator('button[data-date="2031-05-04"]')
    expect(chosen).to_have_attribute("aria-pressed", "true")
    expect(chosen).to_be_focused()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")

    expect(panel).to_be_hidden()
    expect(page.locator("#id_closes_at")).to_have_value("2031-05-05")
    expect(button).to_be_focused()


def test_a_date_and_a_time_keep_the_time_when_the_day_is_chosen(page: Page, live_server, applicant):
    from postulo.applications.models import Reminder

    sign_in(page, live_server.url, "/applications/reminders/new/?on=2030-03-13")
    expect(page.locator("#id_due_at")).to_have_value("2030-03-13")
    expect(page.locator("#id_due_at_time")).to_have_value("09:00")
    button, panel = opener_of(page, "id_due_at")
    button.click()
    panel.locator('button[data-date="2030-03-20"]').click()

    expect(page.locator("#id_due_at")).to_have_value("2030-03-20")
    expect(page.locator("#id_due_at_time")).to_have_value("09:00")
    page.locator("#id_summary").fill("Book the train")
    page.get_by_role("button", name="Save").click()
    expect(page).not_to_have_url(re.compile(r"/reminders/new/"))
    due = Reminder.objects.get(summary="Book the train").due_at
    from django.utils import timezone

    assert timezone.localtime(due).replace(tzinfo=None) == dt.datetime(2030, 3, 20, 9, 0)


def test_escape_closes_and_gives_the_focus_back(page: Page, live_server, applicant):
    sign_in(page, live_server.url, NEW)
    button, panel = opener_of(page, "id_closes_at")
    button.click()
    expect(panel).to_be_visible()

    page.keyboard.press("Escape")

    expect(panel).to_be_hidden()
    expect(button).to_be_focused()
    expect(page.locator("#id_closes_at")).to_have_value("")


# ------------------------------------------------------------- the report's pickers


def test_the_month_picker_moves_by_month_row_and_year(page: Page, live_server, applicant):
    sign_in(page, live_server.url, f"{REPORT}?period=month&on=2026-05")
    button, panel = opener_of(page, "report-month")
    button.click()

    def focused() -> str:
        return page.evaluate("document.activeElement.getAttribute('data-month')")

    assert focused() == "2026-05"
    page.keyboard.press("ArrowRight")
    assert focused() == "2026-06"
    page.keyboard.press("ArrowDown")
    assert focused() == "2026-09"
    page.keyboard.press("Home")
    assert focused() == "2026-07"
    page.keyboard.press("End")
    assert focused() == "2026-09"
    page.keyboard.press("PageDown")
    assert focused() == "2027-09"
    expect(panel.locator("[data-date-picker-year]")).to_have_value("2027")
    page.keyboard.press("PageUp")
    page.keyboard.press("PageUp")
    page.keyboard.press("Enter")

    expect(panel).to_be_hidden()
    expect(page.locator("#report-month")).to_have_value("2025-09")


def test_the_range_takes_two_presses_in_either_order(page: Page, live_server, applicant):
    sign_in(page, live_server.url, f"{REPORT}?period=custom&from=2026-03-02&to=2026-03-05")
    button, panel = opener_of(page, "report-from")
    button.click()

    expect(panel.locator('button[data-date="2026-03-02"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(panel.locator('button[data-date="2026-03-05"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(panel.locator("button[data-in-range]")).to_have_count(2)
    panel.locator('button[data-date="2026-03-20"]').click()
    expect(panel.locator("[data-date-picker-status]")).to_contain_text("now choose the last day")
    panel.locator('button[data-date="2026-03-11"]').click()

    expect(panel).to_be_hidden()
    expect(page.locator("#report-from")).to_have_value("2026-03-11")
    expect(page.locator("#report-to")).to_have_value("2026-03-20")


def test_escape_after_one_press_changes_nothing(page: Page, live_server, applicant):
    sign_in(page, live_server.url, f"{REPORT}?period=custom&from=2026-03-02&to=2026-03-05")
    button, panel = opener_of(page, "report-from")
    button.click()
    panel.locator('button[data-date="2026-03-20"]').click()

    page.keyboard.press("Escape")

    expect(panel).to_be_hidden()
    expect(button).to_be_focused()
    expect(page.locator("#report-from")).to_have_value("2026-03-02")
    expect(page.locator("#report-to")).to_have_value("2026-03-05")


# ------------------------------------------------------------------- a table's filter


def test_a_date_filter_narrows_the_table_from_its_range(page: Page, live_server, search):
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    shows(page, asked(person))
    open_header(page, "applied")

    today = dt.date.today()
    start = today - dt.timedelta(days=15)
    button, panel = opener_of(page, "filter-applied-from")
    button.click()
    expect(panel).to_be_visible()
    if start.month != today.month:
        panel.locator("[data-date-picker-previous]").click()
    panel.locator(f'button[data-date="{iso(start)}"]').click()
    if start.month != today.month:
        panel.locator("[data-date-picker-next]").click()
    panel.locator(f'button[data-date="{iso(today)}"]').click()

    expect(page).to_have_url(re.compile(rf"applied_from={iso(start)}"))
    expect(page).to_have_url(re.compile(rf"applied_to={iso(today)}"))
    expect(page.locator(f"{TABLE} tbody tr")).to_have_count(2)


# ------------------------------------------------------------- the week, the locale


def test_the_week_follows_the_first_weekday_the_server_says(page: Page, live_server, applicant):
    profile = applicant.profile
    profile.first_weekday = 0
    profile.save(update_fields=["first_weekday"])
    page.set_viewport_size(WIDE)
    sign_in(page, live_server.url, "/applications/calendar/?view=week&on=2026-09-16")
    expect(page.locator("html")).to_have_attribute("data-first-weekday", "0")
    page.locator("[data-period] h2 button").click()
    panel = page.locator("#calendar-period")

    expect(panel.locator("thead abbr").first).to_have_attribute("title", "Sunday")
    selected = panel.locator('td[aria-selected="true"] button')
    expect(selected.first).to_have_attribute("data-date", "2026-09-13")
    page.keyboard.press("ArrowDown")
    expect(panel.locator("tr[data-preview] button").first).to_have_attribute(
        "data-date", "2026-09-20"
    )
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"view=week&on=2026-09-20"))


def test_weeks_are_numbered_where_the_language_numbers_them(page: Page, live_server, applicant):
    profile = applicant.profile
    profile.language = "de"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url, "/applications/calendar/?view=day&on=2026-09-16")
    expect(page.locator("html")).to_have_attribute("data-week-numbers", "")
    page.locator("[data-period] h2 button").click()
    panel = page.locator("#calendar-period")

    numbers = panel.locator('tbody th[data-week-number][scope="row"]')
    expect(numbers.first).to_have_text("36")  # 31 Aug - 6 Sep 2026
    expect(numbers.nth(2)).to_have_text("38")


def test_right_to_left_the_arrows_are_mirrored(page: Page, live_server, applicant):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url, NEW)
    page.locator("#id_closes_at").fill("2031-05-04")
    button, panel = opener_of(page, "id_closes_at")
    button.click()
    expect(panel.locator('button[data-date="2031-05-04"]')).to_be_focused()

    page.keyboard.press("ArrowLeft")

    expect(panel.locator('button[data-date="2031-05-05"]')).to_be_focused()


# ------------------------------------------------------------------------ a phone


def test_on_a_phone_the_calendar_is_a_sheet_with_44_pixel_days(page: Page, live_server, applicant):
    page.set_viewport_size({"width": 360, "height": 740})
    sign_in(page, live_server.url, NEW)
    button, panel = opener_of(page, "id_closes_at")
    button.click()
    expect(panel).to_be_visible()

    box = panel.bounding_box()
    assert abs(box["x"]) <= 1 and abs(box["width"] - 360) <= 1, box
    assert abs(box["y"] + box["height"] - 740) <= 1, "along the foot of the window"
    for size in panel.locator("button[data-date]").evaluate_all(
        "(buttons) => buttons.map((b) => {"
        " const r = b.getBoundingClientRect(); return [r.width, r.height]; })"
    ):
        assert min(size) >= 44, size


# ---------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("box", ["report-month", "report-from"])
def test_the_open_pickers_have_no_accessibility_violations(
    page: Page, live_server, applicant, scheme, box
):
    from tests.e2e.test_accessibility import AXE, describe, violations_on

    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, f"{REPORT}?period=custom&from=2026-03-02&to=2026-03-05")
    button, panel = opener_of(page, box)
    button.click()
    expect(panel).to_be_visible()

    found = violations_on(page, AXE.read_text(encoding="utf-8"))

    assert not found, describe(f"the {box} picker ({scheme})", found)


# -------------------------------------------------------------------- scripts off


def test_without_a_script_the_boxes_are_the_form(browser: Browser, live_server, applicant):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, NEW)
        expect(page.locator("[data-date-opener]").first).to_be_hidden()
        expect(page.locator("#id_closes_at")).to_be_visible()

        sign_in(page, live_server.url, REPORT)
        expect(page.locator("[data-date-opener]").first).to_be_hidden()
        page.locator("#report-period").select_option("custom")
        page.locator("#report-from").fill("2026-03-02")
        page.locator("#report-to").fill("2026-03-20")
        page.get_by_role("button", name="Show this period").click()
        expect(page).to_have_url(re.compile(r"from=2026-03-02&to=2026-03-20"))
        expect(page.locator("#report-to")).to_have_value("2026-03-20")
    finally:
        context.close()


def test_without_a_script_a_date_filter_posts_its_two_boxes(browser: Browser, live_server, search):
    context = browser.new_context(java_script_enabled=False, viewport={"width": 360, "height": 800})
    page = context.new_page()
    try:
        sign_in(page, live_server.url, "/applications/")
        narrow = page.locator("[data-narrow-form]")
        narrow.locator("summary").click()
        expect(
            page.locator('[data-date-opener][popovertarget="filter-applied-narrow-calendar"]')
        ).to_be_hidden()
        page.locator("#filter-applied-narrow-from").fill("2020-01-01")
        page.locator("#filter-applied-narrow-to").fill("2030-01-01")
        narrow.locator('button[type="submit"]').click()
        expect(page).to_have_url(re.compile(r"applied_from=2020-01-01"))
        expect(page).to_have_url(re.compile(r"applied_to=2030-01-01"))
    finally:
        context.close()
