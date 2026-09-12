"""The career page's sidebar, in a browser: it navigates, and it says where you are (#175).

The markup is checked next to the templates; what only a browser can show is that an
anchor takes you to its section, and that the entry for the section on the screen is the
one marked -- `aria-current="location"`, set by app.js as the page is read.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e


def test_an_entry_takes_you_to_its_section_and_is_marked_there(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": 1280, "height": 700})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/career/")

    nav = page.locator('nav[aria-label="Career sections"]')
    expect(nav.locator("a")).to_have_count(7)

    nav.locator('a[href="#section-language"]').click()

    section = page.locator("#section-language")
    box = section.bounding_box()
    assert box and 0 <= box["y"] < 700, "the section was not brought onto the screen"
    expect(nav.locator('a[href="#section-language"]')).to_have_attribute("aria-current", "location")
    expect(nav.locator('a[href="#section-experience"]')).not_to_have_attribute(
        "aria-current", "location"
    )


def test_on_a_phone_the_list_is_a_strip_above_the_sections(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": 390, "height": 844})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/career/")

    nav = page.locator('nav[aria-label="Career sections"]').bounding_box()
    first = page.locator("#section-experience").bounding_box()
    assert nav and first
    assert nav["y"] + nav["height"] <= first["y"] + 1, "the list sits above the sections"
    assert nav["width"] <= 390, "the strip scrolls inside itself rather than widening the page"
