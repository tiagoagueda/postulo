"""One search box, used (#313).

`tests/test_site_search.py` reads the markup: one box named `q` on each table page, in the
masthead, whose form goes to the page, with a second button that searches everything. What
it cannot say is whether the box does the job the page's own box did. These use it:

* typing narrows the table to **the same rows** the page gives for that `q` on its own, the
  address carries it, and Enter asks for the same without reloading the page;
* a search and a column filter narrow together, whichever moved last;
* *Search everything* lands on the results page with the words;
* all of it again **with scripts off**, where the choice between the two is two buttons on
  one form and Enter keeps the filter and the sort already in force;
* on a phone the magnifier opens the same box, which fits at 320 pixels in the widest
  languages, and `/` still finds it -- and the open panel never hides whatever has the
  focus, with scripts on, where it closes as the focus leaves, and off, where the page
  makes room for it;
* the newest question wins: an answer held back never lands over a newer one, whichever
  control asked, and a filter changed with a search typed goes back to the first page;
* the masthead keeps to one line at rest under the text-spacing override with scripts
  off, which is what the stylesheet's counts promise;
* and axe, in both themes, with the panel open.
"""

from __future__ import annotations

import re
import threading

import pytest
from playwright.sync_api import Browser, Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_navigation_bar import row_is_one_line, set_language
from .test_reflow import SCROLLS_SIDEWAYS
from .test_text_spacing import ADOPT, TEXT_SPACING

pytestmark = pytest.mark.e2e

#: Each table page, the element its rows are in, what the box is called there, and how many
#: of the rows `tables` gives it are there before and after "aperture".
PAGES = {
    "/jobs/companies/": ("#companies-table", "Search companies", 3, 2),
    "/applications/": ("#applications-table", "Search applications", 2, 1),
    "/listings/": ("#listings-table", "Search listings", 2, 1),
}

PHONE = {"width": 320, "height": 640}


@pytest.fixture
def tables(applicant):
    """Two of everything and a third company: two of the three companies match "aperture",
    and one of the two applications and one of the two listings still to decide."""
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    aperture = Company.objects.create(
        owner=applicant, name="Aperture Science", location="Cambridge"
    )
    mesa = Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")
    Company.objects.create(owner=applicant, name="Aperture Labs", location="New Mexico")
    for company, title in ((aperture, "Test Engineer"), (mesa, "Research Associate")):
        posting = JobPosting.objects.create(owner=applicant, company=company, title=title)
        Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)
    JobPosting.objects.create(owner=applicant, company=aperture, title="Portal Researcher")
    JobPosting.objects.create(owner=applicant, company=mesa, title="Resonance Technician")
    return {"applicant": applicant}


def rows_of(page: Page, table: str) -> list[str]:
    """The rows the table shows, by their text, in order."""
    return [" ".join(text.split()) for text in page.locator(f"{table} tbody tr").all_inner_texts()]


def the_same_question_asked_directly(page: Page, base: str, path: str, table: str) -> list[str]:
    """The rows the page gives for `?q=aperture` on its own: what its own box used to give."""
    other = page.context.new_page()
    try:
        other.goto(f"{base}{path}?q=aperture")
        return rows_of(other, table)
    finally:
        other.close()


def mark_the_page(page: Page) -> None:
    """Something only this document has, which a reload would take away."""
    page.evaluate("() => { window.stillThisPage = true; }")


def still_the_same_page(page: Page) -> bool:
    return page.evaluate("() => window.stillThisPage === true")


# ------------------------------------------------------------------ with scripts on


@pytest.mark.parametrize("path", PAGES)
def test_typing_narrows_the_table_as_the_pages_own_box_did(page: Page, live_server, tables, path):
    table, label, before, after = PAGES[path]
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}{path}")
    expect(page.locator(f"{table} tbody tr")).to_have_count(before)
    expected = the_same_question_asked_directly(page, base, path, table)
    assert len(expected) == after, expected
    mark_the_page(page)

    box = page.get_by_role("searchbox", name=label)
    expect(box).to_have_attribute("placeholder", label)
    box.fill("aperture")

    expect(page.locator(f"{table} tbody tr")).to_have_count(after)
    assert rows_of(page, table) == expected
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    expect(box).to_be_focused()

    # Enter is the table's search too, and asks htmx rather than reloading the page.
    box.press("Enter")
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    assert rows_of(page, table) == expected
    assert still_the_same_page(page), "Enter reloaded the page"
    expect(page.locator(f"{table.removesuffix('-table')}-count")).to_contain_text(f"{after} ")


