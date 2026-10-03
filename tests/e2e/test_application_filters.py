"""The applications table's filters, used from its column headers (#314).

`tests/test_application_filters.py` reads the markup and asks the queryset what each address
means. What it cannot say is whether a person can use any of it. These do:

* **nothing stands between the heading and the table**, and each of the four filters --
  status, outcome, tag, gone quiet -- narrows the table from a header, to the rows the
  queryset gives;
* **by keyboard alone**: every one is reached with Tab, opened with Enter and worked with
  the arrow keys, Enter and Space, and the focus stays in the control being used while the
  table is swapped under it. A list is the button built for its select (#301): the arrows
  move in it and Enter chooses, so the table is asked once, for the status meant;
* **with scripts off**: the same headers, and the *Filter* button each ends in;
* **the neighbours' faults are not repeated**: a filter the page came with can be changed
  and cleared from its header (#622), a header put back to *Any* stays open with the focus
  in it (#626), and a search that matches nothing forgets neither the sort nor the status
  (#647);
* **a filter whose column is hidden** is under *Narrow*, at any width, and a header's
  control does not forget it;
* **on a phone** *Narrow* holds the same fields, and fits at 320 pixels in the widest
  languages; the word that opens it says how many filters are in use;
* **what is entered under *Narrow* and not yet applied** outlives a live swap, and with
  scripts off the page says which button applies what;
* **a header** is called by its column's name, shows the whole of the choice its lists
  hold, and keeps its name at the start of the cell;
* **the board** keeps its form, less the status, which its columns fold to (#315), and
  it works as it did, with scripts on and off;
* **axe**, in both themes, with header filters open, with *Narrow* open, and on the board.

Every count of rows is the queryset's, never a number written here.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from .selects import DRAWN, button_of, drawn
from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_column_filters import settled
from .test_navigation_bar import set_language
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 740}
NARROWEST = {"width": 320, "height": 640}

TABLE = "#applications-table"


@pytest.fixture
def search(applicant):
    """Five applications at two companies: two applied, one of them gone quiet, one in
    interviews, one rejected, one a draft; two tags."""
    from django.utils import timezone

    from postulo.applications.models import Application, Status
    from postulo.applications.services import change_status
    from postulo.core.models import Tag
    from postulo.jobs.models import Company, JobPosting

    aperture = Company.objects.create(
        owner=applicant, name="Aperture Science", location="Cambridge"
    )
    mesa = Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")
    remote, dream = Tag.named(applicant, ["Remote", "Dream job"])
    for company, title, status, days_ago, labels in (
        (aperture, "Test Engineer", Status.APPLIED, 40, [remote]),
        (aperture, "Enrichment Associate", Status.APPLIED, 2, [dream]),
        (aperture, "Portal Researcher", Status.INTERVIEWING, 10, [dream]),
        (mesa, "Research Associate", Status.REJECTED, 20, [remote]),
        (mesa, "Resonance Technician", Status.DRAFT, 1, []),
    ):
        posting = JobPosting.objects.create(owner=applicant, company=company, title=title)
        application = Application.objects.create(
            owner=applicant, posting=posting, status=Status.DRAFT
        )
        when = timezone.now() - dt.timedelta(days=days_ago)
        if status != Status.DRAFT:
            change_status(application, Status.APPLIED, occurred_at=when)
        if status not in (Status.DRAFT, Status.APPLIED):
            change_status(application, status, occurred_at=when)
        application.tags.set(labels)
    return {"applicant": applicant}


def asked(person, **wanted) -> list[int]:
    """The applications the queryset gives for these filters, by primary key: what the
    address has always meant, computed here and never read off the page."""
    from postulo.applications import quiet
    from postulo.applications.models import Application

    rows = Application.objects.for_user(person)
    if wanted.get("quiet"):
        rows = rows.quiet(quiet.threshold_for(person))
    if wanted.get("status"):
        rows = rows.filter(status=wanted["status"])
    if wanted.get("tag"):
        rows = rows.filter(tags__slug=wanted["tag"])
    if wanted.get("state") == "open":
        rows = rows.open()
    elif wanted.get("state") == "closed":
        rows = rows.closed()
    if wanted.get("role"):
        rows = rows.filter(posting__title__icontains=wanted["role"])
    if wanted.get("q"):
        rows = rows.filter(posting__company__name__icontains=wanted["q"])
    return sorted(rows.values_list("pk", flat=True).distinct())


def on_the_board(person, **wanted) -> list[int]:
    from postulo.applications.models import BOARD_STATUSES, Application

    live = Application.objects.for_user(person).filter(status__in=list(BOARD_STATUSES))
    return sorted(set(asked(person, **wanted)) & set(live.values_list("pk", flat=True)))


def rows_shown(page: Page) -> list[int]:
    return sorted(
        page.locator(f"{TABLE} tbody tr").evaluate_all(
            "rows => rows.map((row) => Number(row.id.split('-')[1]))"
        )
    )


def cards_shown(page: Page) -> list[int]:
    return sorted(
        page.locator(f"{TABLE} [data-card]").evaluate_all(
            "cards => cards.map((card) => Number(card.dataset.card))"
        )
    )


def shows(page: Page, wanted: list[int]) -> None:
    """The table shows exactly these rows, once whatever is on its way has landed."""
    expect(page.locator(f"{TABLE} tbody tr")).to_have_count(len(wanted))
    assert rows_shown(page) == wanted


def times_in_the_address(page: Page, name: str) -> int:
    return len(re.findall(rf"[?&]{name}=", page.url))


def header(page: Page, column: str):
    return page.locator(f'{TABLE} thead [data-col="{column}"]')


def seen(page: Page, control: str):
    """A header's control as a person sees it: a list at the button built for its select
    where scripts run (#301), and anything else, or a list without scripts, as itself."""
    return drawn(page.locator(control))


def open_header(page: Page, column: str) -> None:
    disclosure = header(page, column).locator("[data-col-filter]")
    if disclosure.get_attribute("open") is None:
        header(page, column).locator("summary").click()
    expect(disclosure).to_have_attribute("open", "")


# ---------------------------------------------------------------- nothing above the table


def test_nothing_stands_between_the_heading_and_the_table(page: Page, live_server, search):
    """No filter form above the table: the form the headers' controls belong to takes no
    room and shows nothing, *Narrow* is a phone's, and the four controls are folded into
    their headers until a header is opened."""
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    shows(page, asked(search["applicant"]))

    form = page.locator("#application-filters")
    assert form.bounding_box()["height"] == 0
    assert (
        form.locator("input, select, button, a").evaluate_all(
            "all => all.filter((el) => el.checkVisibility()).length"
        )
        == 0
    )
    expect(page.locator("[data-narrow-form]")).to_be_hidden()
    for control in ("#filter-status", "#filter-state", "#filter-quiet", "#filter-tag"):
        expect(page.locator(control)).to_have_count(1)
        expect(page.locator(control)).to_be_hidden()

    # The heading's row, and then the table: nothing with a height in between.
    between = page.evaluate(
        """() => {
          const table = document.querySelector('#applications-table');
          const heading = document.querySelector('main h1').closest('.flex.flex-wrap');
          const found = [];
          let el = heading.nextElementSibling;
          for (; el && el !== table; el = el.nextElementSibling) {
            if (el.getBoundingClientRect().height > 0) found.push(el.outerHTML.slice(0, 80));
          }
          return found;
        }"""
    )
    assert between == [], between

    open_header(page, "status")
    for control in ("#filter-status", "#filter-state", "#filter-quiet"):
        expect(seen(page, control)).to_be_visible()
    open_header(page, "role")
    expect(seen(page, "#filter-tag")).to_be_visible()


# --------------------------------------------------------------- each filter, from a header

#: The control, the header it is in, how it is set, and what it then asks for.
FOUR = {
    "status": ("status", "#filter-status", "rejected", {"status": "rejected"}),
    "outcome": ("status", "#filter-state", "open", {"state": "open"}),
    "tag": ("role", "#filter-tag", "remote", {"tag": "remote"}),
    "gone quiet": ("status", "#filter-quiet", True, {"quiet": "1"}),
}


def use(page: Page, control: str, value) -> None:
    """Set a list or a tick box the way a hand would: with the focus in it."""
    box = page.locator(control)
    box.focus()
    if value is True:
        box.check()
    elif value is False:
        box.uncheck()
    else:
        box.select_option(value)


@pytest.mark.parametrize("which", FOUR)
def test_each_filter_narrows_the_table_from_its_header(page: Page, live_server, search, which):
    column, control, value, wanted = FOUR[which]
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    shows(page, asked(person))
    assert 0 < len(asked(person, **wanted)) < len(asked(person)), "a filter that narrows"

    open_header(page, column)
    use(page, control, value)

    shows(page, asked(person, **wanted))
    (name, sent) = next(iter(wanted.items()))
    expect(page).to_have_url(re.compile(rf"[?&]{name}={sent}(&|$)"))
    assert times_in_the_address(page, name) == 1, page.url
    # The header says it is narrowing, stays open, and the focus is where it was.
    label = "Role is filtered" if column == "role" else "Status is filtered"
    expect(page.get_by_role("img", name=label)).to_be_visible()
    expect(header(page, column)).to_have_attribute("aria-label", label)
    expect(seen(page, control)).to_be_visible()
    expect(seen(page, control)).to_be_focused()
    expect(page.locator("#applications-count")).to_contain_text("Clear filters")

    # *Clear filters* is still the way out of all of them.
    page.locator("#applications-count a").click()
    shows(page, asked(person))
    expect(seen(page, control)).to_be_hidden()


def test_the_four_narrow_together_and_with_the_search(page: Page, live_server, search):
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    open_header(page, "status")
    open_header(page, "role")

    use(page, "#filter-status", "applied")
    shows(page, asked(person, status="applied"))
    settled(page)
    use(page, "#filter-tag", "remote")
    shows(page, asked(person, status="applied", tag="remote"))
    settled(page)
    use(page, "#filter-quiet", True)
    shows(page, asked(person, status="applied", tag="remote", quiet="1"))
    settled(page)
    use(page, "#filter-tag", "")
    shows(page, asked(person, status="applied", quiet="1"))
    settled(page)
    use(page, "#filter-quiet", False)
    shows(page, asked(person, status="applied"))

    page.locator("#site-search").fill("aperture")
    shows(page, asked(person, status="applied", q="aperture"))
    expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    expect(page.locator("#filter-status")).to_have_value("applied")

    page.locator("#filter-role").fill("engineer")
    shows(page, asked(person, status="applied", q="aperture", role="engineer"))
    expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))


