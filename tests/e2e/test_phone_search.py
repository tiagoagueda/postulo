"""On a phone the search icon opens the field in the masthead, where the person is (#350).

Over everything, below `lg`, the icon is a link to the search page and stays one with
scripts off. With them a tap opens the box in the place of the wordmark and the instance's
name -- no new page, the focus already in the field -- and Escape or the close button put
the masthead back and the focus on the icon. Typing and submitting reaches the results page.
The masthead stays one row high, and axe passes with the field open in both themes.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

PHONE = {"width": 320, "height": 640}


def open_on_phone(page: Page, base: str):
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    page.goto(f"{base}/")
    icon = page.locator("header a[data-search-link]")
    expect(icon).to_have_attribute("aria-expanded", "false")
    return icon


def test_tapping_the_icon_opens_a_focused_field_without_leaving_the_page(
    page: Page, live_server, applicant
):
    base = live_server.url
    icon = open_on_phone(page, base)
    box = page.locator("#site-search")
    expect(box).to_be_hidden()
    expect(page.locator("header [data-wordmark]")).to_be_visible()

    icon.click()
    expect(page).to_have_url(f"{base}/")
    expect(box).to_be_visible()
    expect(box).to_be_focused()
    expect(icon).to_have_attribute("aria-expanded", "true")
    expect(page.locator("header [data-wordmark]")).to_be_hidden()
    assert box.get_attribute("type") == "search"
    assert box.get_attribute("enterkeyhint") == "search"
    # 16 pixels at least, or iOS zooms the page when the field takes the focus.
    assert box.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 16
    assert box.bounding_box()["height"] >= 44
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
    header = page.locator("header[data-site-header]").bounding_box()
    assert header["height"] < 80, header

    box.fill("aperture")
    box.press("Enter")
    expect(page).to_have_url(re.compile(r"/search/\?q=aperture"))


def test_escape_and_the_close_button_put_the_masthead_back(page: Page, live_server, applicant):
    base = live_server.url
    icon = open_on_phone(page, base)
    box = page.locator("#site-search")

    icon.click()
    expect(box).to_be_focused()
    page.keyboard.press("Escape")
    expect(box).to_be_hidden()
    expect(page.locator("header [data-wordmark]")).to_be_visible()
    expect(icon).to_be_focused()
    expect(icon).to_have_attribute("aria-expanded", "false")

    icon.click()
    close = page.get_by_role("button", name="Close search")
    expect(close).to_be_visible()
    assert close.bounding_box()["width"] >= 44
    close.click()
    expect(box).to_be_hidden()
    expect(page.locator("header [data-wordmark]")).to_be_visible()
    expect(icon).to_be_focused()


def test_the_slash_key_and_the_icon_open_the_same_field(page: Page, live_server, applicant):
    base = live_server.url
    icon = open_on_phone(page, base)
    box = page.locator("#site-search")

    page.keyboard.press("/")
    expect(box).to_be_focused()
    expect(page).to_have_url(f"{base}/")
    page.keyboard.press("Escape")
    expect(box).to_be_hidden()

    expect(page.locator("[data-nav-search]")).to_have_count(0)
    icon.click()
    expect(page).to_have_url(f"{base}/")
    expect(box).to_be_focused()
    expect(icon).to_have_attribute("aria-expanded", "true")


def test_with_scripts_off_the_icon_opens_the_search_page(browser: Browser, live_server, applicant):
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/")
        page.locator("header a[data-search-link]").click()
        expect(page).to_have_url(f"{base}/search/")
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_the_open_field_passes_axe(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
    direction,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    icon = open_on_phone(page, base)
    if direction == "rtl":
        page.evaluate("document.documentElement.dir = 'rtl'")
    icon.click()
    expect(page.locator("#site-search")).to_be_visible()
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
    found = violations_on(page, axe_source)
    assert not found, describe(f"the open field ({scheme}, {direction})", found)
