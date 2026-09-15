"""The header stays at the top while the page scrolls, and nothing lands under it (#195).

The unit test beside the templates checks the classes and the one measured height; this
checks what the browser makes of them: the masthead at the top edge after a long scroll,
and an anchor that stops clear of it rather than behind it.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

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
    page.wait_for_timeout(100)
    assert top_of(page, "header") == 0, "the header scrolled away"
    header_height = page.locator("header").first.bounding_box()["height"]

    page.evaluate("window.scrollTo(0, 0)")
    page.locator("[data-section-link=section-language]").first.click()
    page.wait_for_timeout(300)
    assert top_of(page, "#section-language") >= header_height, "the section landed under the header"
