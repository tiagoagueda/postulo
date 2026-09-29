"""What a capture kept of its page, in a browser that would run it if it were handed it (#256).

`tests/security/test_captured_pages.py` reads the answers: the media types, the headers,
the escaping. This is the other half of the same promise, asked of the only thing that can
answer it -- a browser, with a kept source that changes the page's title the moment
anything runs it. The content security policy is being enforced throughout, and the
autouse fixture in `conftest.py` fails the test on anything it refuses, so drawing the
picture from this instance is checked against the policy as well as against the eye.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from .test_accessibility import (  # noqa: F401 - `furnished` and `axe_source` are fixtures
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e

#: What the kept source in `furnished` would do to this page if anything ran it.
RAN = "never run"


def address_of(live_server, furnished) -> str:  # noqa: F811
    return f"{live_server.url}/jobs/captures/{furnished['capture'].pk}/page/"


def test_the_source_is_read_and_never_run(page: Page, live_server, furnished):  # noqa: F811
    sign_in(page, live_server.url)
    page.goto(address_of(live_server, furnished))

    source = page.locator("pre[data-source]")
    expect(source).to_be_hidden()
    page.get_by_text("Show it as text").click()
    expect(source).to_be_visible()

    expect(source).to_contain_text("<h1>Research Engineer</h1>")
    expect(source).to_contain_text("<script>document.title = 'never run'</script>")
    assert page.title() != RAN
    assert source.locator("*").count() == 0, "text, with no element of the page's inside it"
    assert page.locator("main iframe, main object, main embed").count() == 0


def test_the_picture_is_drawn_from_this_instance(page: Page, live_server, furnished):  # noqa: F811
    sign_in(page, live_server.url)
    page.goto(address_of(live_server, furnished))

    picture = page.locator("img[data-rendering]")
    expect(picture).to_be_visible()
    assert picture.evaluate("image => image.complete && image.naturalWidth > 0")
    assert picture.get_attribute("src").startswith("/jobs/captures/")
    assert picture.get_attribute("alt"), "and it has a name"


def test_the_source_leaves_as_a_file_of_text(page: Page, live_server, furnished):  # noqa: F811
    """Asked for in a browser, it is saved and not shown: there is no page to look at."""
    sign_in(page, live_server.url)
    page.goto(address_of(live_server, furnished))

    with page.expect_download() as taken:
        page.get_by_role("link", name="Download it as text").click()

    download = taken.value
    assert download.suggested_filename.endswith("-source.txt")
    with open(download.path(), encoding="utf-8") as saved:
        text = saved.read()
    assert "<script>document.title = 'never run'</script>" in text
    assert page.title() != RAN
    assert "/page/source/" not in page.url, "the browser never went there"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_has_no_violations_with_the_source_open(
    page: Page,
    live_server,
    furnished,  # noqa: F811
    axe_source,  # noqa: F811
    scheme,
):
    """The walk reads this page as it arrives, with the source closed. This is the state
    somebody checking a reading is actually in."""
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(address_of(live_server, furnished))
    page.get_by_text("Show it as text").click()
    expect(page.locator("pre[data-source]")).to_be_visible()

    found = violations_on(page, axe_source)

    assert not found, describe(f"the kept page, source open ({scheme})", found)


def test_both_boxes_that_scroll_can_be_reached_from_the_keyboard(
    page: Page,
    live_server,
    furnished,  # noqa: F811
):
    sign_in(page, live_server.url)
    page.goto(address_of(live_server, furnished))
    page.get_by_text("Show it as text").click()

    for box in ("The rendering, in a box that scrolls", "The source, as text"):
        region = page.get_by_role("group", name=box)
        region.focus()
        expect(region).to_be_focused()


def test_it_works_with_no_script_at_all(browser: Browser, live_server, furnished):  # noqa: F811
    """Everything here is a link, a form or a disclosure the browser draws itself."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(address_of(live_server, furnished))

        expect(page.locator("img[data-rendering]")).to_be_visible()
        page.get_by_text("Show it as text").click()
        expect(page.locator("pre[data-source]")).to_contain_text("<h1>Research Engineer</h1>")

        page.get_by_role("link", name="Delete the rendering").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Are you sure?")
        page.get_by_role("button", name="Delete").click()

        expect(page.get_by_text("No rendering was kept.")).to_be_visible()
        expect(page.locator("img[data-rendering]")).to_have_count(0)
        # One half went; the source is still kept.
        expect(page.locator("pre[data-source]")).to_have_count(1)
    finally:
        context.close()
