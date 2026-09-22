"""Narrowing a table from its own headers, in a real browser (#253).

`tests/test_tables.py` reads the markup. What it cannot say is whether the gesture works:
that clicking a column's label opens that column's filter, that typing into it narrows the
table, and that the column comes back marked so nobody spends ten minutes wondering where
their companies went.

The last test is the one that earns its keep. The whole design rests on the claim that a
`<details>` needs no script -- that is why the filter is folded away with a disclosure
rather than built by `app.js` -- and the only honest way to check that claim is to switch
JavaScript off and use the thing.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def two_companies(applicant):
    from postulo.jobs.models import Company

    Company.objects.create(owner=applicant, name="Aperture Science", location="Cambridge")
    Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")


def test_the_filter_is_not_on_screen_until_the_header_is_clicked(
    page: Page, live_server, applicant
):
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    box = page.locator("#filter-location")
    expect(box).to_be_hidden()

    page.locator('[data-col="location"] summary').click()

    expect(box).to_be_visible()


def test_typing_in_a_header_narrows_the_table_and_marks_the_column(
    page: Page, live_server, applicant
):
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(2)

    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("mexico")

    expect(rows).to_have_count(1)
    expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
    expect(page.get_by_label("Location is filtered")).to_be_visible()
    expect(page.locator("#filter-location")).to_be_visible()


def test_a_narrowed_column_comes_back_open_on_a_fresh_page(page: Page, live_server, applicant):
    """The address carries the filter, so somebody arriving on a shared link -- or coming
    back with the back button -- sees which column is doing it."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?location=mexico")

    expect(page.locator("#filter-location")).to_be_visible()
    expect(page.locator("#filter-location")).to_have_value("mexico")
    expect(page.locator('[data-col="name"] #filter-name')).to_be_hidden()


def test_sorting_is_an_icon_and_keeps_the_focus_it_swapped_away(page: Page, live_server, applicant):
    """The label became the filter, so the sort is icon-only. It still has to hand focus to
    its replacement, or the next Tab starts again at the skip link (#227)."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    page.locator("#sort-name").click()

    expect(page.locator("#companies-table tbody tr").first).to_contain_text("Black Mesa")
    expect(page.locator("#sort-name")).to_be_focused()


def test_the_filter_opens_and_applies_with_no_script_at_all(
    browser: Browser, live_server, applicant
):
    """The reason this is a disclosure and not a widget. Postulo's rule is that a script
    *adds* a control and never animates a dead one, so with JavaScript off the header still
    opens, the input is still a real control bound to the form by `form=`, and *Apply*
    still narrows the table (#134, #136)."""
    two_companies(applicant)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/")

        expect(page.locator("#filter-location")).to_be_hidden()
        page.locator('[data-col="location"] summary').click()
        expect(page.locator("#filter-location")).to_be_visible()

        page.locator("#filter-location").fill("mexico")
        page.get_by_role("button", name="Apply").click()

        expect(page).to_have_url(re.compile(r"location=mexico"))
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)
        expect(page.get_by_label("Location is filtered")).to_be_visible()
    finally:
        context.close()
