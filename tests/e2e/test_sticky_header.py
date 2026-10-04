"""The header stays at the top while the page scrolls, and nothing lands under it (#195).

The unit test beside the templates checks the classes and the one measured height; this
checks what the browser makes of them: the masthead at the top edge after a long scroll,
and an anchor that stops clear of it rather than behind it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import furnished, sign_in  # noqa: F401

# `furnished` is a fixture, used by name below.

pytestmark = pytest.mark.e2e


def top_of(page: Page, selector: str) -> float:
    box = page.locator(selector).first.bounding_box()
    assert box, f"{selector} is not drawn"
    return box["y"]


def test_the_header_is_there_after_scrolling_and_an_anchor_clears_it(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    page.set_viewport_size({"width": 1280, "height": 700})
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/career/")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    # The page has seen the scroll: its handler marks the masthead once scrollY > 0.
    expect(page.locator("[data-site-header]")).to_have_attribute("data-scrolled", "")
    assert top_of(page, "header") == 0, "the header scrolled away"
    header_height = page.locator("header").first.bounding_box()["height"]

    page.evaluate("window.scrollTo(0, 0)")
    page.locator("[data-section-link=section-language]").first.click()
    # The jump to the anchor is done once the address carries its fragment.
    expect(page).to_have_url(re.compile(r"#section-language$"))
    assert top_of(page, "#section-language") >= header_height, "the section landed under the header"


def test_the_server_settings_list_stays_in_view_and_fits_the_window(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """Scrolled to the foot of a long page the sections are still there to go to (#699)."""
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/design/")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    # The page has seen the scroll: its handler marks the masthead once scrollY > 0.
    expect(page.locator("[data-site-header]")).to_have_attribute("data-scrolled", "")

    header_height = page.locator("header").first.bounding_box()["height"]
    aside = page.locator("aside[data-sidebar-frame]")
    assert aside.bounding_box()["y"] >= header_height - 1, "under the masthead, not behind it"
    assert aside.bounding_box()["y"] < 200, "and still in view after the scroll"
    aside.get_by_role("link", name="Overview").click()
    page.wait_for_url(f"{live_server.url}/server/overview/")

    page.set_viewport_size({"width": 1024, "height": 480})
    page.goto(f"{live_server.url}/server/design/")
    box = aside.bounding_box()
    header_height = page.locator("header").first.bounding_box()["height"]
    assert box["height"] <= 480 - header_height, "never taller than the space under the masthead"
    # The last section is reached by Tab: focus scrolls the capped list, not the page.
    links = aside.get_by_role("link")
    links.first.focus()
    for _ in range(links.count() - 1):
        page.keyboard.press("Tab")
    expect(links.last).to_be_focused()
    expect(links.last).to_be_in_viewport()