# ------------------------------------------------------------------------ by keyboard


def tab_to(page: Page, selector: str, key: str = "Tab", limit: int = 80) -> None:
    """Press Tab until the focus is on what the selector names: reached, not placed."""
    for _ in range(limit):
        page.keyboard.press(key)
        if page.evaluate("(s) => document.activeElement?.matches(s) ?? false", selector):
            return
    where = page.evaluate("() => document.activeElement?.outerHTML.slice(0, 120)")
    raise AssertionError(f"{key} never reached {selector}; the focus is on {where}")


def test_every_header_filter_is_reached_and_worked_without_a_mouse(page: Page, live_server, search):
    """Tab reaches the column's name, Enter opens its filter, Tab goes into it, the arrow
    keys move down its list, Enter chooses and Space ticks -- and each time the table is
    swapped under the control the focus is still in it, so the next key is the next thing
    done. Passing over a status on the way asks the table for nothing (#301): a native
    list sent a request for every status an arrow key went past."""
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    shows(page, asked(person))

    asked_for = []
    page.on(
        "request",
        lambda request: asked_for.append(request.url) if "/applications/?" in request.url else None,
    )

    tab_to(page, '[data-col="status"] summary')
    expect(seen(page, "#filter-status")).to_be_hidden()
    page.keyboard.press("Enter")
    expect(seen(page, "#filter-status")).to_be_visible()

    page.keyboard.press("Tab")
    status = page.locator("#filter-status")
    expect(button_of(status)).to_be_focused()
    page.keyboard.press("ArrowDown")  # opens the list, on Any
    page.keyboard.press("ArrowDown")  # Any -> Draft
    expect(button_of(status)).to_have_attribute("aria-expanded", "true")
    expect(status).to_have_value(""), "nothing is chosen by passing over it"
    page.keyboard.press("Enter")
    expect(status).to_have_value("draft")
    shows(page, asked(person, status="draft"))
    expect(button_of(status)).to_be_focused()
    expect(button_of(status)).to_have_text("Draft")
    settled(page)
    page.keyboard.press("ArrowDown")  # opens it again, on Draft
    page.keyboard.press("ArrowDown")  # Draft -> Applied
    page.keyboard.press("ArrowDown")  # Applied -> Acknowledged
    page.keyboard.press("ArrowUp")  # and back: two statuses passed over
    page.keyboard.press("Enter")
    expect(status).to_have_value("applied")
    shows(page, asked(person, status="applied"))
    expect(button_of(status)).to_be_focused()
    settled(page)
    assert len(asked_for) == 2, f"one request for each status chosen, and no more: {asked_for}"

    page.keyboard.press("Tab")
    outcome = page.locator("#filter-state")
    expect(button_of(outcome)).to_be_focused()
    page.keyboard.press("ArrowDown")  # opens the list, on Any
    page.keyboard.press("ArrowDown")  # Any -> Still live
    page.keyboard.press("Enter")
    expect(outcome).to_have_value("open")
    shows(page, asked(person, status="applied", state="open"))
    expect(button_of(outcome)).to_be_focused()
    settled(page)

    page.keyboard.press("Tab")
    gone_quiet = page.locator("#filter-quiet")
    expect(gone_quiet).to_be_focused()
    page.keyboard.press("Space")
    expect(gone_quiet).to_be_checked()
    shows(page, asked(person, status="applied", state="open", quiet="1"))
    expect(gone_quiet).to_be_focused()
    settled(page)
    page.keyboard.press("Space")
    expect(gone_quiet).not_to_be_checked()
    shows(page, asked(person, status="applied", state="open"))
    expect(gone_quiet).to_be_focused()
    settled(page)

    # Back up the row to the role, whose header holds the tag.
    tab_to(page, '[data-col="role"] summary', key="Shift+Tab")
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    expect(page.locator("#filter-role")).to_be_focused()
    page.keyboard.press("Tab")
    tag = page.locator("#filter-tag")
    expect(button_of(tag)).to_be_focused()
    page.keyboard.press("d")  # opens the list on the first tag that starts with it
    page.keyboard.press("Enter")
    expect(tag).to_have_value("dream-job")
    shows(page, asked(person, status="applied", state="open", tag="dream-job"))
    expect(button_of(tag)).to_be_focused()

    # And the header folds again from its name, with nothing lost.
    settled(page)
    tab_to(page, '[data-col="role"] summary', key="Shift+Tab")
    page.keyboard.press("Enter")
    expect(tag).to_be_hidden()
    expect(page).to_have_url(re.compile(r"[?&]tag=dream-job(&|$)"))