def test_a_search_and_a_column_filter_narrow_together(page: Page, live_server, tables):
    """The box includes the filter form and the form includes the box, so neither forgets
    the other whichever moved last."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    box = page.locator("#site-search")

    box.fill("aperture")
    expect(rows).to_have_count(2)
    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("mexico")
    expect(rows).to_have_count(1)
    expect(page.locator("#companies-table")).to_contain_text("Aperture Labs")
    expect(page).to_have_url(re.compile(r"q=aperture"))
    expect(page).to_have_url(re.compile(r"location=mexico"))

    box.fill("")
    expect(rows).to_have_count(2)
    expect(page.locator("#companies-table")).to_contain_text("Black Mesa")
    expect(page.locator("#filter-location")).to_have_value("mexico")

    # And a sort keeps the search, because the sort links are drawn from the last answer.
    box.fill("aperture")
    expect(rows).to_have_count(1)
    page.locator("#sort-name").click()
    expect(page).to_have_url(re.compile(r"q=aperture"))
    expect(rows).to_have_count(1)


@pytest.mark.parametrize("path", PAGES)
def test_search_everything_lands_on_the_results_page_with_the_words(
    page: Page, live_server, tables, path
):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}{path}")
    page.locator("#site-search").fill("aperture")
    page.get_by_role("button", name="Search everything").click()

    expect(page).to_have_url(f"{base}/search/?q=aperture")
    expect(page.locator("#search-q")).to_have_value("aperture")
    expect(page.locator("main")).to_contain_text("Aperture Science")


def test_the_slash_key_still_finds_the_box(page: Page, live_server, tables):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")
    page.locator("body").press("/")
    expect(page.locator("#site-search")).to_be_focused()

    page.set_viewport_size(PHONE)
    page.goto(f"{base}/jobs/companies/")
    expect(page.locator("#site-search")).to_be_hidden()
    page.locator("body").press("/")
    expect(page.locator("#site-search")).to_be_visible()
    expect(page.locator("#site-search")).to_be_focused()


# ------------------------------------------------------- the newest question wins


@pytest.fixture
def held(monkeypatch):
    """Hold the server's answer to one question until the test lets it go.

    `held.only(view, wanted)` makes `view` wait before answering any request `wanted` says
    yes to; `held.arrived` is set when such a request has reached the server, and
    `held.release` lets it answer. Held on the server rather than in the browser's routing,
    so the second request is free to overtake the first, which is the case being tested."""

    class Held:
        def __init__(self):
            self.arrived = threading.Event()
            self.release = threading.Event()

        def only(self, view, wanted):
            original = view.get

            def get(view_self, request, *args, **kwargs):
                if wanted(request):
                    self.arrived.set()
                    self.release.wait(10)
                return original(view_self, request, *args, **kwargs)

            monkeypatch.setattr(view, "get", get)

    holding = Held()
    yield holding
    holding.release.set()


def test_a_slow_answer_to_the_box_never_lands_over_a_newer_one_from_the_form(
    page: Page, live_server, tables, held
):
    """Type a search, and while its answer is still on its way choose a status. The form's
    answer is the newer question -- it carries the search too -- and the box's, when it
    comes, must not be drawn over it or put its address back. Before the box and the form
    took turns on one element, it was: the address and the count said *aperture, any
    status* over a table that said nothing matched."""
    from postulo.applications.views import ApplicationListView

    held.only(
        ApplicationListView,
        lambda request: request.GET.get("q") == "aperture" and not request.GET.get("status"),
    )
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")
    box = page.locator("#site-search")
    box.fill("aperture")
    assert held.arrived.wait(10), "the box's request never reached the server"

    page.locator("#filter-status").select_option("rejected")
    expect(page.locator("#applications-table")).to_contain_text("Nothing matches these filters")
    held.release.set()
    page.wait_for_timeout(700)  # long enough for the answer held back to land, if it could

    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]status=rejected(&|$)"))
    expect(page.locator("#applications-table")).to_contain_text("Nothing matches these filters")
    expect(page.locator("#applications-table tbody tr")).to_have_count(0)
    expect(page.locator("#applications-count")).to_contain_text("0 ")
    expect(box).to_have_value("aperture")
    expect(page.locator("#filter-status")).to_have_value("rejected")


def test_a_slow_answer_to_a_column_filter_never_lands_over_a_newer_one_from_the_box(
    page: Page, live_server, tables, held
):
    """The other way round, and a column's own filter rather than the form: its answer is
    held, a search is typed, and the search's answer -- which carries the column's filter
    as well -- is the one that stays."""
    from postulo.jobs.views import CompanyListView

    held.only(
        CompanyListView,
        lambda request: request.GET.get("location") == "mexico" and not request.GET.get("q"),
    )
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(3)
    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("mexico")
    assert held.arrived.wait(10), "the column filter's request never reached the server"

    page.locator("#site-search").fill("aperture")
    expect(rows).to_have_count(1)
    held.release.set()
    page.wait_for_timeout(700)

    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]location=mexico(&|$)"))
    expect(rows).to_have_count(1)
    expect(page.locator("#companies-table")).to_contain_text("Aperture Labs")
    expect(page.locator("#companies-count")).to_contain_text("1 ")
    expect(page.locator("#site-search")).to_have_value("aperture")
    expect(page.locator("#filter-location")).to_have_value("mexico")


def test_a_status_chosen_with_a_search_typed_narrows_both_and_returns_to_page_one(
    page: Page, live_server, tables
):
    """The filter form includes the box, so a status chosen after a search narrows by both;
    and it starts again at the first page, because the second page of one question is not
    a place in the answer to another."""
    from postulo.applications.models import Application, Status
    from postulo.core import tables as core_tables
    from postulo.jobs.models import Company, JobPosting

    applicant = tables["applicant"]
    aperture = Company.objects.get(owner=applicant, name="Aperture Science")
    for number in range(30):
        posting = JobPosting.objects.create(
            owner=applicant, company=aperture, title=f"Enrichment Associate {number:02d}"
        )
        status = Status.APPLIED if number < 27 else Status.DRAFT
        Application.objects.create(owner=applicant, posting=posting, status=status)
    core_tables.save_settings(applicant, "applications", {"page_size": 25})

    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")
    rows = page.locator("#applications-table tbody tr")
    expect(rows).to_have_count(25)
    page.locator("#site-search").fill("aperture")
    expect(page.locator("#applications-count")).to_contain_text("31 ")
    page.locator("#page-next").click()
    expect(page).to_have_url(re.compile(r"[?&]page=2(&|$)"))
    expect(rows).to_have_count(6)

    page.locator("#filter-status").select_option("applied")
    expect(page.locator("#applications-count")).to_contain_text("28 ")
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
    assert "page=" not in page.url, page.url
    expect(rows).to_have_count(25)
    expect(page.locator("#page-prev")).to_have_count(0)
    expect(page.locator("#page-next")).to_be_visible()
    expect(page.locator("#site-search")).to_have_value("aperture")


def test_enter_after_the_table_has_narrowed_is_not_a_second_place_in_the_history(
    page: Page, live_server, tables
):
    """Two elements ask when Enter is pressed: the box, which already has, and the button
    Enter presses. Both answers are for one address, and the history holds it once -- one
    Back leaves the search."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(3)
    before = page.evaluate("history.length")
    box = page.locator("#site-search")
    box.fill("aperture")
    expect(rows).to_have_count(2)
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    assert page.evaluate("history.length") == before + 1

    with page.expect_response(lambda response: "q=aperture" in response.url):
        box.press("Enter")
    page.wait_for_timeout(300)
    expect(rows).to_have_count(2)
    assert page.evaluate("history.length") == before + 1, "Enter pushed the same address again"

    page.go_back()
    expect(page).to_have_url(f"{base}/jobs/companies/")
    expect(page.locator("#companies-table tbody tr")).to_have_count(3)


