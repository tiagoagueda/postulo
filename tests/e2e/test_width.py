"""At 2560 pixels, a table uses the screen and a form keeps its measure (#188).

The other end of reflow. Every page used to sit in a 1280-pixel column, so on a wide
monitor a table with ten chosen columns scrolled inside a box with grey on both sides --
two nested scrolls to read one row for anybody using a magnifier. The unit test next to the
templates checks which pages empty the cap; this checks what the browser does with it.

The company form takes the screen too, as two columns from `2xl`, with the measure kept on
its cards rather than on the page (#210): the tests at the end say where the line falls
and that it mirrors right to left.
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


#: Today's single column, `max-w-2xl`: neither column may be narrower than this (#210).
MEASURE = 672

#: The first breakpoint at which two columns of that width fit beside each other: `2xl`.
#: 672 + 24 + 672 inside two 16-pixel gutters needs a window of 1400; at `xl`, 1280, each
#: column would be 612.
TWO_COLUMNS = 1536


def company_form_parts(page: Page) -> tuple[dict, dict, dict]:
    details = page.locator("main form .card").first.bounding_box()
    identifiers = page.locator("main form [data-identifiers]").bounding_box()
    # By its type rather than its name: the right-to-left test reads the page in Arabic.
    save = page.locator('main form button[type="submit"]').bounding_box()
    assert details and identifiers and save
    return details, identifiers, save


def test_the_company_form_is_two_columns_on_a_wide_screen(live_server, page: Page, furnished):  # noqa: F811
    """The details beside the identifiers, each at today's measure, *Save* after both."""
    sign_in(page, live_server.url)
    company = furnished["company"]
    for width in (TWO_COLUMNS, WIDE):
        page.set_viewport_size({"width": width, "height": 1200})
        for path in ("/jobs/companies/new/", f"/jobs/companies/{company.pk}/edit/"):
            page.goto(f"{live_server.url}{path}")
            where = f"{path} at {width}"
            assert width_of(page, "main") == width, f"{where}: the page is capped"
            details, identifiers, save = company_form_parts(page)
            assert details["y"] == identifiers["y"], f"{where}: not side by side"
            assert identifiers["x"] > details["x"] + details["width"], f"{where}: order"
            assert details["width"] >= MEASURE, f"{where}: narrower than one column was"
            assert identifiers["width"] >= MEASURE, f"{where}: narrower than one column was"
            # The measure stays on the parts: a text box or the notes never stretch to the
            # width of the monitor, however wide it is.
            for box in ("#id_name", "#id_notes"):
                assert width_of(page, box) < MEASURE, f"{where}: {box} lost its measure"
            bottom = max(details["y"] + details["height"], identifiers["y"] + identifiers["height"])
            assert save["y"] >= bottom, f"{where}: Save is not after both columns"


def test_below_the_breakpoint_the_company_form_is_one_column(live_server, page: Page, furnished):  # noqa: F811
    """One pixel short of `2xl`, and a laptop: the identifiers under the details, as always."""
    sign_in(page, live_server.url)
    for width in (TWO_COLUMNS - 1, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}/jobs/companies/new/")
        details, identifiers, save = company_form_parts(page)
        assert identifiers["y"] >= details["y"] + details["height"], f"one column at {width}"
        assert details["x"] == identifiers["x"], f"one column at {width}"
        assert details["width"] == identifiers["width"] == MEASURE, f"the measure at {width}"
        assert save["y"] >= identifiers["y"] + identifiers["height"]


def test_the_two_columns_mirror_right_to_left(live_server, page: Page, furnished):  # noqa: F811
    """In Arabic the details sit at the right-hand edge, where reading starts, and the
    identifiers to their left: a flex row follows the direction of the text."""
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": WIDE, "height": 1200})
    page.goto(f"{live_server.url}/jobs/companies/new/")

    assert page.locator("html").get_attribute("dir") == "rtl"
    details, identifiers, _save = company_form_parts(page)
    assert details["y"] == identifiers["y"]
    assert details["x"] > identifiers["x"] + identifiers["width"], "the details lead"
    assert round(details["x"] + details["width"]) == WIDE - 16, "from the reading edge"