def test_a_control_a_swap_brought_in_answers_at_once(page: Page, live_server, search):
    """htmx wires what it swapped in twenty milliseconds after it lands, and for that long a
    list that was replaced has the focus and hears nothing: a key held down in it could end
    on a status the table was never asked for. The page wires what a swap brought in as it
    lands. Here the list is changed again in the turn after the swap, which no hand can do
    and which is the only way to be inside that window every time."""
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    open_header(page, "status")
    page.evaluate(
        """() => document.addEventListener('htmx:afterSwap', (event) => {
          if (event.target.id !== 'applications-table') return;
          setTimeout(() => {
            const list = document.querySelector('#filter-status');
            if (list.value !== 'applied') return;
            list.value = 'rejected';
            list.dispatchEvent(new Event('change', {bubbles: true}));
          }, 0);
        })"""
    )

    use(page, "#filter-status", "applied")

    expect(page).to_have_url(re.compile(r"[?&]status=rejected(&|$)"))
    shows(page, asked(person, status="rejected"))
    expect(page.locator("#filter-status")).to_have_value("rejected")


# -------------------------------------------------------------------- with scripts off


def test_with_scripts_off_each_header_has_the_button_that_applies_it(
    browser: Browser, live_server, search
):
    """The same headers, opened by the browser alone; the controls are real controls of the
    page's form; and each header ends in a *Filter* button, which is the only way to send
    them and is not there at all when a script is running."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/")
        assert rows_shown(page) == asked(person)
        expect(page.locator("[data-narrow-form]")).to_be_hidden()

        expect(page.locator("#filter-status")).to_be_hidden()
        header(page, "status").locator("summary").click()
        page.locator("#filter-status").select_option("applied")
        page.locator("#filter-state").select_option("open")
        assert rows_shown(page) == asked(person), "nothing is sent until the button is pressed"
        header(page, "status").get_by_role("button", name="Filter", exact=True).click()
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]state=open(&|$)"))
        assert rows_shown(page) == asked(person, status="applied", state="open")
        expect(page.get_by_role("img", name="Status is filtered")).to_be_visible()
        expect(page.locator("#filter-status")).to_have_value("applied")

        # Gone quiet, from the same header; the status rides along.
        page.locator("#filter-quiet").check()
        header(page, "status").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="applied", state="open", quiet="1")
        expect(page.locator("#filter-quiet")).to_be_checked()
        page.locator("#filter-quiet").uncheck()
        page.locator("#filter-state").select_option("")
        header(page, "status").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="applied")

        # The tag, from the role's header and its own button: any of them sends the form.
        header(page, "role").locator("summary").click()
        page.locator("#filter-tag").select_option("dream-job")
        header(page, "role").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="applied", tag="dream-job")
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        expect(page.get_by_role("img", name="Role is filtered")).to_be_visible()

        # Enter in a header's box sends the form too, with everything else in it.
        page.locator("#filter-tag").select_option("")
        page.locator("#filter-role").fill("engineer")
        page.locator("#filter-role").press("Enter")
        expect(page).to_have_url(re.compile(r"[?&]role=engineer(&|$)"))
        assert rows_shown(page) == asked(person, status="applied", role="engineer")
        for name in ("status", "state", "tag", "role"):
            assert times_in_the_address(page, name) == 1, (name, page.url)
    finally:
        context.close()


def test_with_scripts_off_the_keyboard_alone_reaches_the_button(
    browser: Browser, live_server, search
):
    """Tab to the column's name, Enter, Tab into the list, an arrow, and Tab on to the
    button under it: the next things the keyboard reaches, in the order they are drawn."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/")
        tab_to(page, '[data-col="status"] summary')
        page.keyboard.press("Enter")
        page.keyboard.press("Tab")
        expect(page.locator("#filter-status")).to_be_focused()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        expect(page.locator("#filter-status")).to_have_value("applied")
        page.keyboard.press("Tab")
        expect(page.locator("#filter-state")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("#filter-quiet")).to_be_focused()
        page.keyboard.press("Space")
        page.keyboard.press("Tab")
        button = header(page, "status").get_by_role("button", name="Filter", exact=True)
        expect(button).to_be_focused()
        page.keyboard.press("Enter")

        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]quiet=1(&|$)"))
        assert rows_shown(page) == asked(person, status="applied", quiet="1")
    finally:
        context.close()


