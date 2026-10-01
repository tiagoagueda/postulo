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
from urllib.parse import parse_qs, urlsplit

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


def test_emptying_a_header_filter_leaves_it_open_with_the_focus_in_it(
    page: Page, live_server, applicant
):
    """Clearing a filter to try another value is the ordinary way to use one. The header
    was drawn open only while its column narrowed, so the table that answered an emptied
    box folded the header shut around it: the box left the screen, the focus went to the
    document, and the next letters went nowhere (#626). The column whose control asked is
    drawn open, so the box stays where it is and what is typed next narrows again.

    That it is the server that draws it open is read off the answer itself, the markup htmx
    was sent for the emptied box, and not only off the page: a script may come to open a
    header as well, and the page alone could not then say which of the two had."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    disclosure = page.locator('[data-col="name"] [data-col-filter]')
    box = page.locator("#filter-name")

    def for_the_emptied_box(response) -> bool:
        asked = parse_qs(urlsplit(response.url).query, keep_blank_values=True)
        return (
            urlsplit(response.url).path == "/jobs/companies/"
            and response.request.headers.get("hx-trigger") == "filter-name"
            and set(asked.get("name", ["?"])) == {""}
        )

    page.locator('[data-col="name"] summary').click()
    box.press_sequentially("ap")
    expect(rows).to_have_count(1)
    expect(page.get_by_label("Name is filtered")).to_be_visible()

    with page.expect_response(for_the_emptied_box) as answer:
        box.press("Backspace")
        box.press("Backspace")
    drawn = answer.value.text().split('data-col="name"')[1].split("</th>")[0]
    assert "data-col-filter open" in drawn, "the answer draws the header that asked open"
    assert "is filtered" not in drawn, "open, and narrowing nothing"
    expect(rows).to_have_count(2)
    expect(disclosure).to_have_attribute("open", "")
    expect(box).to_be_visible()
    expect(box).to_be_focused()
    expect(page.get_by_label("Name is filtered")).to_have_count(0)

    # The keys that follow go into the box, and the table follows them.
    page.keyboard.type("black")
    expect(rows).to_have_count(1)
    expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
    expect(box).to_be_focused()


def test_choosing_any_in_a_header_list_leaves_it_open_with_the_focus_in_it(
    page: Page, live_server, applicant
):
    """The same for a list, which is not kept across the swap as a box is: htmx puts the
    focus back on the list that replaced it, by its id, and can only do that if the header
    it is in was drawn open (#626)."""
    from postulo.core import tables
    from postulo.jobs.models import Company

    two_companies(applicant)
    Company.objects.filter(owner=applicant, name="Black Mesa").update(kind="employment_service")
    tables.save_settings(applicant, "companies", {"columns": ["name", "kind", "location"]})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    disclosure = page.locator('[data-col="kind"] [data-col-filter]')
    choice = page.locator("#filter-kind")

    page.locator('[data-col="kind"] summary').click()
    choice.focus()  # where a hand or a key would have put it; `select_option` does not
    choice.select_option("employment_service")
    expect(rows).to_have_count(1)
    expect(choice).to_be_focused()

    choice.select_option("")
    expect(rows).to_have_count(2)
    expect(disclosure).to_have_attribute("open", "")
    expect(choice).to_be_visible()
    expect(choice).to_be_focused()
    expect(choice).to_have_value("")


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
