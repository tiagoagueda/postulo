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


def settled(page: Page) -> None:
    """Wait until the table htmx swapped in has been wired.

    htmx attaches what it swapped in when the swap *settles*, twenty milliseconds after it
    lands, and marks the new elements `htmx-settling` until then. A control that was
    replaced by the swap -- a list, a date, a tick box; a text box is kept, not replaced --
    hears nothing in that window. A hand is never that quick and a test is, one run in
    three (#161), so a test that uses a replaced control twice waits here in between."""
    page.wait_for_function(
        "() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


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
    settled(page)

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


# ---------------------------- what is not a control still travels with the filters (#647)


def names_shown(page: Page) -> list[str]:
    return page.locator("#companies-table tbody tr [data-cell-value]").all_inner_texts()


def test_a_search_that_matches_nothing_keeps_the_sort(page: Page, live_server, applicant):
    """The sort in force is a hidden field in the table's header, and the header was drawn
    only when there were rows: a search that matched nothing took it off the page, and the
    search typed after it was sent with no sort. The header is drawn over no rows now, so
    the sort survives the empty answer and comes back with the rows."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?sort=-name")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(2)
    assert names_shown(page) == ["Black Mesa", "Aperture Science"]
    box = page.locator("#site-search")

    box.fill("zzzz")
    expect(page.locator("#companies-table")).to_contain_text("Nothing matches these filters")
    expect(rows).to_have_count(0)
    expect(page.locator("#companies-table thead")).to_be_visible()
    expect(page.locator("#sort-name")).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))

    box.fill("a")
    expect(rows).to_have_count(2)
    expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
    assert names_shown(page) == ["Black Mesa", "Aperture Science"]


def test_a_filter_that_matches_nothing_can_be_loosened_where_it_is(
    page: Page, live_server, applicant
):
    """The same header holds the filter that emptied the table. It used to go with the rows,
    which left *Clear* as the only way out; it stays, open, and what is typed into it next
    brings the rows back in the order they were in."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?sort=-name")
    rows = page.locator("#companies-table tbody tr")

    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("zzzz")
    expect(page.locator("#companies-table")).to_contain_text("Nothing matches these filters")
    expect(page.locator("#filter-location")).to_be_visible()
    expect(page.locator("#filter-location")).to_be_focused()

    page.locator("#filter-location").fill("e")
    expect(rows).to_have_count(2)
    expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
    assert names_shown(page) == ["Black Mesa", "Aperture Science"]


def test_with_scripts_off_an_empty_answer_keeps_the_sort(browser: Browser, live_server, applicant):
    """Without a script the masthead's box always carried the whole address. The filter
    form did not: *Apply* over an empty answer posted a form whose sort had gone with the
    header."""
    two_companies(applicant)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?sort=-name&location=zzzz")
        expect(page.locator("#companies-table")).to_contain_text("Nothing matches these filters")
        expect(page.locator("#companies-table tbody tr")).to_have_count(0)

        expect(page.locator("#filter-location")).to_be_visible()
        page.locator("#filter-location").fill("e")
        page.get_by_role("button", name="Apply").click()

        expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
        expect(page.locator("#companies-table tbody tr")).to_have_count(2)
        assert names_shown(page) == ["Black Mesa", "Aperture Science"]
    finally:
        context.close()


def a_group_and_a_stranger(applicant) -> None:
    from postulo.jobs.models import Company

    top = Company.objects.create(owner=applicant, name="Alphabet", location="California")
    Company.objects.create(owner=applicant, name="Google", location="Dublin", parent=top)
    Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")


def test_a_live_filter_keeps_the_table_to_its_group(page: Page, live_server, applicant):
    """`?group=` is a link's doing and no control held it, so the first live filter, sort or
    search asked for the table without it. The filter form carries it now."""
    a_group_and_a_stranger(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?group=Alphabet")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(2)

    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("dublin")
    expect(rows).to_have_count(1)
    expect(page).to_have_url(re.compile(r"[?&]group=Alphabet(&|$)"))

    # Loosened again, it is the group that comes back and not every company.
    page.locator("#filter-location").fill("")
    expect(rows).to_have_count(2)
    expect(page).to_have_url(re.compile(r"[?&]group=Alphabet(&|$)"))
    expect(page.locator("#companies-table")).not_to_contain_text("Black Mesa")

    # A search and a sort keep it too.
    page.locator("#site-search").fill("o")
    expect(page).to_have_url(re.compile(r"[?&]q=o(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]group=Alphabet(&|$)"))
    page.locator("#sort-name").click()
    # The table is in name order already, so the click asks for the other direction. The
    # address said `sort=name` before the click as well: waiting for that waited for nothing.
    expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]group=Alphabet(&|$)"))
    expect(page.locator("#companies-table")).not_to_contain_text("Black Mesa")