# --------------------------------------------- the neighbours' faults, for these four


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_filters_the_page_came_with_can_be_changed_and_cleared_from_their_headers(
    browser: Browser, live_server, search, scripts
):
    """#622. A saved view, a bookmark, a reload, Back: the page arrives with filters in its
    address. Each of the four is then changed or cleared from its header, one at a time,
    and after each the table is what the queryset gives for what the headers now say."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=WIDE)
    page = context.new_page()

    def then(column: str, **wanted) -> None:
        if not scripts:
            header(page, column).get_by_role("button", name="Filter", exact=True).click()
        shows(page, asked(person, **wanted))
        for name in ("status", "state", "tag", "quiet"):
            assert times_in_the_address(page, name) <= 1, (name, page.url)
        if scripts:
            settled(page)

    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&state=open&tag=remote&quiet=1")
        shows(page, asked(person, status="applied", state="open", tag="remote", quiet="1"))
        expect(page.locator("#filter-status")).to_have_value("applied")
        expect(page.locator("#filter-state")).to_have_value("open")
        expect(page.locator("#filter-tag")).to_have_value("remote")
        expect(page.locator("#filter-quiet")).to_be_checked()

        use(page, "#filter-quiet", False)
        then("status", status="applied", state="open", tag="remote")
        assert "quiet=" not in page.url

        use(page, "#filter-tag", "")
        then("role", status="applied", state="open")

        use(page, "#filter-status", "rejected")
        then("status", status="rejected", state="open")
        expect(page.locator(TABLE)).to_contain_text("Nothing matches these filters")

        use(page, "#filter-state", "closed")
        then("status", status="rejected", state="closed")

        use(page, "#filter-status", "")
        then("status", state="closed")

        use(page, "#filter-state", "")
        then("status")
        expect(page.locator("#applications-count")).not_to_contain_text("Clear filters")
    finally:
        context.close()


@pytest.mark.parametrize("which", FOUR)
def test_a_filter_put_back_to_any_leaves_its_header_open_with_the_focus_in_it(
    page: Page, live_server, search, which
):
    """#626. Choosing *Any*, or unticking the box, leaves a header that narrows nothing. It
    is still the header being used: it stays open and the control keeps the focus."""
    column, control, value, wanted = FOUR[which]
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/")
    open_header(page, column)
    use(page, control, value)
    shows(page, asked(person, **wanted))
    settled(page)

    use(page, control, False if value is True else "")

    shows(page, asked(person))
    disclosure = header(page, column).locator("[data-col-filter]")
    expect(disclosure).to_have_attribute("open", "")
    expect(seen(page, control)).to_be_visible()
    expect(seen(page, control)).to_be_focused()
    expect(header(page, column).locator("[aria-label$='is filtered']")).to_have_count(0)
    expect(header(page, column)).not_to_have_attribute("aria-label", re.compile("is filtered"))
    # And it answers again from where it is.
    settled(page)
    use(page, control, value)
    shows(page, asked(person, **wanted))
    expect(seen(page, control)).to_be_focused()


def test_a_search_that_matches_nothing_forgets_neither_the_sort_nor_the_filters(
    page: Page, live_server, search
):
    """#647. The header went with the rows, and the four are in the header now: a search
    for something that is not there would have been followed by one sent with no status,
    no tag and no sort. The header stays, over no rows, and they stay with it."""
    person = search["applicant"]
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/applications/?sort=role&status=applied&state=open")
    shows(page, asked(person, status="applied", state="open"))
    in_order = page.locator(f"{TABLE} tbody tr td a.font-medium").all_inner_texts()
    assert in_order == sorted(in_order) and len(in_order) == 2

    box = page.locator("#site-search")
    box.fill("zzzz")
    expect(page.locator(TABLE)).to_contain_text("Nothing matches these filters")
    shows(page, [])
    expect(seen(page, "#filter-status")).to_be_visible()
    expect(seen(page, "#filter-status")).to_have_text("Applied")
    expect(page.locator("#filter-status")).to_have_value("applied")
    expect(page.locator("#filter-state")).to_have_value("open")
    expect(page.locator("#sort-role")).to_be_visible()

    box.fill("")
    shows(page, asked(person, status="applied", state="open"))
    assert page.locator(f"{TABLE} tbody tr td a.font-medium").all_inner_texts() == in_order
    for kept in ("sort=role", "status=applied", "state=open"):
        expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))

    # A filter that leaves nothing can be loosened where it is.
    use(page, "#filter-status", "rejected")
    expect(page.locator(TABLE)).to_contain_text("Nothing matches these filters")
    expect(seen(page, "#filter-status")).to_be_focused()
    settled(page)
    use(page, "#filter-state", "")
    shows(page, asked(person, status="rejected"))
    expect(page).to_have_url(re.compile(r"[?&]sort=role(&|$)"))


def test_with_scripts_off_an_empty_answer_keeps_the_sort_and_the_filters(
    browser: Browser, live_server, search
):
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?sort=role&status=rejected&tag=dream-job")
        expect(page.locator(TABLE)).to_contain_text("Nothing matches these filters")
        assert rows_shown(page) == []
        expect(page.locator("#filter-status")).to_have_value("rejected")
        expect(page.locator("#filter-tag")).to_have_value("dream-job")

        page.locator("#filter-tag").select_option("")
        header(page, "role").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="rejected")
        expect(page).to_have_url(re.compile(r"[?&]sort=role(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]status=rejected(&|$)"))

        # The masthead's box over an empty answer keeps them as well.
        page.goto(f"{base}/applications/?sort=role&status=applied&q=zzzz")
        assert rows_shown(page) == []
        page.locator("#site-search").fill("aperture")
        page.locator("#site-search").press("Enter")
        assert rows_shown(page) == asked(person, status="applied", q="aperture")
        expect(page).to_have_url(re.compile(r"[?&]sort=role(&|$)"))
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
    finally:
        context.close()


# --------------------------------------------------- a filter whose column is hidden


def hide_the_status_column(person) -> None:
    from postulo.core import tables

    tables.save_settings(person, "applications", {"columns": ["role", "company", "applied"]})


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_with_the_status_column_hidden_its_filters_are_under_narrow_at_any_width(
    browser: Browser, live_server, search, scripts
):
    """A column that is hidden still has to be filterable. Its filters are under *Narrow*,
    which is then on the page at desktop width too, holding those alone; one in use brings
    it back open, where it can be seen and cleared; and a header's control, which sends
    another form, does not forget it."""
    person = search["applicant"]
    hide_the_status_column(person)
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/")
        assert rows_shown(page) == asked(person)
        narrow = page.locator("[data-narrow-form]")
        expect(narrow.locator("summary")).to_be_visible()
        expect(page.locator("#filter-status-narrow")).to_be_hidden()

        narrow.locator("summary").click()
        for field in ("#filter-status-narrow", "#filter-state-narrow", "#filter-quiet-narrow"):
            expect(seen(page, field)).to_be_visible()
        # The fields the headers hold are a phone's, and are not drawn here.
        expect(seen(page, "#filter-role-narrow")).to_be_hidden()
        expect(seen(page, "#filter-tag-narrow")).to_be_hidden()
        # Each is one control by its label's name: a list is a combobox either way, the
        # native select without scripts and the button built for it with them.
        expect(narrow.get_by_role("combobox", name="Status", exact=True)).to_be_visible()
        expect(narrow.get_by_role("combobox", name="Outcome", exact=True)).to_be_visible()
        expect(narrow.get_by_role("checkbox", name="Gone quiet", exact=True)).to_be_visible()

        page.locator("#filter-status-narrow").select_option("applied")
        narrow.get_by_role("button", name="Filter", exact=True).click()
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        assert rows_shown(page) == asked(person, status="applied")
        assert times_in_the_address(page, "status") == 1, page.url

        # In use, so open, and saying what it narrows by.
        expect(seen(page, "#filter-status-narrow")).to_be_visible()
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")
        expect(narrow.get_by_role("combobox", name="Status", exact=True)).to_contain_text("Applied")

        # A header's control keeps it.
        header(page, "role").locator("summary").click()
        page.locator("#filter-role").fill("engineer")
        if not scripts:
            header(page, "role").get_by_role("button", name="Filter", exact=True).click()
        shows(page, asked(person, status="applied", role="engineer"))
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")

        # And it is cleared where it is.
        page.locator("#filter-status-narrow").select_option("")
        narrow.get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, role="engineer")
        expect(page).to_have_url(re.compile(r"[?&]role=engineer(&|$)"))
        assert "status=applied" not in page.url
        expect(seen(page, "#filter-status-narrow")).to_be_hidden()
    finally:
        context.close()


# ---------------------------------------------------------------------------- a phone


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_on_a_phone_narrow_holds_the_same_fields(browser: Browser, live_server, search, scripts):
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&sort=role")
        assert rows_shown(page) == asked(person, status="applied")
        narrow = page.locator("[data-narrow-form]")
        expect(page.locator("#filter-status-narrow")).to_be_hidden()
        narrow.locator("summary").click()

        for field in ("role", "tag", "company", "location", "status", "state", "quiet"):
            expect(page.locator(f"#filter-{field}-narrow")).to_be_visible()
        expect(page.locator("#filter-applied-narrow-from")).to_be_visible()
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")

        # It changes a filter the page came with, and the header's copy does not override it.
        page.locator("#filter-status-narrow").select_option("")
        page.locator("#filter-tag-narrow").select_option("remote")
        page.locator("#filter-quiet-narrow").check()
        narrow.get_by_role("button", name="Filter", exact=True).click()

        expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
        assert rows_shown(page) == asked(person, tag="remote", quiet="1")
        expect(page).to_have_url(re.compile(r"[?&]sort=role(&|$)"))
        assert "status=applied" not in page.url
        for name in ("status", "tag", "quiet", "sort"):
            assert times_in_the_address(page, name) <= 1, (name, page.url)
        # Both copies came back agreeing.
        expect(page.locator("#filter-tag")).to_have_value("remote")
        expect(page.locator("#filter-quiet")).to_be_checked()
        expect(page.locator("#filter-status")).to_have_value("")
    finally:
        context.close()


@pytest.mark.parametrize("language", ["en", "el", "de"])
def test_narrow_and_an_open_header_fit_at_320_pixels(page: Page, live_server, search, language):
    """Reflow (SC 1.4.10). With *Narrow* open and every field in it, the page scrolls down
    and not across, and every field is inside the screen and a thumb's size; and a header
    opened in the table makes the table scroll inside its own box, as a table may."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(NARROWEST)
    page.goto(f"{base}/applications/?status=applied&tag=remote")
    page.locator("[data-narrow] > summary").click()
    expect(seen(page, "#filter-status-narrow")).to_be_visible()

    result = page.evaluate(SCROLLS_SIDEWAYS)
    assert not result["reached"], f"{language}: {result}"
    # What a person sees: a list is the button built for its select, which is a button.
    controls = page.locator(
        "[data-narrow-form] :is(input:not([type=hidden]), select:not([data-select-ready]),"
        " button, summary)"
    )
    assert controls.count() >= 10
    for control in controls.all():
        where = control.bounding_box()
        assert where["x"] >= 0 and where["x"] + where["width"] <= NARROWEST["width"], (
            language,
            control.evaluate("el => el.outerHTML.slice(0, 80)"),
            where,
        )
    for label in page.locator("[data-narrow-form] label.tap-target").all():
        assert label.bounding_box()["height"] >= 24

    # The headers the address opened are in the table's own scroll box.
    for control in ("#filter-status", "#filter-state", "#filter-quiet", "#filter-tag"):
        expect(page.locator(control)).to_be_attached()
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


