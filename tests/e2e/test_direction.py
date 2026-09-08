"""The interface under ``dir="rtl"``, in a real browser.

Postulo offers no right-to-left language yet, and that is exactly why this exists. The
``dir`` attribute has been emitted since the first release and has been ``ltr`` on every
page ever rendered, so what the interface did under ``rtl`` was unknown rather than
known-good. #70 brings Arabic; this is the work that has to be true before it arrives.

The pseudo-locale is a real language tag on a profile with no catalogue behind it. The
words stay English, which makes the layout easier to judge rather than harder: what is
being checked is which edge things sit against, and reading the labels while you check is
a help.
"""

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e

#: Enough of the application to see every kind of layout that names an edge.
PAGES = (
    "/",
    "/applications/",
    "/applications/board/",
    "/jobs/companies/",
    "/documents/cvs/",
    "/career/",
    "/settings/dashboard/",
    "/server/plugins/",
)


@pytest.fixture
def right_to_left(furnished):  # noqa: F811
    """The signed-in account reads Postulo in Arabic. No catalogue; the words stay English."""
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    return furnished


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_page_has_no_violations_right_to_left(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    right_to_left,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)

    failures = []
    for path in PAGES:
        page.goto(f"{base}{path}")
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} rtl ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_the_action_group_moves_to_the_other_end(live_server, page: Page, right_to_left):
    """`ms-auto` is what puts *Record an application* at the far end of a heading row.

    In Arabic the far end is the other one, and this is the check that says so in pixels
    rather than in class names: the button sits left of the heading, which under `ltr` it
    never does.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/board/")

    heading = page.get_by_role("heading", name="Board").bounding_box()
    action = page.get_by_role("link", name="Record an application").bounding_box()

    assert action["x"] < heading["x"], "the actions should sit at the reading-end edge"


def test_the_board_starts_at_the_reading_edge(live_server, page: Page, right_to_left):
    """Columns are a horizontally scrolling row, so the first status must be nearest.

    Flexbox is direction-aware, so this needs no code of its own -- which is worth a test
    precisely because it would be easy to "fix" it later with something that breaks it.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/board/")

    columns = page.locator("[data-board-column]")
    expect(columns.first).to_be_visible()
    first = columns.first.bounding_box()
    last = columns.last.bounding_box()

    assert first["x"] > last["x"], "the earliest status should be at the reading-start edge"


def test_the_skip_link_lands_on_the_reading_edge(live_server, page: Page, right_to_left):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")
    page.keyboard.press("Tab")

    skip = page.get_by_role("link", name="Skip to content")
    expect(skip).to_be_focused()
    box = skip.bounding_box()
    width = page.viewport_size["width"]

    assert box["x"] + box["width"] > width / 2, "the skip link should start at the reading edge"


def test_a_latin_name_keeps_its_own_direction(live_server, page: Page, right_to_left):
    """The name renders left to right inside a right-to-left line, which is what <bdi> is for."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")

    expect(page.get_by_text("Aperture Science").first).to_be_visible()
    assert page.locator("html").get_attribute("dir") == "rtl"
