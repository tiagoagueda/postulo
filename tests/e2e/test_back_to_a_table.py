"""Back to a table that was narrowed live is the page, not the table alone (#646).

A table page answers an htmx request with its table alone, at the page's own address, which
is how a search, a filter or a sort gets into the address bar as it is applied. The answer
did not say that it depended on who was asking, so the browser's cache kept the fragment
under the page's address; and Back, which asks the cache for that address as a document, was
handed the fragment: `<html><head></head><body><div id="companies-table">`, with no title,
no masthead and no stylesheet.

`tests/test_tables.py` reads the header that says so, on every answer of the three pages.
This is what the header is for: narrow the table, open a row, press Back, and find the page.
Chromium as Playwright runs it has no back/forward cache, so Back goes to the HTTP cache,
which is the case that failed; a browser that restores the page from memory never asked.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import sign_in
from .test_site_search import PAGES, tables  # noqa: F401

pytestmark = pytest.mark.e2e

#: What each page is called in its `<title>`, after the instance's name.
TITLES = {
    "/jobs/companies/": "Companies",
    "/applications/": "Applications",
    "/listings/": "Listings",
}

#: A link in a row that leaves the page: not one that swaps an editor into its cell.
A_ROW = "tbody tr a[href^='/']:not([hx-get])"


def the_whole_page_is_there(page: Page, path: str) -> None:
    """Its title, its masthead with the search box, and a stylesheet to draw them with."""
    expect(page).to_have_title(re.compile(rf"{TITLES[path]}$"))
    expect(page.locator("header[data-site-header]")).to_be_visible()
    expect(page.locator("header [data-nav-main]")).to_be_visible()
    expect(page.locator("#site-search")).to_be_visible()
    expect(page.locator("main h1")).to_have_text(TITLES[path])
    assert page.evaluate("() => document.styleSheets.length") >= 1, "the page has no stylesheet"
    assert page.evaluate("() => document.head.children.length") > 3, "the page has no head"


@pytest.mark.parametrize("path", PAGES)
def test_back_after_a_live_search_is_the_whole_page_and_the_narrowed_table(
    page: Page,
    live_server,
    tables,  # noqa: F811
    path,
):
    table, _label, before, after = PAGES[path]
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}{path}")
    rows = page.locator(f"{table} tbody tr")
    expect(rows).to_have_count(before)

    page.locator("#site-search").fill("aperture")
    expect(rows).to_have_count(after)
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    narrowed = page.url

    page.locator(f"{table} {A_ROW}").first.click()
    expect(page).not_to_have_url(narrowed)
    expect(page.locator(table)).to_have_count(0)

    page.go_back()
    expect(page).to_have_url(narrowed)
    the_whole_page_is_there(page, path)
    expect(page.locator("#site-search")).to_have_value("aperture")
    expect(page.locator(f"{table} tbody tr")).to_have_count(after)


def test_back_after_a_sort_is_the_whole_page_and_the_sorted_table(
    page: Page,
    live_server,
    tables,  # noqa: F811
):
    """A click on a sort icon alone was enough: nothing here is the search box's doing."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")
    expect(page.locator("#companies-table tbody tr")).to_have_count(3)

    page.locator("#sort-name").click()
    expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
    expect(page.locator("#companies-table tbody tr").first).to_contain_text("Black Mesa")
    sorted_address = page.url

    page.locator(f"#companies-table {A_ROW}").first.click()
    expect(page).not_to_have_url(sorted_address)

    page.go_back()
    expect(page).to_have_url(sorted_address)
    the_whole_page_is_there(page, "/jobs/companies/")
    expect(page.locator("#companies-table tbody tr")).to_have_count(3)
    expect(page.locator("#companies-table tbody tr").first).to_contain_text("Black Mesa")