# ------------------------------------- what is entered under Narrow and not yet applied


def test_on_a_phone_what_is_set_under_narrow_outlives_a_search(
    browser: Browser, live_server, search
):
    """A role typed, a tag chosen and a box ticked under *Narrow*, and then a word typed in
    the masthead's box. The search swaps the table, and the block is drawn with the table:
    it came back folded and holding none of the three. They are put back in the block that
    replaces it, still open and still unsent, and its own button then sends them with the
    search, the status the page came with and the sort."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&sort=role")
        shows(page, asked(person, status="applied"))
        page.locator("[data-narrow] > summary").click()
        page.locator("#filter-role-narrow").fill("eng")
        page.locator("#filter-tag-narrow").select_option("remote")
        page.locator("#filter-quiet-narrow").check()

        # The address is pushed in the turn the table is swapped in.
        page.locator("[data-search-link]").click()
        page.locator("#site-search").fill("a")
        expect(page).to_have_url(re.compile(r"[?&]q=a(&|$)"))
        shows(page, asked(person, status="applied", q="a"))
        for unsent in ("role=eng", "tag=remote", "quiet=1"):
            assert unsent not in page.url, page.url

        expect(page.locator("[data-narrow]")).to_have_attribute("open", "")
        expect(page.locator("#filter-role-narrow")).to_have_value("eng")
        expect(page.locator("#filter-tag-narrow")).to_have_value("remote")
        expect(seen(page, "#filter-tag-narrow")).to_have_text("Remote")
        expect(page.locator("#filter-quiet-narrow")).to_be_checked()
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")

        page.keyboard.press("Escape")
        page.locator("[data-narrow-form]").get_by_role("button", name="Filter", exact=True).click()
        wanted = {"status": "applied", "q": "a", "role": "eng", "tag": "remote", "quiet": "1"}
        assert rows_shown(page) == asked(person, **wanted)
        for name, value in {**wanted, "sort": "role"}.items():
            expect(page).to_have_url(re.compile(rf"[?&]{name}={value}(&|$)"))
            assert times_in_the_address(page, name) == 1, (name, page.url)
    finally:
        context.close()


def test_with_the_status_column_hidden_what_is_set_under_narrow_outlives_a_headers_swap(
    page: Page, live_server, search
):
    """At desktop width, with the status column off, its filters are under *Narrow*. A
    status chosen there and not yet applied was thrown away by the first header to narrow
    the table, and the block folded shut over *Any*. It is kept, in a block still open; the
    header's request does not send it; a second swap keeps it still; and *Filter* under it
    then sends it with what the headers asked."""
    person = search["applicant"]
    hide_the_status_column(person)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/")
    page.locator("[data-narrow] > summary").click()
    status = page.locator("#filter-status-narrow")
    status.select_option("applied")
    page.locator("#filter-quiet-narrow").check()

    open_header(page, "role")
    page.locator("#filter-role").fill("eng")
    expect(page).to_have_url(re.compile(r"[?&]role=eng(&|$)"))
    shows(page, asked(person, role="eng"))
    assert "status=applied" not in page.url and "quiet=1" not in page.url, page.url

    expect(page.locator("[data-narrow]")).to_have_attribute("open", "")
    expect(status).to_have_value("applied")
    expect(button_of(status)).to_have_text("Applied"), "and its button shows it"
    expect(page.locator("#filter-quiet-narrow")).to_be_checked()
    expect(page.locator("#filter-state-narrow")).to_have_value("")
    expect(page.locator("#filter-role")).to_be_focused()
    # With a script nothing needs saying about which button applies what, and a header
    # has no button: htmx parses what it swaps in as a document with no script would, so
    # what a `<noscript>` holds is in the page again after a swap, and is not drawn.
    expect(page.locator("[data-narrow-reach]")).to_be_hidden()
    expect(header(page, "role").get_by_role("button", name="Filter", exact=True)).to_have_count(0)

    page.locator("#sort-company").click()
    expect(page).to_have_url(re.compile(r"[?&]sort=company(&|$)"))
    expect(page.locator("[data-narrow]")).to_have_attribute("open", "")
    expect(status).to_have_value("applied")
    expect(page.locator("#filter-quiet-narrow")).to_be_checked()

    page.locator("[data-narrow-form]").get_by_role("button", name="Filter", exact=True).click()
    wanted = {"role": "eng", "status": "applied", "quiet": "1"}
    assert rows_shown(page) == asked(person, **wanted)
    for name, value in {**wanted, "sort": "company"}.items():
        expect(page).to_have_url(re.compile(rf"[?&]{name}={value}(&|$)"))
        assert times_in_the_address(page, name) == 1, (name, page.url)


def test_with_scripts_off_each_button_sends_its_own_fields_and_the_page_says_so(
    browser: Browser, live_server, search
):
    """Without a script nothing is swapped and nothing is live: there are two forms, the
    headers' and *Narrow*'s, and a button sends the form it belongs to. Each carries what
    the address holds for the other's filters, so applying one never drops the other; what
    was changed in the other and not yet applied cannot be sent, a control belonging to one
    form. The page says which button applies what, under the one in *Narrow*."""
    person = search["applicant"]
    hide_the_status_column(person)
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&role=e&sort=role")
        assert rows_shown(page) == asked(person, status="applied", role="e")
        narrow = page.locator("[data-narrow-form]")
        button = narrow.get_by_role("button", name="Filter", exact=True)
        note = page.locator("[data-narrow-reach]")
        expect(note).to_be_visible()
        expect(note).to_contain_text("This button applies the filters above it.")
        expect(note).to_contain_text("have a button of their own")
        below = button.bounding_box()
        assert note.bounding_box()["y"] >= below["y"] + below["height"], "under its button"

        # A header's button: its own box, and the status the address holds, not the one
        # chosen under Narrow and left unapplied.
        page.locator("#filter-status-narrow").select_option("rejected")
        page.locator("#filter-role").fill("eng")
        header(page, "role").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="applied", role="eng")
        for kept in ("status=applied", "role=eng", "sort=role"):
            expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")

        # Narrow's button: its own fields, and the role the address holds, not the one
        # typed in the header and left unapplied.
        page.locator("#filter-role").fill("zzzz")
        page.locator("#filter-status-narrow").select_option("")
        page.locator("#filter-state-narrow").select_option("open")
        button.click()
        assert rows_shown(page) == asked(person, state="open", role="eng")
        for kept in ("state=open", "role=eng", "sort=role"):
            expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))
        assert "status=applied" not in page.url and "zzzz" not in page.url, page.url
        for name in ("status", "state", "role", "sort"):
            assert times_in_the_address(page, name) <= 1, (name, page.url)
        expect(page.locator("#filter-role")).to_have_value("eng")
    finally:
        context.close()


def test_with_scripts_off_on_a_phone_the_search_and_narrow_keep_each_others_question(
    browser: Browser, live_server, search
):
    """The masthead's box is a third form, and without a script it loads the page: it
    carries the filters the address holds, and *Narrow* carries the search it holds, so a
    search and a filter still narrow together. The note under *Narrow*'s button fits a
    phone's screen."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&sort=role")
        page.locator("[data-search-link]").click()
        page.locator("#site-search").fill("aperture")
        page.locator("#site-search").press("Enter")
        expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
        assert rows_shown(page) == asked(person, status="applied", q="aperture")

        page.locator("[data-narrow] > summary").click()
        note = page.locator("[data-narrow-reach]")
        expect(note).to_be_visible()
        where = note.bounding_box()
        assert where["x"] >= 0 and where["x"] + where["width"] <= PHONE["width"], where
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]

        page.locator("#filter-tag-narrow").select_option("remote")
        page.locator("[data-narrow-form]").get_by_role("button", name="Filter", exact=True).click()
        assert rows_shown(page) == asked(person, status="applied", q="aperture", tag="remote")
        for kept in ("q=aperture", "status=applied", "tag=remote", "sort=role"):
            expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))
    finally:
        context.close()