def test_with_scripts_off_apply_keeps_the_table_to_its_group(
    browser: Browser, live_server, applicant
):
    a_group_and_a_stranger(applicant)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?group=Alphabet")
        expect(page.locator("#companies-table tbody tr")).to_have_count(2)

        page.locator('[data-col="location"] summary').click()
        page.locator("#filter-location").fill("dublin")
        page.get_by_role("button", name="Apply").click()

        expect(page).to_have_url(re.compile(r"[?&]group=Alphabet(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]location=dublin(&|$)"))
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)
    finally:
        context.close()


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


# ------------------------------------------- a filter the page was loaded with (#622)

PHONE = {"width": 390, "height": 740}


def times_in_the_address(page: Page, name: str) -> int:
    return len(re.findall(rf"[?&]{name}=", page.url))


def test_a_filter_the_page_was_loaded_with_can_be_cleared_and_changed_from_its_header(
    page: Page, live_server, applicant
):
    """A saved view, a bookmark, a reload after filtering, Back: each loads the page with a
    filter in its address, and the header box for it then did nothing. The copy of the
    filter in the phone block sat in the same form, still held the value the page came
    with, and was read first -- so clearing the box, or typing another name, left the table
    as it was, and only *Clear* got out. Each copy has its own form now."""
    two_companies(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?name=aperture")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(1)
    box = page.locator("#filter-name")
    expect(box).to_have_value("aperture")

    box.fill("")
    expect(rows).to_have_count(2)
    expect(page).not_to_have_url(re.compile(r"name=aperture"))

    box.fill("black")
    expect(rows).to_have_count(1)
    expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
    expect(page).to_have_url(re.compile(r"[?&]name=black(&|$)"))
    assert times_in_the_address(page, "name") == 1, f"posted twice: {page.url}"
    expect(box).to_be_focused()


def test_with_scripts_off_a_filter_the_page_was_loaded_with_can_be_changed(
    browser: Browser, live_server, applicant
):
    """*Apply* posted both copies in the same order, so it was as stuck without a script."""
    two_companies(applicant)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?name=aperture")
        rows = page.locator("#companies-table tbody tr")
        expect(rows).to_have_count(1)

        page.locator("#filter-name").fill("black")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(rows).to_have_count(1)
        expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
        assert times_in_the_address(page, "name") == 1, page.url

        page.locator("#filter-name").fill("")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(rows).to_have_count(2)
    finally:
        context.close()


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_on_a_phone_narrow_changes_a_filter_the_page_was_loaded_with(
    browser: Browser, live_server, applicant, scripts
):
    """The other way round, on a phone: a field changed under *Narrow* was overridden by the
    header's copy when that one was filled as the page loaded. *Narrow* is a form of its
    own with a button of its own, holding the question as it stands and carrying the sort,
    so what it sends is its own fields and the rest of the address, once each."""
    two_companies(applicant)
    context = browser.new_context(java_script_enabled=scripts, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?name=aperture&sort=-name")
        rows = page.locator("#companies-table tbody tr")
        expect(rows).to_have_count(1)

        page.locator("[data-narrow] summary").click()
        field = page.locator("#filter-name-narrow")
        expect(field).to_have_value("aperture")
        field.fill("mesa")
        page.locator("[data-narrow-form]").get_by_role("button", name="Apply").click()

        expect(rows).to_have_count(1)
        expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
        expect(page).to_have_url(re.compile(r"[?&]name=mesa(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
        assert times_in_the_address(page, "name") == 1, page.url
        # Both copies came back holding the answer's value.
        expect(page.locator("#filter-name")).to_have_value("mesa")
        page.locator("[data-narrow] summary").click()
        expect(page.locator("#filter-name-narrow")).to_have_value("mesa")

        page.locator("#filter-name-narrow").fill("")
        page.locator("[data-narrow-form]").get_by_role("button", name="Apply").click()
        expect(rows).to_have_count(2)
        expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
    finally:
        context.close()


def test_narrow_holds_what_a_header_last_asked(browser: Browser, live_server, applicant):
    """The block is drawn with the table, so a filter typed into a header is what its copy
    under *Narrow* holds afterwards. Outside the swap it kept the values the page was loaded
    with, and would have put them back."""
    two_companies(applicant)
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?name=aperture")
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)

        page.locator("#filter-name").fill("mesa")
        expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
        page.locator("[data-narrow] summary").click()
        expect(page.locator("#filter-name-narrow")).to_have_value("mesa")
    finally:
        context.close()


# ------------------- what is entered under Narrow, and the one button that sends it (#622)


def a_list_among_the_columns(applicant) -> None:
    """*Kind* is a list, and is off until somebody adds it: with it on, the block holds a
    box and a list, the two shapes a field is put back in."""
    from postulo.core import tables
    from postulo.jobs.models import Company

    Company.objects.filter(owner=applicant, name="Black Mesa").update(kind="employment_service")
    tables.save_settings(applicant, "companies", {"columns": ["name", "kind", "location"]})


def test_what_is_entered_under_narrow_outlives_a_live_swap(
    browser: Browser, live_server, applicant
):
    """The block is drawn with the table, so anything live that swaps the table replaces it:
    a name typed and a kind chosen under *Narrow*, and then a search typed in the masthead,
    came back as a folded block holding neither. What was entered and not yet applied is
    put back in the block that replaces it, still open, and its own button then sends it
    with the search the swap answered."""
    two_companies(applicant)
    a_list_among_the_columns(applicant)
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?sort=-name")
        rows = page.locator("#companies-table tbody tr")
        expect(rows).to_have_count(2)

        page.locator("[data-narrow] > summary").click()
        page.locator("#filter-name-narrow").fill("mesa")
        page.locator("#filter-kind-narrow").select_option("employment_service")

        # The address is pushed in the turn the table is swapped in, so once it says the
        # search, the block on the page is the one the answer brought.
        page.locator("[data-search-link]").click()
        page.locator("#site-search").fill("a")
        expect(page).to_have_url(re.compile(r"[?&]q=a(&|$)"))

        # Nothing under Narrow was sent, and nothing under it was lost.
        expect(rows).to_have_count(2)
        assert "mesa" not in page.url and "employment_service" not in page.url, page.url
        expect(page.locator("[data-narrow]")).to_have_attribute("open", "")
        expect(page.locator("#filter-name-narrow")).to_have_value("mesa")
        expect(page.locator("#filter-kind-narrow")).to_have_value("employment_service")
        # A field nobody touched holds the answer's value, as it did.
        expect(page.locator("#filter-location-narrow")).to_have_value("")

        page.keyboard.press("Escape")
        page.locator("[data-narrow-form]").get_by_role("button", name="Apply").click()
        expect(rows).to_have_count(1)
        expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
        for kept in ("name=mesa", "kind=employment_service", "q=a", "sort=-name"):
            expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))
    finally:
        context.close()


def test_narrow_takes_the_answer_for_a_filter_its_header_has_changed_since(
    browser: Browser, live_server, applicant
):
    """Only what the swap did not itself answer is put back. A name typed under *Narrow*
    and then another typed into the *Name* header is one filter asked twice, and the
    header's is the later word: the block holds what the table is narrowed by."""
    two_companies(applicant)
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?name=a")
        page.locator("[data-narrow] > summary").click()
        page.locator("#filter-name-narrow").fill("aperture")
        page.locator("#filter-location-narrow").fill("cam")

        page.locator("#filter-name").fill("mesa")
        expect(page).to_have_url(re.compile(r"[?&]name=mesa(&|$)"))

        expect(page.locator("[data-narrow]")).to_have_attribute("open", "")
        expect(page.locator("#filter-name-narrow")).to_have_value("mesa")
        expect(page.locator("#filter-location-narrow")).to_have_value("cam")
    finally:
        context.close()


def test_an_answer_that_lands_while_narrow_is_typed_in_leaves_the_hand_where_it_was(
    browser: Browser, live_server, applicant
):
    """A search is answered three hundred milliseconds after its last letter and however
    long the server takes after that, which is time enough to have opened *Narrow* and be
    typing in it. The answer replaces the field under the hand: the letters, the focus and
    the caret are all put back, so the next key goes where the last one did."""
    two_companies(applicant)
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    held: list = []

    def hold_the_search(route) -> None:
        if route.request.headers.get("hx-trigger") == "site-search" and not held:
            held.append(route)
        else:
            route.continue_()

    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/")
        page.route(re.compile(r"/jobs/companies/[?]"), hold_the_search)

        page.locator("[data-search-link]").click()
        page.locator("#site-search").fill("a")
        page.wait_for_function("() => document.querySelector('#site-search.htmx-request')")
        page.locator("[data-narrow] > summary").click()
        field = page.locator("#filter-name-narrow")
        field.click()
        field.press_sequentially("mesa")
        field.press("ArrowLeft")
        assert held, "the search's request was sent, and is held"

        held[0].continue_()
        expect(page).to_have_url(re.compile(r"[?&]q=a(&|$)"))

        expect(field).to_be_visible()
        expect(field).to_be_focused()
        expect(field).to_have_value("mesa")
        assert field.evaluate("el => [el.selectionStart, el.selectionEnd]") == [3, 3]
        page.keyboard.type("s")
        expect(field).to_have_value("messa")
    finally:
        context.close()


def two_listings(applicant) -> None:
    from postulo.jobs.models import Company, JobPosting

    for name, title in (("Aperture Science", "Test Engineer"), ("Black Mesa", "Researcher")):
        company = Company.objects.get(owner=applicant, name=name)
        JobPosting.objects.create(owner=applicant, company=company, title=title)


#: A table page with a filter form of its own above its table: its address, the element
#: its rows are in, the filter of its first column, what to type there, and the row left.
ONE_BUTTON = {
    "companies": ("/jobs/companies/", "#companies-table", "name", "mesa", "Black Mesa"),
    "listings": ("/listings/", "#listings-table", "title", "engineer", "Test Engineer"),
}


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
@pytest.mark.parametrize("where", ONE_BUTTON)
def test_on_a_phone_one_button_applies_the_filters_and_it_is_under_them(
    browser: Browser, live_server, applicant, where, scripts
):
    """*Narrow* is where a phone filters, and its button is under its fields. The page's
    filter form had an *Apply* of its own above the block: two buttons with one word on
    them, and the upper one sent the page's form, which holds nothing entered under
    *Narrow*, so it answered with every row and a block folded over what had been typed.
    Below the width the headers take over at, the page's form draws nothing."""
    path, table, name, typed, shown = ONE_BUTTON[where]
    two_companies(applicant)
    two_listings(applicant)
    context = browser.new_context(java_script_enabled=scripts, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}{path}")
        rows = page.locator(f"{table} tbody tr")
        expect(rows).to_have_count(2)

        page.locator("[data-narrow] > summary").click()
        field = page.locator(f"#filter-{name}-narrow")
        field.fill(typed)

        buttons = page.get_by_role("button", name="Apply", exact=True)
        expect(buttons).to_have_count(1)
        expect(
            page.locator("[data-narrow-form]").get_by_role("button", name="Apply")
        ).to_be_visible()
        assert buttons.bounding_box()["y"] > field.bounding_box()["y"], "under what it sends"

        buttons.click()
        expect(page).to_have_url(re.compile(rf"[?&]{name}={typed}(&|$)"))
        expect(rows).to_have_count(1)
        expect(page.locator(table)).to_contain_text(shown)
    finally:
        context.close()


def test_with_scripts_off_on_a_phone_enter_in_a_headers_box_still_sends_the_pages_form(
    browser: Browser, live_server, applicant
):
    """The page's form is not drawn on a phone, and it is still the form the headers'
    controls belong to. A header in force is drawn open inside the table, and Enter in its
    box sends that form as it did: the button a browser presses for Enter is the form's
    first, drawn or not."""
    two_companies(applicant)
    context = browser.new_context(java_script_enabled=False, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/companies/?location=mexico&sort=-name")
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)

        page.locator("#filter-location").fill("cambridge")
        page.locator("#filter-location").press("Enter")

        expect(page).to_have_url(re.compile(r"[?&]location=cambridge(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]sort=-name(&|$)"))
        expect(page.locator("#companies-table")).to_contain_text("Aperture Science")
        assert times_in_the_address(page, "location") == 1, page.url
    finally:
        context.close()
