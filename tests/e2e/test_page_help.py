"""The question mark beside a page's heading (#839): the sentence on hover, the help in a drawer.

Listings is the first page with one. These are the parts that need a browser: that the sentence
is a tooltip where a script runs and a line under the heading where one does not, that the
drawer opens and closes with the keyboard, and that the heading and the mark share a line at a
phone's width without anything scrolling sideways.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

SENTENCE = "Every posting you have noticed, until you decide"


def open_listings(page: Page, live_server) -> None:
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/listings/")


def test_the_sentence_is_a_tooltip_and_the_help_opens_in_a_drawer(
    page: Page, live_server, applicant
):
    open_listings(page, live_server)
    mark = page.locator(".page-title-help [data-help-mark]")
    summary = page.locator(".page-title-help [data-help-summary]")
    drawer = page.locator("#help-listings > div")

    expect(summary).to_be_hidden()
    mark.hover()
    expect(summary).to_be_visible()
    expect(summary).to_contain_text(SENTENCE)

    mark.click()
    expect(drawer).to_be_visible()
    expect(drawer).to_contain_text("Capture a posting")
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()


def test_the_heading_and_the_mark_share_a_line_at_320_pixels(page: Page, live_server, applicant):
    page.set_viewport_size({"width": 320, "height": 640})
    open_listings(page, live_server)

    heading = page.locator(".page-title-help h1").bounding_box()
    mark = page.locator(".page-title-help [data-help-mark]").bounding_box()

    assert abs((heading["y"] + heading["height"] / 2) - (mark["y"] + mark["height"] / 2)) < 12
    assert mark["x"] >= heading["x"] + heading["width"] - 1, "beside it, not over it"
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_with_scripts_off_the_sentence_stands_under_the_heading_and_the_mark_is_a_link(
    browser: Browser, live_server, applicant
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        open_listings(page, live_server)
        summary = page.locator(".page-title-help [data-help-summary]")
        heading = page.locator(".page-title-help h1")

        expect(summary).to_be_visible()
        assert (
            summary.bounding_box()["y"]
            >= heading.bounding_box()["y"] + heading.bounding_box()["height"] - 1
        )
        mark = page.locator(".page-title-help [data-help-mark]")
        assert mark.get_attribute("href") == "/help/listings/"
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_heading_with_its_mark_passes_axe(
    page: Page,
    live_server,
    applicant,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    open_listings(page, live_server)
    page.locator(".page-title-help [data-help-mark]").hover()

    found = violations_on(page, axe_source)

    assert not found, describe(f"Listings with its question mark ({scheme})", found)