# ------------------------------------------------------- the word that opens Narrow

#: What an eye reads on the word that opens *Narrow*: the word, and whichever of its marks
#: is drawn at this width, less the words that are there for a screen reader.
SEEN_ON = r"""(summary) => {
  const drawn = [...summary.querySelectorAll('[data-narrow-count]')].filter((mark) =>
    mark.checkVisibility());
  const word = [...summary.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE);
  const figures = drawn.flatMap((mark) =>
    [...mark.children].filter((part) => !part.matches('.sr-only, svg')));
  return {
    text: [...word, ...figures].map((part) => part.textContent).join(' ')
      .replace(/\s+/g, ' ').trim(),
    counts: drawn.map((mark) => mark.dataset.narrowCount),
    funnels: drawn.map((mark) => mark.querySelectorAll('svg[data-icon="filter"]').length),
    inside: drawn.every((mark) => {
      const box = mark.getBoundingClientRect();
      return box.width > 0 && box.left >= 0 && box.right <= document.documentElement.clientWidth;
    }),
  };
}"""


def test_on_a_phone_narrow_says_how_many_filters_are_in_use_and_stays_folded(
    browser: Browser, live_server, search
):
    """A status and a tag in force, on a phone: the headers that say so are a sideways
    scroll away inside the table, and *Narrow* is folded. The word carries the mark a
    narrowed column carries and how many of its fields are in force, in a figure for the
    eye and in words for a screen reader, and stays folded: opening it is one press."""
    base = live_server.url
    context = browser.new_context(viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?status=applied&tag=remote")
        block = page.locator("[data-narrow]")
        summary = block.locator("> summary")
        expect(summary).to_be_visible()
        assert block.get_attribute("open") is None, "folded"
        expect(page.locator("#filter-status-narrow")).to_be_hidden()

        seen = summary.evaluate(SEEN_ON)
        assert seen == {"text": "Narrow · 2", "counts": ["2"], "funnels": [1], "inside": True}
        expect(summary).to_have_accessible_name("Narrow 2 filters in use")
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]

        # One press away, and holding what the count counted.
        summary.click()
        expect(page.locator("#filter-status-narrow")).to_have_value("applied")
        expect(page.locator("#filter-tag-narrow")).to_have_value("remote")

        page.goto(f"{base}/applications/?tag=remote&sort=role&q=a")
        assert summary.evaluate(SEEN_ON)["text"] == "Narrow · 1"
        expect(summary).to_have_accessible_name("Narrow 1 filter in use")

        page.goto(f"{base}/applications/?sort=role")
        assert summary.evaluate(SEEN_ON) == {
            "text": "Narrow",
            "counts": [],
            "funnels": [],
            "inside": True,
        }
        expect(summary).to_have_accessible_name("Narrow")

        # The same block on the other tables.
        page.goto(f"{base}/jobs/companies/?name=a&location=e")
        assert summary.evaluate(SEEN_ON)["text"] == "Narrow · 2"
        expect(summary).to_have_accessible_name("Narrow 2 filters in use")
    finally:
        context.close()


