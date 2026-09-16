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


def test_focus_stays_on_the_column_after_sorting(page: Page, live_server, companies):
    """htmx puts focus back only for an element with an `id`. Without one the next Tab
    started again at the skip link, so sorting a table by keyboard cost you your place."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    link = page.locator("#sort-name")
    link.focus()
    with page.expect_response(lambda r: "sort=" in r.url):
        link.press("Enter")

    expect(page.locator('th[data-col="name"]')).to_have_attribute("aria-sort", "ascending")
    assert focused_id(page) == "sort-name"


def test_focus_stays_on_next_after_paging(page: Page, live_server, companies):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    button = page.locator("#page-next")
    button.focus()
    with page.expect_response(lambda r: "page=2" in r.url):
        button.press("Enter")

    assert focused_id(page) == "page-prev" or focused_id(page) == "page-next", (
        "whichever of the pair the new page draws, focus is still in the pagination"
    )


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


def test_a_single_key_does_nothing_once_it_is_switched_off(page: Page, live_server, applicant):
    """WCAG 2.1.4, level A. The switch is under Settings → Appearance."""
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/settings/appearance/")
    page.locator("#id_keyboard_shortcuts").uncheck()
    page.get_by_role("button", name="Save").click()
    page.goto(f"{live_server.url}/")

    page.locator("body").press("/")

    assert focused_id(page) != "site-search", "the key is off, so the box did not take focus"