# ----------------------------------------------------------------- with scripts off


@pytest.mark.parametrize("path", PAGES)
def test_with_scripts_off_enter_narrows_and_the_other_button_searches_everything(
    browser: Browser, live_server, tables, path
):
    table, label, _before, _after = PAGES[path]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, base)
        expected = the_same_question_asked_directly(page, base, path, table)
        page.goto(f"{base}{path}")

        box = page.get_by_role("searchbox", name=label)
        box.fill("aperture")
        box.press("Enter")
        expect(page).to_have_url(re.compile(rf"{re.escape(path)}\?(.*&)?q=aperture(&|$)"))
        assert rows_of(page, table) == expected
        expect(page.get_by_role("searchbox", name=label)).to_have_value("aperture")

        # The same form carried the page's own parameters to get here; the search page
        # reads none of them and its address holds none of them.
        page.get_by_role("button", name="Search everything").click()
        expect(page).to_have_url(f"{base}/search/?q=aperture")
        expect(page.locator("#search-q")).to_have_value("aperture")
    finally:
        context.close()


def test_with_scripts_off_a_search_keeps_the_filter_and_the_sort(
    browser: Browser, live_server, tables
):
    """The box is outside the page's filter form, so its own form carries the rest of the
    question; and the filter form carries the search the other way."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/jobs/companies/?location=mexico&sort=-name")
        expect(page.locator("#companies-table tbody tr")).to_have_count(2)

        page.locator("#site-search").fill("aperture")
        page.locator("#site-search").press("Enter")
        expect(page).to_have_url(re.compile(r"location=mexico"))
        expect(page).to_have_url(re.compile(r"sort=-name"))
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)
        expect(page.locator("#companies-table")).to_contain_text("Aperture Labs")

        # And the other way: Apply, from the filter form, keeps the search in force.
        page.goto(f"{base}/jobs/companies/?q=aperture")
        expect(page.locator("#companies-table tbody tr")).to_have_count(2)
        page.locator('[data-col="location"] summary').click()
        page.locator("#filter-location").fill("mexico")
        page.get_by_role("button", name="Apply").click()
        expect(page).to_have_url(re.compile(r"q=aperture"))
        expect(page).to_have_url(re.compile(r"location=mexico"))
        expect(page.locator("#companies-table tbody tr")).to_have_count(1)
        expect(page.locator("#site-search")).to_have_value("aperture")

        # *Search everything* from a table filtered and sorted takes the words and leaves
        # the filter and the sort behind: they are the table's, and mean nothing there.
        page.goto(f"{base}/jobs/companies/?location=mexico&sort=-name")
        page.locator("#site-search").fill("aperture")
        page.get_by_role("button", name="Search everything").click()
        expect(page).to_have_url(f"{base}/search/?q=aperture")
        expect(page.locator("main")).to_contain_text("Aperture Science")
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_the_masthead_is_one_line_under_the_text_spacing_override_with_scripts_off(
    browser: Browser, live_server, tables, path
):
    """The row's counts are written to hold, with no script to fit it, under the
    text-spacing override in English at 1280 (`app.css`, the main navigation). The box and
    its two buttons take no more of the row than the box over everything does when it is
    open, 192 pixels, which is what those counts allow for -- they took 194, by the margin
    Basecoat gives an addon, and in the font CI draws in that was the masthead on two
    lines. With no script the box does not grow on focus either, so focused is the same."""
    base = live_server.url
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 1280, "height": 800}
    )
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}{path}")
        group = page.locator(".site-search .input-group").bounding_box()
        assert group["width"] <= 192, group
        margins = page.locator(".site-search .input-group > button").evaluate_all(
            "all => all.map((b) => getComputedStyle(b).marginInlineEnd)"
        )
        assert margins == ["0px", "0px"], margins

        page.evaluate(ADOPT, TEXT_SPACING)
        line = row_is_one_line(page)
        assert line["spread"] <= 2, f"the masthead wraps at rest on {path}: {line}"
        page.locator("#site-search").focus()
        line = row_is_one_line(page)
        assert line["spread"] <= 2, f"the masthead wraps with the box focused on {path}: {line}"
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
    finally:
        context.close()


def test_a_placeholder_that_does_not_fit_ends_in_an_ellipsis(page: Page, live_server, tables):
    """The placeholder is the only words on the screen that say which table the box
    searches, and at rest most of them are wider than the box: cut where the box ends they
    read as a word nobody has heard of (*Search applic*). The whole name is still the box's
    accessible name, whatever is drawn."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 1280, "height": 800})
    for path, (_table, label, _before, _after) in PAGES.items():
        page.goto(f"{base}{path}")
        box = page.get_by_role("searchbox", name=label, exact=True)
        assert box.evaluate("el => getComputedStyle(el).textOverflow") == "ellipsis", path