def test_the_count_on_narrow_is_of_the_fields_it_shows_at_that_width(
    page: Page, live_server, search
):
    """With the status column hidden the block is on the page at every width, and from
    `md` up it holds only the filters no header holds: a status and a role in force are
    two of its fields on a phone and one at a desk, where the role is in its header and
    marked there."""
    hide_the_status_column(search["applicant"])
    base = live_server.url
    sign_in(page, base)
    summary = page.locator("[data-narrow] > summary")

    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/?status=applied&role=e")
    assert summary.evaluate(SEEN_ON)["text"] == "Narrow · 1"
    expect(summary).to_have_accessible_name("Narrow 1 filter in use")
    expect(page.get_by_role("img", name="Role is filtered")).to_be_visible()

    page.set_viewport_size(PHONE)
    assert summary.evaluate(SEEN_ON)["text"] == "Narrow · 2"
    expect(summary).to_have_accessible_name("Narrow 2 filters in use")

    # Only a header's filter in force: nothing under the word at a desk, one on a phone.
    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/?role=e")
    assert summary.evaluate(SEEN_ON)["text"] == "Narrow"
    expect(summary).to_have_accessible_name("Narrow")
    page.set_viewport_size(PHONE)
    assert summary.evaluate(SEEN_ON)["text"] == "Narrow · 1"


# ------------------------------------------------ what a header is called, and its lists

EVERY_HEADER_OPEN = (
    "/applications/?status=acknowledged&state=open&quiet=1&tag=remote"
    "&role=e&company=a&location=l&applied_from=2020-01-01"
)

#: Every list in a header that cannot show the whole of one of its choices: measured by
#: the browser, in whatever font it is drawing in. The list is the button built for the
#: select (#301), and each of its options is written out beside the words it shows and
#: measured against the room those words have.
LISTS_CUT_SHORT = """() => {
  const short = [];
  for (const select of document.querySelectorAll('#applications-table thead select')) {
    const list = __DRAWN__(select);
    if (!list.checkVisibility()) continue;
    const words = list.querySelector('[data-select-text]');
    const probe = document.createElement('span');
    probe.style.cssText = 'position: absolute; visibility: hidden; white-space: nowrap;';
    words.appendChild(probe);
    const has = words.getBoundingClientRect().width;
    for (const option of select.options) {
      probe.textContent = option.text;
      const wants = probe.getBoundingClientRect().width;
      if (has < wants - 0.5) {
        short.push(
          `${select.id} has ${Math.round(has)} for "${option.text}", wanting ${Math.round(wants)}`);
      }
    }
    probe.remove();
  }
  return short;
}""".replace("__DRAWN__", DRAWN)


@pytest.mark.parametrize("language", ["de", "fr-fr"])
def test_a_list_in_a_header_is_never_narrower_than_its_widest_choice(
    page: Page, live_server, search, language
):
    """A list shows the choice it holds, and a header's was as wide as a squeezed column
    let it be: with every header open at 1024 pixels the status list had room for about
    half of "Zur Kenntnis genommen", so a filter in force could not be read. A list in a
    header holds its column open instead, and the table scrolls inside its own box."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    for width in (768, 1024):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{base}{EVERY_HEADER_OPEN}")
        for control in ("#filter-status", "#filter-state", "#filter-tag", "#filter-applied-from"):
            expect(seen(page, control)).to_be_visible()
        assert page.evaluate(LISTS_CUT_SHORT) == [], (language, width)
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], (language, width)


def test_a_header_cell_is_announced_by_its_columns_name(page: Page, live_server, search):
    """A header cell with no name of its own is called by everything in it, and its name is
    what every cell under it is announced with. Open, the status header holds two lists, a
    tick box and what they are set to, and all of it was the column's name. The cell says
    its own -- the column's, and that it is filtered while it is -- and every control in it
    is still there under the name it had."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)

    def column_names() -> list[str]:
        session = page.context.new_cdp_session(page)
        nodes = session.send("Accessibility.getFullAXTree")["nodes"]
        session.detach()
        return [
            node.get("name", {}).get("value", "")
            for node in nodes
            if node.get("role", {}).get("value") == "columnheader"
        ]

    page.goto(f"{base}/applications/")
    assert column_names() == ["Selected", "Role", "Company", "Location", "Status", "Applied"]

    page.goto(f"{base}/applications/?status=applied&state=open&quiet=1&tag=remote&sort=role")
    open_header(page, "company")
    assert column_names() == [
        "Selected",
        "Role is filtered",
        "Company",
        "Location",
        "Status is filtered",
        "Applied",
    ]
    expect(header(page, "role")).to_have_attribute("aria-sort", "ascending")

    status = header(page, "status")
    expect(status.get_by_role("combobox", name="Filter by status", exact=True)).to_be_visible()
    expect(status.get_by_role("combobox", name="Outcome", exact=True)).to_be_visible()
    expect(status.get_by_role("checkbox", name="Gone quiet", exact=True)).to_be_visible()
    expect(status.get_by_role("img", name="Status is filtered", exact=True)).to_be_visible()
    expect(
        status.get_by_role("link", name="Sort by status, lowest first", exact=True)
    ).to_be_visible()
    expect(status.get_by_role("separator", name="Width of Status", exact=True)).to_be_attached()
    role = header(page, "role")
    expect(role.get_by_role("searchbox", name="Filter by role", exact=True)).to_be_visible()
    expect(role.get_by_role("combobox", name="Tag", exact=True)).to_be_visible()
    company = header(page, "company")
    expect(company.get_by_role("searchbox", name="Filter by company", exact=True)).to_be_visible()


