"""A calendar beside the date of birth's boxes, which stay the form (#863)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

PROFILE = "/accounts/profile/"


def test_with_a_script_the_calendar_fills_the_three_boxes(page: Page, live_server, applicant):
    sign_in(page, live_server.url, PROFILE)
    button = page.locator("[data-birth-picker]")
    expect(button).to_be_visible()

    button.click()
    picker = page.locator("#birth-picker-input")
    expect(picker).to_be_visible()
    picker.fill("1990-03-07")

    expect(page.get_by_label("Year", exact=True)).to_have_value("1990")
    expect(page.get_by_label("Month", exact=True)).to_have_value("3")
    expect(page.get_by_label("Day", exact=True)).to_have_value("7")
    expect(page.locator("#birth-picker")).to_be_hidden()


def test_the_boxes_still_take_a_year_alone_and_start_the_picker_from_a_whole_date(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url, PROFILE)
    page.get_by_label("Year", exact=True).fill("1985")
    page.get_by_label("Month", exact=True).fill("11")
    page.get_by_label("Day", exact=True).fill("2")

    page.locator("[data-birth-picker]").click()

    expect(page.locator("#birth-picker-input")).to_have_value("1985-11-02")


def test_without_a_script_there_is_no_button_and_the_boxes_are_the_form(
    browser: Browser, live_server, applicant
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, PROFILE)
        expect(page.locator("[data-birth-picker]")).to_be_hidden()
        expect(page.get_by_label("Year", exact=True)).to_be_visible()
    finally:
        context.close()