# ------------------------------------------------------------------------ a phone


@pytest.mark.parametrize("language", ["en", "el", "de"])
def test_on_a_phone_the_magnifier_opens_the_same_box_and_it_fits(
    page: Page, live_server, tables, language
):
    """The mode goes with the search to a phone (#299): the magnifier opens the box and its
    two buttons in a bar under the masthead, which at 320 pixels scrolls nothing sideways and
    keeps every control on the screen and a thumb's size."""
    set_language(tables, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    for path, (table, _label, _before, after) in PAGES.items():
        page.goto(f"{base}{path}")
        trigger = page.locator("header [data-search-link]")
        box = page.locator("#site-search")
        expect(box).to_be_hidden()

        trigger.click()
        expect(box).to_be_visible()
        expect(box).to_be_focused()
        result = page.evaluate(SCROLLS_SIDEWAYS)
        assert not result["reached"], f"{path} in {language}: {result}"
        for control in (box, *page.locator("[data-site-search] button").all()):
            where = control.bounding_box()
            assert where["x"] >= 0 and where["x"] + where["width"] <= PHONE["width"], where
            assert where["height"] >= 44, (path, where)
        for button in page.locator("[data-site-search] button").all():
            assert button.bounding_box()["width"] >= 44

        # Escape closes it and hands the focus back. Asked of an empty box: in one that holds
        # words, Chromium's first Escape empties the field, as it does in any search box.
        page.keyboard.press("Escape")
        expect(box).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        box.fill("aperture")
        expect(page.locator(f"{table} tbody tr")).to_have_count(after)
        expect(page).to_have_url(re.compile(r"q=aperture"))
        # The table was swapped under the open panel, and the panel is still there with the
        # focus in it: it closes when the focus leaves, and a swap is not the focus leaving.
        expect(box).to_be_visible()
        expect(box).to_be_focused()


def test_on_a_phone_with_scripts_off_the_box_opens_and_narrows(
    browser: Browser, live_server, tables
):
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/")
        page.locator("header [data-search-link]").click()
        box = page.locator("#site-search")
        expect(box).to_be_visible()
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
        box.fill("aperture")
        box.press("Enter")
        expect(page).to_have_url(re.compile(r"/applications/\?(.*&)?q=aperture"))
        expect(page.locator("#applications-table tbody tr")).to_have_count(1)
    finally:
        context.close()


#: Where the focus is, and whether the open panel is over it. The panel is a bar across the
#: window, so "over it" is any overlap between the two boxes from top to bottom.
FOCUS_AND_PANEL = """() => {
  const el = document.activeElement;
  const panel = document.querySelector('[data-site-search]');
  const open = panel.matches(':popover-open');
  if (!el || el === document.body) return {what: 'nothing', open, inside: false, under: false};
  const box = el.getBoundingClientRect();
  const bar = panel.getBoundingClientRect();
  const inside = panel.contains(el);
  return {
    what: el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 30),
    top: Math.round(box.top), bottom: Math.round(box.bottom),
    bar: [Math.round(bar.top), Math.round(bar.bottom)],
    open, inside,
    under: open && !inside && box.bottom > bar.top + 1 && box.top < bar.bottom - 1,
  };
}"""

#: A phone and a tablet: both are below `lg`, where the box is in the panel, and at the
#: second the buttons beside a page's title sit in the band the panel covers.
NARROW = ((390, 740), (800, 740))


@pytest.fixture
def many_companies(tables):
    """Enough companies that the table runs well past the foot of the window, so the browser
    has to scroll to each row the focus reaches."""
    from postulo.jobs.models import Company

    Company.objects.bulk_create(
        [
            Company(owner=tables["applicant"], name=f"Company {number:02d}", location="Ohio")
            for number in range(40)
        ]
    )
    return tables


@pytest.mark.parametrize("size", NARROW)
def test_the_open_panel_closes_when_the_focus_leaves_it(
    page: Page, live_server, many_companies, size
):
    """SC 2.4.11, Focus Not Obscured. The panel is a bar in the top layer just under the
    masthead, which is where the browser puts whatever it scrolls into view, and a popover
    stays open when Tab leaves it: the table's header, its sort links and every few rows
    took the focus behind it. So it closes as the focus goes anywhere else -- except to its
    own button, which is in the masthead and is how it is closed by hand."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"{base}/jobs/companies/")
    trigger = page.locator("header [data-search-link]")
    box = page.locator("#site-search")

    trigger.click()
    expect(box).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(trigger).to_be_focused()
    expect(box).to_be_visible()
    page.keyboard.press("Tab")
    expect(box).to_be_focused()

    # On through the panel's two buttons and out, and then down into the table: the panel
    # is open only while the focus is in it, and nothing focused is ever under it.
    seen = []
    for _ in range(30):
        page.keyboard.press("Tab")
        where = page.evaluate(FOCUS_AND_PANEL)
        seen.append(where)
        assert not where["under"], where
        assert where["inside"] or not where["open"], f"the panel stayed open: {where}"
    assert [where["inside"] for where in seen[:3]] == [True, True, False], seen[:3]
    assert any(where["what"] == "#sort-name" for where in seen), "the walk reached the table"
    expect(box).to_be_hidden()

    # And back up into it again: opened by its button, left by Shift+Tab past the button.
    trigger.click()
    expect(box).to_be_focused()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    expect(box).to_be_hidden()


@pytest.mark.parametrize("size", NARROW)
def test_with_scripts_off_the_open_panel_is_never_over_what_has_the_focus(
    browser: Browser, live_server, many_companies, size
):
    """Without a script nothing closes the panel but Escape and a click elsewhere, so the
    stylesheet keeps the focus clear of it while it is open: scroll padding for whatever
    the browser scrolls to, the page moved down for what is at its top and cannot be
    scrolled to, and the skip link below the bar instead of behind it. Walked forwards into
    the table and then backwards to the first thing on the page, on all three pages."""
    base = live_server.url
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": size[0], "height": size[1]}
    )
    page = context.new_page()
    try:
        sign_in(page, base)
        walks = (("/jobs/companies/", 45), ("/applications/", 14), ("/listings/", 14))
        for path, forwards in walks:
            page.goto(f"{base}{path}")
            page.locator("header [data-search-link]").click()
            expect(page.locator("#site-search")).to_be_visible()
            seen = []
            for key, presses in (("Tab", forwards), ("Shift+Tab", forwards + 14)):
                for _ in range(presses):
                    page.keyboard.press(key)
                    where = page.evaluate(FOCUS_AND_PANEL)
                    seen.append(where)
                    assert where["open"], f"{path}: the panel closed by itself: {where}"
                    assert not where["under"], f"{path}: focused behind the panel: {where}"
            outside = [w for w in seen if not w["inside"] and w["what"] != "nothing"]
            assert len(outside) > forwards, f"{path}: the walk never left the panel"
            assert any("skip-link" in w["what"] for w in seen), (
                f"{path}: the walk back never reached the skip link"
            )
    finally:
        context.close()


# ------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_box_passes_axe(live_server, page: Page, axe_source, tables, scheme):  # noqa: F811
    """In the row with a search in force, and in the panel on a phone, in both themes."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    failures = []
    for path in PAGES:
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base}{path}?q=aperture")
        page.locator("#site-search").focus()
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} in the row ({scheme})", found))

        page.set_viewport_size(PHONE)
        page.goto(f"{base}{path}")
        page.locator("header [data-search-link]").click()
        expect(page.locator("#site-search")).to_be_visible()
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} in the panel ({scheme})", found))
    assert not failures, "\n\n".join(failures)
