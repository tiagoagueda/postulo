"""At 2560 pixels, a table uses the screen and a form keeps its measure (#188).

The other end of reflow. Every page used to sit in a 1280-pixel column, so on a wide
monitor a table with ten chosen columns scrolled inside a box with grey on both sides --
two nested scrolls to read one row for anybody using a magnifier. The unit test next to the
templates checks which pages empty the cap; this checks what the browser does with it.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e

#: A monitor rather than a laptop: 1440p, the first width at which the old cap showed.
WIDE = 2560


def width_of(page: Page, selector: str) -> float:
    box = page.locator(selector).first.bounding_box()
    assert box, f"{selector} is not drawn"
    return box["width"]


def test_a_table_takes_the_screen_and_a_form_does_not(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": WIDE, "height": 1200})
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/applications/")
    assert width_of(page, "main") > 2400, "the applications table is still in a 1280px column"
    assert width_of(page, "main .scroll-x") > 2000, "the table's scroll box did not follow"
    assert width_of(page, "header") == WIDE, "the masthead sits narrower than the table"

    page.goto(f"{live_server.url}/applications/new/")
    assert width_of(page, "main") == 1280, "a form is a wide paragraph now"


def test_a_laptop_sees_no_difference(live_server, page: Page, furnished):  # noqa: F811
    """Below the old cap nothing changes: the cap was never reached there."""
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url)

    for path in ("/applications/", "/applications/new/"):
        page.goto(f"{live_server.url}{path}")
        assert width_of(page, "main") == 1280, path