START_OF_THE_NAME = """(cell) => {
  const name = cell.querySelector('summary');
  const panel = cell.querySelector('details');
  const style = getComputedStyle(cell);
  const [c, n, p] = [cell, name, panel].map((el) => el.getBoundingClientRect());
  const rtl = style.direction === 'rtl';
  const edge = rtl
    ? c.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth)
    : c.left + parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth);
  return {
    gap: Math.round(Math.abs((rtl ? n.right : n.left) - edge)),
    spare: Math.round(p.width - n.width),
    rtl,
  };
}"""


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_columns_name_stays_at_the_start_of_its_cell_when_its_filter_opens(
    page: Page, live_server, search, language
):
    """A browser centres a header cell unless told otherwise, and nobody saw it while a
    name was all a cell held. Open, the status header is as wide as its lists, and the
    name moved to the middle of it. It stays against the cell's starting edge, which is
    the right-hand one in Arabic."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/")
    cell = header(page, "status")

    closed = cell.evaluate(START_OF_THE_NAME)
    assert closed["gap"] <= 1 and closed["rtl"] == (language == "ar"), closed

    open_header(page, "status")
    opened = cell.evaluate(START_OF_THE_NAME)
    assert opened["spare"] > 20, (
        f"the panel is wider than the name, or this shows nothing: {opened}"
    )
    assert opened["gap"] <= 1, opened


# -------------------------------------------------------------------------- the board


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_the_boards_filters_still_work(browser: Browser, live_server, search, scripts):
    """The board has no headers, so it keeps the form: outcome, tag and gone quiet, each
    narrowing the cards to what the queryset gives of what is still live, live with a
    script and by its *Filter* button without one. The status is not in the form (#315):
    a column's heading folds the board to it, the cards on the screen are then that
    column's, and the form goes on narrowing them (`tests/e2e/test_board_fold.py`)."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=WIDE)
    page = context.new_page()

    def then(**wanted) -> None:
        if not scripts:
            page.locator("#application-filters").get_by_role("button", name="Filter").click()
        expect(page.locator(f"{TABLE} [data-card]")).to_have_count(
            len(on_the_board(person, **wanted))
        )
        assert cards_shown(page) == on_the_board(person, **wanted)
        expect(page).to_have_url(re.compile(r"[?&]view=board(&|$)"))
        if scripts:
            settled(page)

    try:
        sign_in(page, base)
        page.goto(f"{base}/applications/?view=board")
        assert cards_shown(page) == on_the_board(person)
        form = page.locator("#application-filters")

        def field(label: str):
            """The form's own control under this label: the tick box, or the native select,
            which is what the form posts with scripts and without them (#301)."""
            return form.get_by_label(label, exact=True).and_(form.locator("select, input"))

        # One control a person can see for each: a list is a combobox either way, the
        # native select without scripts and the button built for it with them. The status
        # is not among them: on the board its columns are the status (#315).
        for label, role in (
            ("Outcome", "combobox"),
            ("Tag", "combobox"),
            ("Gone quiet", "checkbox"),
        ):
            expect(form.get_by_role(role, name=label, exact=True)).to_be_visible()
        expect(form.get_by_label("Status", exact=True)).to_have_count(0)
        expect(form.get_by_role("button", name="Filter")).to_be_visible()
        expect(page.locator("[data-narrow-form]")).to_have_count(0)
        expect(page.locator("thead")).to_have_count(0)

        # The status, from its column's heading; the form then narrows what is folded.
        page.locator("#board-fold-applied").click()
        expect(page).to_have_url(re.compile(r"[?&]status=applied(&|$)"))
        if scripts:
            settled(page)
        field("Tag").select_option("remote")
        then(status="applied", tag="remote")
        field("Gone quiet").check()
        then(status="applied", tag="remote", quiet="1")
        assert times_in_the_address(page, "status") == 1, page.url
        field("Gone quiet").uncheck()
        field("Tag").select_option("")
        then(status="applied")
        page.locator("#board-unfold").click()
        expect(page).not_to_have_url(re.compile(r"[?&]status=[^&]"))
        expect(page.locator(f"{TABLE} [data-card]")).to_have_count(len(on_the_board(person)))
        assert cards_shown(page) == on_the_board(person)
        if scripts:
            settled(page)

        # What is settled is said, not shown: the board is for what is still live.
        field("Outcome").select_option("closed")
        then(state="closed")
        assert cards_shown(page) == []
        expect(page.locator("[data-off-board]")).to_have_attribute(
            "data-off-board", str(len(asked(person, state="closed")))
        )
        field("Outcome").select_option("open")
        then(state="open")
        expect(page.locator("[data-off-board]")).to_have_count(0)
    finally:
        context.close()


# -------------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_filters_pass_axe(live_server, page: Page, axe_source, search, scheme):  # noqa: F811
    """With headers open -- two narrowing and one opened by hand -- with *Narrow* open on a
    phone and at desktop width for a hidden column, and on the board, in both themes."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    failures = []

    def look(what: str) -> None:
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{what} ({scheme})", found))

    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/?status=applied&state=open&tag=remote&quiet=1")
    open_header(page, "company")
    for control in ("#filter-status", "#filter-state", "#filter-quiet", "#filter-tag"):
        expect(page.locator(control)).to_be_visible()
    look("the table with its headers open")

    page.goto(f"{base}/applications/?status=rejected&tag=dream-job")
    expect(page.locator(TABLE)).to_contain_text("Nothing matches these filters")
    look("the table with nothing matching")

    page.goto(f"{base}/applications/?view=board&status=applied&quiet=1")
    look("the board")

    page.set_viewport_size(NARROWEST)
    page.goto(f"{base}/applications/?status=applied")
    expect(page.locator("[data-narrow] [data-narrow-count]")).to_be_visible()
    look("Narrow folded, saying how many filters are in use")
    page.locator("[data-narrow] > summary").click()
    expect(seen(page, "#filter-status-narrow")).to_be_visible()
    look("Narrow on a phone")

    hide_the_status_column(search["applicant"])
    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/?status=applied&quiet=1")
    expect(seen(page, "#filter-status-narrow")).to_be_visible()
    look("Narrow for a hidden column")

    assert not failures, "\n\n".join(failures)
