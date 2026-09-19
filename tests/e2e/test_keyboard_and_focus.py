"""Where focus goes, and whether it can be seen (#227).

The two halves of that issue a document cannot answer. axe reads markup: it cannot press
Enter on a sort link and say where focus landed afterwards, and it cannot ask what a
high-contrast theme paints. Both were broken and every page passed.

Everything else — the ids that make the focus restore possible, the preference behind the
single-key shortcuts, the board's menus, the headings — is in
`tests/test_keyboard_and_focus.py`, which needs no browser and fails on a laptop first.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


@pytest.fixture
def companies(applicant):
    """Enough rows for a second page, so *Next* is on the screen to be pressed."""
    from postulo.jobs.models import Company

    Company.objects.bulk_create(
        [Company(owner=applicant, name=f"Company {number:03d}") for number in range(60)]
    )


def focused_id(page: Page) -> str:
    return page.evaluate("() => document.activeElement && document.activeElement.id")


def wait_for_focus(page: Page, *wanted: str) -> None:
    """Wait until focus has landed on one of ``wanted``, and say so if it never does.

    A response arriving is not a swap finishing: htmx puts focus back while it settles,
    which is after the request this test waited for. Asserting straight away asks the
    question before the answer exists.
    """
    page.wait_for_function(
        "names => names.includes(document.activeElement && document.activeElement.id)",
        arg=list(wanted),
    )


def test_focus_stays_on_the_column_after_sorting(page: Page, live_server, companies):
    """htmx puts focus back only for an element with an `id`. Without one the next Tab
    started again at the skip link, so sorting a table by keyboard cost you your place."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    heading = page.locator('th[data-col="name"]')
    was = heading.get_attribute("aria-sort")
    link = page.locator("#sort-name")
    link.focus()
    with page.expect_response(lambda r: "sort=" in r.url):
        link.press("Enter")

    # That the order *changed*, not which way it went: the companies table already arrives
    # sorted by name, so one press makes it descending, and pinning a direction here would
    # be pinning today's default ordering to a test about focus.
    expect(heading).not_to_have_attribute("aria-sort", was or "")
    wait_for_focus(page, "sort-name")


def test_focus_stays_on_next_after_paging(page: Page, live_server, companies):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    button = page.locator("#page-next")
    button.focus()
    with page.expect_response(lambda r: "page=2" in r.url):
        button.press("Enter")

    # Sixty companies is two pages, so *Next* takes itself off the page by being pressed and
    # there is no id left for htmx to restore. Focus belongs in the pagination all the same,
    # and it is the script added for that which puts it on whichever of the pair survives.
    wait_for_focus(page, "page-prev", "page-next")


def test_focus_stays_on_the_theme_switch(page: Page, live_server, applicant):
    """It lives in the account menu, and losing focus there closes the menu around it."""
    sign_in(page, live_server.url)

    page.locator("details[data-menu] summary").last.click()
    button = page.locator("#theme-switch-button")
    with page.expect_response(lambda r: "/theme/" in r.url and r.request.method == "POST"):
        button.click()

    assert focused_id(page) == "theme-switch-button"


def test_a_field_shows_its_focus_in_forced_colours(page: Page, live_server, applicant):
    """`focus:outline-none` compiled to `outline-style: none` and beat the base rule, so the
    only cue left was a border colour and a ring — and a high-contrast theme throws both
    away. Every input, select, textarea and table filter had no visible focus at all."""
    sign_in(page, live_server.url)
    page.emulate_media(forced_colors="active")
    page.goto(f"{live_server.url}/jobs/companies/new/")

    field = page.locator("#id_name")
    field.focus()
    outline = page.evaluate(
        """() => {
            const style = getComputedStyle(document.activeElement);
            return {style: style.outlineStyle, width: style.outlineWidth};
        }"""
    )

    assert outline["style"] != "none", "there is an outline at all"
    assert outline["width"] not in ("", "0px"), "and it has a width to be seen by"


def test_a_button_shows_its_focus_in_forced_colours(page: Page, live_server, applicant):
    """Basecoat's `.btn` sets `outline-none` and shows focus with a ring and a border colour,
    both of which a high-contrast theme throws away. The style pack puts an outline back and
    says `solid` out loud, because `outline-none` also leaves Tailwind's outline-style
    variable at `none`, and `outline-2` alone reads it back and draws nothing (#262)."""
    sign_in(page, live_server.url)
    page.emulate_media(forced_colors="active")
    page.goto(f"{live_server.url}/jobs/companies/new/")

    page.locator("button.btn[type=submit]").first.focus()
    outline = page.evaluate(
        """() => {
            const style = getComputedStyle(document.activeElement);
            return {style: style.outlineStyle, width: style.outlineWidth};
        }"""
    )

    assert outline["style"] == "solid", outline
    assert outline["width"] not in ("", "0px"), "and it has a width to be seen by"


def test_a_wide_table_scrolls_from_the_keyboard(page: Page, live_server, companies):
    """Twenty scroll boxes, none focusable (#275): once a table overflowed, a plain-text cell
    off the edge could never be brought into view from the keyboard. The box is a named
    region in the tab order now, and the arrow keys scroll it."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 480, "height": 800})
    page.goto(f"{live_server.url}/jobs/companies/")

    box = page.get_by_role("region", name="Companies")
    box.focus()
    assert page.evaluate("() => document.activeElement.getAttribute('aria-label')") == "Companies"
    assert page.evaluate(
        "() => document.activeElement.scrollWidth > document.activeElement.clientWidth"
    )
    page.keyboard.press("ArrowRight")
    page.wait_for_function("() => document.activeElement.scrollLeft > 0")


def test_both_shapes_of_the_applications_page_are_in_the_tab_order(
    page: Page, live_server, applicant
):
    """The current shape was a disabled button: out of the tab order and drawn at half
    strength, reading as unavailable while announcing current (#275)."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/")

    buttons = page.locator("[data-shape-switch] button")
    expect(buttons).to_have_count(2)
    expect(buttons.first).to_be_enabled()
    expect(buttons.last).to_be_enabled()
    buttons.first.focus()
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.closest('[data-shape-switch]') !== null")
    assert (
        page.evaluate("() => document.activeElement.textContent.trim()")
        == buttons.last.text_content().strip()
    )


def test_the_failure_alert_closes_on_escape_and_on_its_button(page: Page, live_server, applicant):
    """A fixed box with no way off the screen sat over the foot of a long table (#275)."""
    sign_in(page, live_server.url)
    words = page.locator("[data-htmx-alert]")
    page.evaluate(
        "() => { document.querySelector('[data-htmx-alert]').textContent = 'It failed.' }"
    )
    expect(words).to_be_visible()

    page.keyboard.press("Escape")
    expect(words).to_have_text("")
    expect(words).to_be_hidden()

    page.evaluate("() => { document.querySelector('[data-htmx-alert]').textContent = 'Again.' }")
    page.get_by_role("button", name="Dismiss").click()
    expect(words).to_have_text("")


def test_a_single_key_does_nothing_once_it_is_switched_off(page: Page, live_server, applicant):
    """WCAG 2.1.4, level A. The switch is under Settings → Appearance."""
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/settings/appearance/")
    page.locator("#id_keyboard_shortcuts").uncheck()
    page.get_by_role("button", name="Save").click()
    page.goto(f"{live_server.url}/")

    page.locator("body").press("/")

    assert focused_id(page) != "site-search", "the key is off, so the box did not take focus"
