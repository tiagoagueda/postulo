"""The Applications board: its columns fold to one, and it scrolls at its edge under a
dragged card (#315).

`tests/test_board_fold.py` reads what the server draws for an address. What it cannot say is
whether a person can use any of it. These do:

* **a column's heading folds the board to that column**, in place, by mouse and by keyboard:
  one column open, the others strips that keep their name and their count, the address
  saying which, the focus on the control that was pressed, `aria-expanded` on each;
* ***All columns* is the way back**, reached by Tab before any strip, and it leaves the focus
  on the heading of the column that was open;
* **without a script** the same headings are links, and the page that arrives is the same
  board; the filters' button and a live filter both keep the fold;
* **Back, a reload and a bookmark** agree with what is drawn;
* **a strip is still a column**: a card dragged onto one moves there, a card moved by its
  menu leaves the open column, and the counts -- the strips', the heading's, the page's
  own -- are the queryset's afterwards;
* **the board scrolls sideways while a card is held near its edge**, the faster the nearer,
  and not otherwise: not with nothing held, not once the card is dropped, the drag given up
  or the pointer gone from the window, and not at all under reduced motion; and it scrolls
  the other way in a right-to-left page;
* **a card that is held is not swapped away**, by a filter's answer or by a fold;
* **a status with no column** folds every column and says where those applications are;
* **on a phone** the page does not scroll sideways, no strip's name is cut, and nothing in
  the row of filters is cut, in the widest languages;
* **axe**, in both themes, open and folded;
* **several columns open at once** (#709): a strip opens beside the columns already open, an
  open column's heading folds that one alone, and a card moved between two open columns is
  still what the address asks for;
* **the board is centred**: in its box while it fits, and with its open columns brought to
  the middle of a box it does not fit;
* **a fold moves**: each column goes from its old width to its new one, and nothing moves
  for somebody who asked for less motion.

Every count is the queryset's, never a number written here; and nothing here counts lines
of text, which differ between the font this machine draws in and the one CI does.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from . import test_accessibility as walks
from . import test_application_filters as filter_tests
from .test_accessibility import describe, sign_in, violations_on
from .test_application_filters import (
    asked,
    cards_shown,
    on_the_board,
    rows_shown,
    tab_to,
    times_in_the_address,
)
from .test_board_drag import drag
from .test_column_filters import settled
from .test_navigation_bar import set_language
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

#: The five applications the table's filters are tested against, and axe's source, under
#: the names they have where they are defined: a fixture is found by its name in the module
#: that asks for it.
search = filter_tests.search
axe_source = walks.axe_source

WIDE = {"width": 1280, "height": 900}
NARROWEST = {"width": 320, "height": 640}

BOARD = "/applications/?view=board"

#: Every column as it is drawn: whether it is a strip, what its heading's control says of
#: it, whether its list of cards is on the screen, the count beside its name, the words the
#: control is described by, and its cards: all of them, and the ones that can be seen.
COLUMNS = """() => [...document.querySelectorAll('[data-board-section]')].map((section) => {
  const control = section.querySelector('[data-board-fold]');
  const cards = section.querySelector('[data-board-column]');
  const described = document.getElementById(control.getAttribute('aria-describedby'));
  const numbered = (found) => found.map((card) => Number(card.dataset.card)).sort((a, b) => a - b);
  return {
    status: section.dataset.boardSection,
    strip: section.hasAttribute('data-board-strip'),
    expanded: control.getAttribute('aria-expanded'),
    controls: control.getAttribute('aria-controls') === cards.id,
    name: control.getAttribute('aria-label'),
    described: described ? described.textContent.trim().replace(/\\s+/g, ' ') : null,
    listed: cards.checkVisibility(),
    count: Number(section.querySelector('[data-column-count]').textContent),
    cards: numbered([...cards.querySelectorAll('[data-card]')]),
    seen: numbered([...cards.querySelectorAll('[data-card]')].filter((c) => c.checkVisibility())),
  };
})"""


def statuses() -> list[str]:
    from postulo.applications.models import BOARD_STATUSES

    return [str(status) for status in BOARD_STATUSES]


def label_of(status: str) -> str:
    from postulo.applications.models import Status

    return str(Status(status).label)


def in_words(count: int) -> str:
    """A count as the page says it: under its title, and of each column to a screen reader."""
    return f"{count} application" if count == 1 else f"{count} applications"


def columns(page: Page) -> dict[str, dict]:
    return {column["status"]: column for column in page.evaluate(COLUMNS)}


def page_count(page: Page) -> int:
    return int(re.match(r"\s*(\d+)", page.locator("#applications-count").inner_text()).group(1))


def folded_to(page: Page, person, status: str, **wanted) -> None:
    """The board is folded to the columns of ``status`` -- one status, or several in one
    value, ``applied,interviewing`` (#709): those open and holding the cards the queryset
    gives them, every other a strip with no card on the screen, each saying its name and the
    count the queryset gives it under the rest of the filters -- and the page's own count is
    of what the address asks for, the statuses included. Each heading is named for what it
    does: the one column open shows every column, one of several folds itself, a strip opens
    beside them, and where nothing is open a strip opens alone."""
    named = status.split(",")
    opened = [key for key in statuses() if key in named]
    # Waited for, since a fold in place lands a moment after the press: these columns open
    # (or none, for a status with no column) and every other a strip.
    for key in opened:
        expect(page.locator(f"[data-board-section='{key}']:not([data-board-strip])")).to_have_count(
            1
        )
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - len(opened))
    drawn = columns(page)
    assert list(drawn) == statuses()
    for key, column in drawn.items():
        held = on_the_board(person, **{**wanted, "status": key})
        if key in opened:
            does = "show all columns" if len(opened) == 1 else "fold this column"
            assert not column["strip"] and column["listed"], key
            assert column["expanded"] == "true", key
            assert column["name"] == f"{label_of(key)}: {does}"
            assert column["cards"] == held, key
        else:
            does = "show this column too" if opened else "show only this column"
            assert column["strip"] and not column["listed"], key
            assert column["expanded"] == "false", key
            assert column["name"] == f"{label_of(key)}: {does}"
            assert column["cards"] == [], key
        assert column["count"] == len(held), (key, column["count"], held)
        assert column["described"] == in_words(len(held)), (key, column["described"])
        assert column["controls"], key
    expect(page.locator("#applications-count")).to_contain_text(
        re.compile(rf"^\s*{len(asked(person, **{**wanted, 'status': status}))}\b")
    )
    expect(page.locator("#board-unfold")).to_be_visible()
    expect(page.locator("#board-unfold")).to_have_text("All columns")


def unfolded(page: Page, person, **wanted) -> None:
    """Every column open, with the cards and the count the queryset gives it."""
    expect(page.locator("[data-board-strip]")).to_have_count(0)
    expect(page.locator("[data-board-section]")).to_have_count(len(statuses()))
    drawn = columns(page)
    assert list(drawn) == statuses()
    for key, column in drawn.items():
        held = on_the_board(person, **{**wanted, "status": key})
        assert not column["strip"] and column["listed"] and column["expanded"] == "true", key
        assert column["name"] == f"{label_of(key)}: show only this column"
        assert column["cards"] == held and column["count"] == len(held), key
        assert column["described"] == in_words(len(held)), (key, column["described"])
    expect(page.locator("#applications-count")).to_contain_text(
        re.compile(rf"^\s*{len(asked(person, **wanted))}\b")
    )
    expect(page.locator("#board-unfold")).to_have_count(0)


def headings_are_the_statuses(page: Page) -> None:
    """Each column's heading is called by its status, open or folded, and not by the control
    in it, whose name says what pressing it does."""
    board = page.locator("[data-board]")
    expect(board.get_by_role("heading", level=2)).to_have_count(len(statuses()))
    for status in statuses():
        expect(board.get_by_role("heading", name=label_of(status), exact=True)).to_have_count(1)


def says_status_once(page: Page, status: str) -> None:
    """The address names these statuses, once: one, or a set in one value, its commas
    written as they are or escaped, as htmx writes them."""
    written = "(?:,|%2C)".join(re.escape(one) for one in status.split(","))
    expect(page).to_have_url(re.compile(rf"[?&]status={written}(&|$)"))
    assert times_in_the_address(page, "status") == 1, page.url


def says_no_status(page: Page) -> None:
    expect(page).not_to_have_url(re.compile(r"[?&]status=[^&]"))


def fold(page: Page, status: str):
    return page.locator(f"#board-fold-{status}")


# ----------------------------------------------------------------------- by the mouse


def test_a_heading_folds_the_board_to_its_column_in_place(page: Page, live_server, search):
    """Press *Applied*: that column stays, the others become strips, the address says so,
    and nothing was loaded. Press a strip: its column opens beside it (#709). Press one of
    two open columns' headings: that one folds and the other stays. Press the open column's
    heading, or *All columns*: every column is back."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    headings_are_the_statuses(page)
    page.evaluate("() => { window.loadedOnce = true; }")

    fold(page, "applied").click()
    folded_to(page, person, "applied")
    headings_are_the_statuses(page)
    says_status_once(page, "applied")
    expect(page).to_have_url(re.compile(r"[?&]view=board(&|$)"))
    expect(fold(page, "applied")).to_be_focused()
    expect(page.get_by_role("button", name="Applied: show all columns", exact=True)).to_be_focused()

    # A strip is the same control, for its own column: it opens it beside the one open.
    settled(page)
    strip = page.get_by_role("button", name="Interviewing: show this column too", exact=True)
    expect(strip).to_have_attribute("aria-expanded", "false")
    strip.click()
    folded_to(page, person, "applied,interviewing")
    says_status_once(page, "applied,interviewing")
    expect(fold(page, "interviewing")).to_be_focused()
    expect(fold(page, "interviewing")).to_have_attribute("aria-expanded", "true")

    # One of two open columns folds itself alone, and the focus stays on its strip.
    settled(page)
    page.get_by_role("button", name="Applied: fold this column", exact=True).click()
    folded_to(page, person, "interviewing")
    says_status_once(page, "interviewing")
    expect(fold(page, "applied")).to_be_focused()
    expect(fold(page, "applied")).to_have_attribute("aria-expanded", "false")

    # The open column's heading is one way back, and the focus stays on it.
    settled(page)
    fold(page, "interviewing").click()
    unfolded(page, person)
    says_no_status(page)
    expect(fold(page, "interviewing")).to_be_focused()

    # *All columns* is the other, and leaves the focus where the reading was.
    settled(page)
    fold(page, "applied").click()
    folded_to(page, person, "applied")
    settled(page)
    page.get_by_role("button", name="All columns", exact=True).click()
    unfolded(page, person)
    says_no_status(page)
    expect(fold(page, "applied")).to_be_focused()

    assert page.evaluate("() => window.loadedOnce === true"), "the page was never loaded again"


def test_all_columns_comes_before_every_column_and_stays_where_it_is(
    page: Page, live_server, search
):
    """One control, in one place while the board is folded: before the columns in the
    source, above the board on the screen, whichever column is open."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    where = []
    for status in ("draft", "applied", "offer", "rejected"):
        page.goto(f"{base}{BOARD}&status={status}")
        control = page.locator("#board-unfold")
        expect(control).to_be_visible()
        assert control.evaluate(
            """(control) => [...document.querySelectorAll('[data-board-section] [data-board-fold]')]
              .every((heading) => control.compareDocumentPosition(heading)
                & Node.DOCUMENT_POSITION_FOLLOWING)"""
        ), status
        box = control.bounding_box()
        board = page.locator("[data-board]").bounding_box()
        assert box["y"] + box["height"] <= board["y"], status
        where.append((round(box["x"]), round(box["width"])))
    assert len(set(where)) == 1, where


# -------------------------------------------------------------------- by the keyboard


def test_the_board_folds_and_unfolds_from_the_keyboard(page: Page, live_server, search):
    """Tab reaches a heading, Enter folds to it and the focus is still on it; *All columns*
    is the first of the board's controls the keyboard reaches, before any strip; Space
    presses a strip as Enter does, because the control is a button while it folds in
    place, and opens its column beside the open one (#709)."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)

    tab_to(page, "#board-fold-applied")
    page.keyboard.press("Enter")
    folded_to(page, person, "applied")
    says_status_once(page, "applied")
    expect(fold(page, "applied")).to_be_focused()
    expect(fold(page, "applied")).to_have_attribute("aria-expanded", "true")
    settled(page)

    # From the row of filters above it, the first of the board's own controls the keyboard
    # reaches is *All columns*.
    page.locator("#filter-state").focus()
    tab_to(page, "[data-board-fold]")
    expect(page.locator("#board-unfold")).to_be_focused()

    # On to a strip, and Space.
    tab_to(page, "#board-fold-interviewing")
    expect(fold(page, "interviewing")).to_have_attribute("aria-expanded", "false")
    scrolled = page.evaluate("() => window.scrollY")
    page.keyboard.press("Space")
    folded_to(page, person, "applied,interviewing")
    says_status_once(page, "applied,interviewing")
    expect(fold(page, "interviewing")).to_be_focused()
    expect(fold(page, "interviewing")).to_have_attribute("aria-expanded", "true")
    assert page.evaluate("() => window.scrollY") == scrolled, (
        "Space pressed it, and scrolled nothing"
    )
    settled(page)

    # Back up to *All columns*, and Enter: the focus lands on the first column that was open.
    tab_to(page, "#board-unfold", key="Shift+Tab")
    page.keyboard.press("Enter")
    unfolded(page, person)
    says_no_status(page)
    expect(fold(page, "applied")).to_be_focused()
    settled(page)

    # And Space on an open column's heading folds to it.
    page.keyboard.press("Space")
    folded_to(page, person, "applied")
    expect(fold(page, "applied")).to_be_focused()


def test_space_held_down_presses_a_heading_once(page: Page, live_server, search):
    """A key held down repeats. Each repeat of Space was another press, so a heading held
    for half a second folded and unfolded the board by turns, and where it ended was a
    matter of how many repeats there had been. One press, however long it is held; and the
    repeats still do not scroll the page."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    asked_for = []
    page.on(
        "request",
        lambda request: asked_for.append(request.url) if "/applications/" in request.url else None,
    )
    scrolled = page.evaluate("() => window.scrollY")

    fold(page, "applied").focus()
    page.keyboard.down("Space")
    for _ in range(6):
        # Each further `down` of a key that is down is a repeat, as a held key sends them.
        page.wait_for_timeout(60)
        page.keyboard.down("Space")
    page.keyboard.up("Space")

    unfolded(page, person)
    settled(page)
    page.wait_for_timeout(300)
    assert len(asked_for) == 1, asked_for
    unfolded(page, person)
    says_no_status(page)
    expect(fold(page, "applied")).to_be_focused()
    assert page.evaluate("() => window.scrollY") == scrolled


#: The focus ring of the control that has the focus, as a box: the control's own box, out
#: by the outline's offset and its width. And the part of the board's scroll box that is
#: drawn: a box that scrolls clips at the inside of its border, on every side.
RING = """() => {
  const control = document.activeElement;
  const box = document.querySelector('[data-board]');
  const style = getComputedStyle(control);
  const out = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
  const c = control.getBoundingClientRect();
  const b = box.getBoundingClientRect();
  const left = b.left + box.clientLeft, top = b.top + box.clientTop;
  return {
    id: control.id,
    shown: control.matches(':focus-visible'),
    style: style.outlineStyle,
    width: parseFloat(style.outlineWidth),
    ring: [c.left - out, c.top - out, c.right + out, c.bottom + out],
    drawn: [left, top, left + box.clientWidth, top + box.clientHeight],
    screen: [0, 0, document.documentElement.clientWidth, window.innerHeight],
    clips: getComputedStyle(box).overflowY,
  };
}"""


@pytest.mark.parametrize("colours", ["none", "active"], ids=["its own colours", "forced colours"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_focus_ring_of_a_heading_is_not_cut_by_the_boards_box(
    page: Page, live_server, search, language, colours
):
    """The board scrolls sideways in a box, and a box that scrolls one way clips the other
    way too. The usual ring is drawn two pixels outside its control: on a strip only two of
    its four sides were left, and every heading lost its top. The ring of a heading is
    drawn inside the control, so all four sides are inside what the box draws: the first
    strip and the last, the open column's heading, every heading of an open board, in both
    directions of writing, and in the system's own colours."""
    set_language(search, language)
    page.emulate_media(forced_colors=colours)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)

    for address in (f"{BOARD}&status=applied", BOARD):
        page.goto(f"{base}{address}")
        page.locator("#filter-state").focus()
        for status in statuses():
            tab_to(page, f"#board-fold-{status}")
            # Chromium leaves a control that is partly in the box where it is when it takes
            # the focus. What is asked here is whether the ring of a control that is in the
            # box is in it too, so the control is brought in, the least way: right up
            # against the box's edge, where a ring outside it has nowhere to be drawn.
            page.evaluate(
                "() => document.activeElement.scrollIntoView({block: 'nearest', inline: 'nearest'})"
            )
            found = page.evaluate(RING)
            assert found["id"] == f"board-fold-{status}" and found["shown"], found
            assert found["style"] == "solid" and found["width"] >= 2, found
            assert found["clips"] != "visible", "the box clips what is drawn outside it"
            ring, drawn, screen = found["ring"], found["drawn"], found["screen"]
            for side, sign in (("left", 1), ("top", 1), ("right", -1), ("bottom", -1)):
                index = ("left", "top", "right", "bottom").index(side)
                inside = (ring[index] - drawn[index]) * sign
                assert inside >= 0, f"{address} {status}: the ring's {side} is cut by {-inside}"
                assert (ring[index] - screen[index]) * sign >= 0, (address, status, side)


# ------------------------------------------------------------------- without a script


def test_without_a_script_a_heading_is_a_link_to_the_folded_board(
    browser: Browser, live_server, search
):
    """The same headings, as the links the server drew: each goes to the address that
    draws the board folded to its column, and *All columns* to the one that unfolds it.
    The filters' button keeps the fold, because the status in force is a field of that
    form. Nothing is called a button, and nothing can be dragged."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}{BOARD}")
        unfolded(page, person)
        expect(page.locator("[data-board-fold][role]")).to_have_count(0)
        expect(page.locator("[data-card][draggable]")).to_have_count(0)

        page.get_by_role("link", name="Applied: show only this column", exact=True).click()
        says_status_once(page, "applied")
        folded_to(page, person, "applied")

        page.get_by_role("link", name="Interviewing: show this column too", exact=True).click()
        says_status_once(page, "applied,interviewing")
        folded_to(page, person, "applied,interviewing")

        # A filter, sent by its button, narrows the folded board and leaves it folded.
        form = page.locator("#application-filters")
        expect(form.get_by_label("Status", exact=True)).to_have_count(0)
        form.locator("select[name=tag]").select_option("dream-job")
        form.get_by_role("button", name="Filter", exact=True).click()
        says_status_once(page, "applied,interviewing")
        expect(page).to_have_url(re.compile(r"[?&]tag=dream-job(&|$)"))
        folded_to(page, person, "applied,interviewing", tag="dream-job")

        # One of the two folds alone, the tag kept.
        page.get_by_role("link", name="Applied: fold this column", exact=True).click()
        says_status_once(page, "interviewing")
        expect(page).to_have_url(re.compile(r"[?&]tag=dream-job(&|$)"))
        folded_to(page, person, "interviewing", tag="dream-job")

        # The open column's heading goes back to every column, the tag kept.
        page.get_by_role("link", name="Interviewing: show all columns", exact=True).click()
        says_no_status(page)
        expect(page).to_have_url(re.compile(r"[?&]tag=dream-job(&|$)"))
        unfolded(page, person, tag="dream-job")

        # And so does *All columns*.
        page.get_by_role("link", name="Draft: show only this column", exact=True).click()
        folded_to(page, person, "draft", tag="dream-job")
        page.get_by_role("link", name="All columns", exact=True).click()
        says_no_status(page)
        unfolded(page, person, tag="dream-job")
    finally:
        context.close()


@pytest.mark.parametrize("scripts", [False, True], ids=["scripts off", "scripts on"])
def test_a_board_that_opens_folded_by_a_default_view_can_be_unfolded(
    browser: Browser, live_server, search, scripts
):
    """Somebody keeps *Applied* as their default view, so Applications opens folded to it.
    *All columns* and the open column's heading led to the bare address, which answers
    with that view again: with scripts off the board could not be unfolded at all, and
    with them on the same link in a new tab or a bookmark could not. Both say the plain
    board, and the address they leave draws it again by itself."""
    from postulo.applications.tables import ApplicationsTable
    from postulo.core import tables

    person = search["applicant"]
    kept = ApplicationsTable.save_view({"shape": "board"}, "Applied only", "status=applied", [])
    tables.save_settings(
        person, "applications", ApplicationsTable.make_default(kept, "applied-only")
    )
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        for way_back in ("#board-unfold", "#board-fold-applied"):
            page.goto(f"{base}/applications/")
            expect(page).to_have_url(re.compile(r"[?&]saved=applied-only(&|$)"))
            folded_to(page, person, "applied")
            control = page.locator(way_back)
            assert control.get_attribute("href") == "/applications/?saved=none", way_back

            control.click()
            unfolded(page, person)
            says_no_status(page)
            # The address it left is not the bare one: asked for again, it is this board.
            assert page.url != f"{base}/applications/", way_back
            page.reload()
            unfolded(page, person)

            # And the link as a link, which is what a new tab and a bookmark follow.
            page.goto(f"{base}/applications/?saved=none")
            expect(page).to_have_url(re.compile(r"[?&]saved=none(&|$)"))
            unfolded(page, person)
    finally:
        context.close()


#: Every click from now on, as it is left for the browser: on which link, with a modifier
#: held or not, and whether anything cancelled it. Heard at the window, before any of the
#: page's own listeners, and its fate read once they have all run.
HEAR_CLICKS = """() => {
  window.clicksHeard = [];
  window.addEventListener('click', (event) => {
    const link = event.target.closest('a');
    const heard = {
      on: link ? link.id : null,
      modified: event.ctrlKey || event.metaKey || event.shiftKey,
      trusted: event.isTrusted,
    };
    window.clicksHeard.push(heard);
    setTimeout(() => { heard.cancelled = event.defaultPrevented; }, 0);
  }, true);
}"""


def click_heard(page: Page, control, modifiers: list[str]) -> dict:
    """Click a control and say what became of the click. Whatever page the browser opened
    for it is closed: this is about the page the click was made on."""
    control.click(modifiers=modifiers)
    page.wait_for_function(
        "() => window.clicksHeard.length && 'cancelled' in window.clicksHeard.at(-1)"
    )
    heard = page.evaluate("() => window.clicksHeard.at(-1)")
    page.wait_for_timeout(400)
    for other in page.context.pages:
        if other != page:
            other.close()
    return heard


@pytest.mark.parametrize("modifier", ["ControlOrMeta", "Shift"])
def test_a_click_with_a_modifier_is_the_browsers_and_folds_nothing_here(
    page: Page, live_server, search, modifier
):
    """Ctrl and a click on a link asks for it in a new tab, Shift in a new window, and a
    heading is a link. htmx took the click whatever was held, cancelled it and folded the
    board where it was, so nothing opened. A click with a modifier is left to the browser:
    nothing cancels it, nothing is asked for this board, and this board is as it was. The
    address the browser is left to follow is the link's own, which draws the fold by
    itself. *All columns* the same.

    What is held here is the page's half: the click, a real one, reaches the browser
    uncancelled. Whether a new tab then opens is not asserted. The headless shell this
    suite runs in does not open one every time for a modified click on any link -- an
    ordinary one included, when it was measured -- while the whole browser
    (`--browser-channel chromium`) did, forty times of forty, a heading's among them; so a
    test of that here would be a test of the shell."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    expect(fold(page, "applied")).to_have_attribute("role", "button")
    page.evaluate(HEAR_CLICKS)
    asked_for = []
    page.on(
        "request",
        lambda request: asked_for.append(request.url) if "/applications/" in request.url else None,
    )

    heard = click_heard(page, fold(page, "applied"), [modifier])
    assert heard == {
        "on": "board-fold-applied",
        "modified": True,
        "trusted": True,
        "cancelled": False,
    }
    assert asked_for == [], "nothing was asked for this board"
    unfolded(page, person)
    says_no_status(page)
    # Where the browser goes with it: the link's own address, which is the fold.
    elsewhere = page.context.new_page()
    try:
        elsewhere.set_viewport_size(WIDE)
        elsewhere.goto(f"{base}{fold(page, 'applied').get_attribute('href')}")
        says_status_once(elsewhere, "applied")
        folded_to(elsewhere, person, "applied")
    finally:
        elsewhere.close()

    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    page.evaluate(HEAR_CLICKS)
    asked_for.clear()
    for control in ("board-unfold", "board-fold-applied", "board-fold-draft"):
        heard = click_heard(page, page.locator(f"#{control}"), [modifier])
        assert heard == {"on": control, "modified": True, "trusted": True, "cancelled": False}
        assert asked_for == [], f"{control}: nothing was asked for this board"
        folded_to(page, person, "applied")
        says_status_once(page, "applied")

    # A click with nothing held is still the fold in place, and is htmx's: cancelled as a
    # link, answered in the board.
    heard = click_heard(page, page.locator("#board-unfold"), [])
    assert heard == {"on": "board-unfold", "modified": False, "trusted": True, "cancelled": True}
    unfolded(page, person)
    assert len(asked_for) == 1


def test_a_live_filter_and_the_search_keep_the_board_folded(page: Page, live_server, search):
    """The status left the row of filters, and the row still sends it: a tag chosen above a
    folded board narrows what is in the open column and in every strip's count, and the
    masthead's box does the same. Unfolding then keeps both."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    form = page.locator("#application-filters")
    expect(form.locator("select[name=status]")).to_have_count(0)
    # What a person sees for each: a list is a combobox with scripts on or off (#301).
    for label, role in (("Outcome", "combobox"), ("Tag", "combobox"), ("Gone quiet", "checkbox")):
        expect(form.get_by_role(role, name=label, exact=True)).to_be_visible()

    form.locator("select[name=tag]").select_option("remote")
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    folded_to(page, person, "applied", tag="remote")
    says_status_once(page, "applied")
    settled(page)

    page.locator("#site-search").fill("aperture")
    expect(page).to_have_url(re.compile(r"[?&]q=aperture(&|$)"))
    folded_to(page, person, "applied", tag="remote", q="aperture")
    says_status_once(page, "applied")
    settled(page)

    # A fold asked for now is the question as it stands: the tag and the search with it.
    fold(page, "draft").click()
    says_status_once(page, "draft,applied")
    folded_to(page, person, "draft,applied", tag="remote", q="aperture")
    settled(page)

    page.locator("#board-unfold").click()
    says_no_status(page)
    for kept in ("tag=remote", "q=aperture", "view=board"):
        expect(page).to_have_url(re.compile(rf"[?&]{kept}(&|$)"))
    unfolded(page, person, tag="remote", q="aperture")


def asked_of_the_board(page: Page) -> list[str]:
    """Every request this page makes for the Applications page from now on, in order."""
    seen: list[str] = []
    page.on(
        "request",
        lambda request: seen.append(request.url) if "/applications/?" in request.url else None,
    )
    return seen


def hold_the_first(page: Page) -> list:
    """Keep the next request for the Applications page from reaching the server, and let
    every one after it through: an answer that is slow, with a newer question behind it."""
    held: list = []

    def handle(route):
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route(re.compile(r"/applications/\?"), handle)
    return held


def wait_until_held(page: Page, held: list) -> None:
    for _ in range(100):
        if held:
            return
        page.wait_for_timeout(50)
    raise AssertionError("the request never left the page")


def statuses_asked(url: str) -> list[str]:
    from urllib.parse import parse_qs, urlsplit

    return [s for s in parse_qs(urlsplit(url).query, keep_blank_values=True).get("status", []) if s]


@pytest.mark.parametrize(
    ("starts", "pressed", "lands"),
    [
        ("&status=applied", "#board-fold-draft", "draft,applied"),
        ("", "#board-fold-applied", "applied"),
        ("&status=applied", "#board-unfold", ""),
        ("&status=applied", "#board-fold-applied", ""),
    ],
    ids=["a strip", "a heading of an open board", "All columns", "the open column's heading"],
)
def test_a_filter_chosen_while_a_fold_is_on_its_way_takes_the_fold_with_it(
    page: Page, live_server, search, starts, pressed, lands
):
    """The status the filter form sends is a field drawn with the board, so while a fold is
    on its way it is still the old one. A filter chosen then is the newer request, replaces
    the fold's, and asked for the old status: the press was dropped without a word. The
    field is given the pressed status as the fold is sent -- made, where the board had
    none, and taken away by a fold that opens every column -- so the filter asks for what
    was pressed."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}{starts}")
    expect(page.locator("[data-board-fold][role=button]").first).to_be_visible()
    seen = asked_of_the_board(page)
    held = hold_the_first(page)

    page.locator(pressed).click()
    wait_until_held(page, held)
    assert statuses_asked(seen[0]) == ([lands] if lands else [])
    page.locator("#application-filters select[name=tag]").select_option("remote")

    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    assert len(seen) == 2, seen
    assert statuses_asked(seen[1]) == ([lands] if lands else []), seen[1]
    if lands:
        folded_to(page, person, lands, tag="remote")
        says_status_once(page, lands)
    else:
        unfolded(page, person, tag="remote")
        says_no_status(page)
    sent = page.locator("input[type=hidden][name=status][form=application-filters]")
    expect(sent).to_have_count(1 if lands else 0)
    if lands:
        expect(sent).to_have_value(lands)
    assert sent.evaluate_all("all => all.every((field) => field.closest('#applications-table'))")


def test_a_fold_pressed_while_a_filter_is_on_its_way_takes_the_filter_with_it(
    page: Page, live_server, search
):
    """The other order, which always worked: a fold asks for the page with the filter form
    as it stands, so a tag chosen a moment before is in it."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    seen = asked_of_the_board(page)
    held = hold_the_first(page)

    page.locator("#application-filters select[name=tag]").select_option("remote")
    wait_until_held(page, held)
    assert statuses_asked(seen[0]) == ["applied"] and "tag=remote" in seen[0]
    fold(page, "draft").click()

    says_status_once(page, "draft,applied")
    assert len(seen) == 2 and statuses_asked(seen[1]) == ["draft,applied"]
    assert "tag=remote" in seen[1]
    folded_to(page, person, "draft,applied", tag="remote")


def test_a_fold_that_fails_leaves_the_form_asking_for_the_board_that_is_drawn(
    page: Page, live_server, search
):
    """The field is given the pressed status as the fold is sent. If the fold then fails,
    the board on the screen is still the one that was drawn, and the field goes back to
    its status: the next filter narrows the board that is there, and the failure is said."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    sent = page.locator("input[type=hidden][name=status][form=application-filters]")
    refused = re.compile(r"/applications/\?.*status=draft")
    for answer in ("a 500", "no answer"):
        if answer == "a 500":
            page.route(refused, lambda route: route.fulfill(status=500, body="no"))
        else:
            page.route(refused, lambda route: route.abort())
        fold(page, "draft").click()
        expect(page.locator("[data-htmx-alert]")).not_to_be_empty()
        expect(sent).to_have_value("applied")
        expect(sent).to_have_count(1)
        page.unroute(refused)

    page.locator("#application-filters select[name=tag]").select_option("remote")
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    says_status_once(page, "applied")
    folded_to(page, person, "applied", tag="remote")

    # And where the board had no status: a fold that fails leaves none behind.
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    page.route(refused, lambda route: route.fulfill(status=500, body="no"))
    fold(page, "draft").click()
    expect(page.locator("[data-htmx-alert]")).not_to_be_empty()
    expect(sent).to_have_count(0)
    page.unroute(refused)


# ------------------------------------------------------ Back, a reload and a bookmark


def test_back_a_reload_and_a_bookmark_agree_with_what_is_drawn(page: Page, live_server, search):
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    fold(page, "applied").click()
    folded_to(page, person, "applied")
    settled(page)
    fold(page, "interviewing").click()
    folded_to(page, person, "applied,interviewing")
    settled(page)

    page.go_back()
    says_status_once(page, "applied")
    folded_to(page, person, "applied")
    expect(page).to_have_title(re.compile(r"Applications$"))
    expect(page.locator("header[data-site-header]")).to_be_visible()

    page.go_back()
    says_no_status(page)
    unfolded(page, person)

    page.go_forward()
    says_status_once(page, "applied")
    folded_to(page, person, "applied")

    page.reload()
    says_status_once(page, "applied")
    folded_to(page, person, "applied")

    # A bookmark: the address alone, in a tab that never saw the board unfolded.
    bookmark = page.url
    other = page.context.new_page()
    try:
        other.set_viewport_size(WIDE)
        other.goto(bookmark)
        folded_to(other, person, "applied")
    finally:
        other.close()


# ------------------------------------------------------------- a strip is still a column


def test_a_card_dropped_on_a_strip_moves_to_that_column(page: Page, live_server, search):
    """The strip is what the card is dropped on. The move goes through the card's own
    form, so the timeline has it; the board comes back folded as it was, the card gone
    from the open column, and every count is the queryset's."""
    from postulo.applications.models import Application, ApplicationEvent, EventKind, Status

    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    moved, stays = on_the_board(person, status="applied")
    before = columns(page)

    card = page.locator(f"[data-card='{moved}']")
    strip = page.locator("[data-board-section='interviewing']")
    expect(strip).to_have_attribute("data-board-strip", "")
    with page.expect_navigation():
        drag(page, card.element_handle(), strip.element_handle())

    assert Application.objects.get(pk=moved).status == Status.INTERVIEWING
    event = ApplicationEvent.objects.filter(
        application_id=moved, kind=EventKind.STATUS_CHANGE
    ).latest("pk")
    assert event.from_status == Status.APPLIED and event.to_status == Status.INTERVIEWING

    says_status_once(page, "applied")
    folded_to(page, person, "applied")
    after = columns(page)
    assert after["applied"]["cards"] == [stays]
    assert after["applied"]["count"] == before["applied"]["count"] - 1
    assert after["interviewing"]["count"] == before["interviewing"]["count"] + 1
    assert page_count(page) == after["applied"]["count"], "the page counts what the address asks"
    # The card is in a strip now, and nowhere on the screen: the page says where it went.
    expect(page.get_by_role("status").filter(has_text="Moved to Interviewing.")).to_be_visible()
    expect(page.get_by_text("Status updated.")).to_have_count(0)


def test_a_strip_takes_the_card_and_counts_it_before_the_page_answers(
    page: Page, live_server, search
):
    """The card moves at once, as it does between open columns: out of the open column and
    into the strip's list, which is not on the screen, with both counts following."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    before = columns(page)
    moved = on_the_board(person, status="applied")[0]

    # The card's form is kept from sending, so the page as the drop left it can be read.
    page.evaluate(
        """() => document.addEventListener('submit', (event) => {
            window.sentBy = event.target.getAttribute('action');
            event.preventDefault();
        }, true)"""
    )
    card = page.locator(f"[data-card='{moved}']")
    strip = page.locator("[data-board-section='offer']")
    cancelled = page.evaluate(
        """([card, strip]) => {
            const transfer = new DataTransfer();
            const fire = (target, type) => {
                const event = new DragEvent(
                    type, {bubbles: true, cancelable: true, dataTransfer: transfer});
                target.dispatchEvent(event);
                return event.defaultPrevented;
            };
            fire(card, 'dragstart');
            const answers = {enter: fire(strip, 'dragenter'), over: fire(strip, 'dragover')};
            fire(strip, 'drop');
            fire(card, 'dragend');
            return answers;
        }""",
        [card.element_handle(), strip.element_handle()],
    )
    assert cancelled == {"enter": True, "over": True}, "a strip accepts the drag"

    now = columns(page)
    assert now["applied"]["count"] == before["applied"]["count"] - 1
    assert now["offer"]["count"] == before["offer"]["count"] + 1
    assert moved not in now["applied"]["cards"]
    expect(card).to_be_hidden()
    assert card.evaluate("(card) => card.parentElement.dataset.boardColumn") == "offer"
    assert card.evaluate("(card) => card.querySelector('select').value") == "offer"
    assert page.evaluate("() => window.sentBy") == f"/applications/{moved}/status/"


def test_a_card_moved_by_its_menu_into_a_folded_column_leaves_the_open_one(
    page: Page, live_server, search
):
    """The menu is the way that works with a keyboard, a touch screen and a screen reader.
    Folded, the column chosen is a strip: the card leaves the open column, the strip's
    count is right, and the page says where the card went, as it does for any move made
    on the board."""
    from postulo.applications.models import Application, Status

    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied&tag=remote")
    folded_to(page, person, "applied", tag="remote")
    (moved,) = on_the_board(person, status="applied", tag="remote")
    before = columns(page)

    with page.expect_navigation():
        page.locator(f"[data-card='{moved}'] select[name=status]").select_option("offer")

    assert Application.objects.get(pk=moved).status == Status.OFFER
    says_status_once(page, "applied")
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    folded_to(page, person, "applied", tag="remote")
    after = columns(page)
    assert after["applied"]["cards"] == [] and after["applied"]["count"] == 0
    assert after["offer"]["count"] == before["offer"]["count"] + 1
    assert page_count(page) == 0
    expect(page.get_by_role("status").filter(has_text="Moved to Offer.")).to_be_visible()
    expect(page.locator("[data-board-section='applied'] [data-empty]")).to_be_visible()


def test_tabbing_to_a_strip_says_how_many_cards_it_holds(page: Page, live_server, search):
    """The count beside a strip's name was a bare figure and no part of the control, so a
    screen reader tabbing through the strips heard each name and never the count. The
    control is described by it, in words; the open column's heading the same. Asked of the
    browser's own reading of the page, and after a fold in place as well as on arrival."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=interviewing")
    folded_to(page, person, "interviewing")

    def said(status: str, does: str) -> None:
        control = page.get_by_role("button", name=f"{label_of(status)}: {does}", exact=True)
        held = len(on_the_board(person, status=status))
        expect(control).to_have_accessible_description(in_words(held))

    said("applied", "show this column too")
    assert len(on_the_board(person, status="applied")) == 2
    said("draft", "show this column too")
    said("offer", "show this column too")
    said("interviewing", "show all columns")
    # The figure is what is seen, and is not read out a second time beside the words.
    figure = page.locator("[data-board-section='applied'] [data-column-count]")
    expect(figure).to_be_visible()
    expect(figure).to_have_attribute("aria-hidden", "true")
    expect(figure).to_have_text("2")

    fold(page, "applied").click()
    folded_to(page, person, "applied,interviewing")
    said("applied", "fold this column")
    said("interviewing", "fold this column")
    said("draft", "show this column too")


# ------------------------------------------------------------- scrolling at the edge

SCROLL = "() => document.querySelector('[data-board]').scrollLeft"


def scroll_of(page: Page) -> float:
    return page.evaluate(SCROLL)


def room_left(page: Page) -> float:
    """How much further the board can be scrolled towards its last column."""
    return page.evaluate(
        "() => { const b = document.querySelector('[data-board]');"
        " return b.scrollWidth - b.clientWidth - Math.abs(b.scrollLeft); }"
    )


def pick_up(page: Page, card) -> None:
    """Press a card and move a little: a drag the browser begins itself, as a hand does."""
    where = card.bounding_box()
    x, y = where["x"] + where["width"] / 2, where["y"] + where["height"] - 6
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 8, y + 4, steps=4)
    expect(card).to_have_class(re.compile(r"(^|\s)opacity-50(\s|$)"))


#: How far inside an edge the pointer is held. Chromium scrolls a box under a drag by itself
#: within twenty pixels of its edge, whatever the page does; forty is outside that and well
#: inside the band the page scrolls in, so what moves the board here is the page.
INSIDE = 40.0


def at_the_edge(page: Page, end: bool, inside: float = INSIDE) -> tuple[float, float]:
    """A point level with the board and `inside` pixels from one of its side edges: its
    inline end, or its inline start. Which side that is depends on the page's direction."""
    box = page.locator("[data-board]").bounding_box()
    rtl = page.evaluate("() => getComputedStyle(document.documentElement).direction === 'rtl'")
    on_the_right = end != rtl
    x = box["x"] + box["width"] - inside if on_the_right else box["x"] + inside
    return x, box["y"] + box["height"] / 2


def hold_at(page: Page, x: float, y: float, for_ms: int) -> None:
    """Keep the pointer at one place for a while, moving it by a pixel as a hand does: a
    browser reports a drag at rest every few hundred milliseconds, and the driver only when
    the pointer moves."""
    for step in range(max(1, for_ms // 50)):
        page.mouse.move(x, y + (step % 2))
        page.wait_for_timeout(50)


def hold_until(page: Page, x: float, y: float, done, what: str) -> None:
    """Keep the pointer at one place until something has happened, however slowly this
    machine is drawing: the board scrolls by the time that passes, not by the frame."""
    for _ in range(100):
        hold_at(page, x, y, for_ms=100)
        if done():
            return
    raise AssertionError(f"{what}: the scroll position is {scroll_of(page)}")


def is_still(page: Page) -> bool:
    first = scroll_of(page)
    page.wait_for_timeout(250)
    return scroll_of(page) == first


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_board_scrolls_while_a_card_is_held_near_its_edge(
    page: Page, live_server, search, language
):
    """Near the inline-end edge the board scrolls towards its last column, near the
    inline-start edge back towards its first; in Arabic both are the other way round, and
    so is the sign of the scroll position. Away from the edges it does not move, and it
    stops when the card is let go."""
    from postulo.applications.models import Application, Status

    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    expect(page.locator("html")).to_have_attribute("dir", "rtl" if language == "ar" else "ltr")
    forwards = -1 if language == "ar" else 1
    room = page.evaluate(
        "() => { const b = document.querySelector('[data-board]');"
        " return b.scrollWidth - b.clientWidth; }"
    )
    assert room > 400, "a board wider than its box, or this shows nothing"
    assert scroll_of(page) == 0
    page.evaluate("() => { window.loadedOnce = true; }")

    # With nothing held, the pointer at the edge moves nothing.
    end = at_the_edge(page, end=True)
    hold_at(page, *end, for_ms=300)
    assert scroll_of(page) == 0

    card = page.locator("[data-board-section='draft'] [data-card]").first
    held = int(card.get_attribute("data-card"))
    pick_up(page, card)
    assert is_still(page), "held in the middle of its own column, the board does not move"

    hold_until(
        page,
        *end,
        lambda: scroll_of(page) * forwards > 60,
        "held near its end edge, the board scrolls towards its last column",
    )
    reached = scroll_of(page) * forwards

    # Away from the edge it stops, where it is.
    box = page.locator("[data-board]").bounding_box()
    middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    hold_at(page, *middle, for_ms=100)
    assert is_still(page)
    there = scroll_of(page) * forwards
    assert there >= reached

    # And back the other way at the other edge.
    start = at_the_edge(page, end=False)
    hold_until(
        page,
        *start,
        lambda: scroll_of(page) * forwards < there - 30,
        "held near its start edge, the board scrolls back towards its first column",
    )
    hold_at(page, *middle, for_ms=100)
    assert is_still(page)

    # Let go where there is no column: the drag ends, and the board stays where it is.
    hold_at(page, *end, for_ms=150)
    page.mouse.move(end[0], box["y"] + box["height"] - 4)
    page.mouse.up()
    assert is_still(page), "let go, and still"
    expect(card).not_to_have_class(re.compile(r"(^|\s)opacity-50(\s|$)"))
    assert page.evaluate("() => window.loadedOnce === true"), "nothing was moved or loaded"
    assert Application.objects.get(pk=held).status == Status.DRAFT
    hold_at(page, *end, for_ms=200)
    assert is_still(page), "and with nothing held it does not start again"


def test_the_nearer_the_edge_the_faster_the_board_scrolls(page: Page, live_server, search):
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    card = page.locator("[data-board-section='draft'] [data-card]").first
    pick_up(page, card)

    def speed(inside: float) -> float:
        x, y = at_the_edge(page, end=True, inside=inside)
        page.mouse.move(x, y)
        page.wait_for_timeout(60)
        began = page.evaluate(f"() => [performance.now(), ({SCROLL})()]")
        hold_at(page, x, y, for_ms=500)
        ended = page.evaluate(f"() => [performance.now(), ({SCROLL})()]")
        return (ended[1] - began[1]) / (ended[0] - began[0])

    # Both outside the twenty pixels in which the browser scrolls by itself.
    far = speed(60)
    near = speed(24)
    box = page.locator("[data-board]").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] - 4)
    page.mouse.up()
    assert far > 0, "inside the band the board moves"
    assert near > far * 1.5, f"{near:.2f} px/ms near the edge against {far:.2f} further in"


def test_a_card_dropped_on_a_column_reached_by_scrolling_leaves_the_board_there(
    page: Page, live_server, search
):
    """A card is held at the board's end edge until its last column comes into view, and
    dropped on it. The move loads the page, and the board came back at its start, with the
    column the card went to off the screen again. It comes back where it was, the card in
    sight, and the page says where the card went. Once: the page loaded again by hand is a
    board at its start."""
    from postulo.applications.models import Application, Status

    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 900, "height": 800})
    page.goto(f"{base}{BOARD}")
    assert scroll_of(page) == 0 and room_left(page) > 600
    card = page.locator("[data-board-section='draft'] [data-card]").first
    moved = int(card.get_attribute("data-card"))
    offer = page.locator("[data-board-section='offer']")
    expect(offer).not_to_be_in_viewport()

    pick_up(page, card)
    hold_until(
        page,
        *at_the_edge(page, end=True, inside=30),
        lambda: room_left(page) < 2,
        "held at its end edge, the board scrolls to its last column",
    )
    reached = scroll_of(page)
    target = offer.bounding_box()
    x, y = target["x"] + target["width"] / 2, target["y"] + 120
    page.mouse.move(x, y, steps=5)
    page.mouse.move(x, y + 2)
    with page.expect_navigation():
        page.mouse.up()

    assert Application.objects.get(pk=moved).status == Status.OFFER
    expect(page.get_by_role("status").filter(has_text="Moved to Offer.")).to_be_visible()
    page.wait_for_function(f"(reached) => Math.abs(({SCROLL})() - reached) <= 1", arg=reached)
    expect(page.locator(f"[data-board-section='offer'] [data-card='{moved}']")).to_be_in_viewport()

    page.reload()
    expect(page.locator("[data-card][draggable=true]").first).to_be_visible()
    assert scroll_of(page) == 0, "kept for the load the move made, and no longer"


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_card_moved_by_its_menu_leaves_the_board_where_it_was_scrolled(
    page: Page, live_server, search, language
):
    """The menu is the way that works everywhere, and it loads the page as a drop does. A
    board scrolled by hand to a column, and a card moved from there by its menu, comes back
    scrolled to that column: in a page written right to left as well, where the scroll
    position counts the other way."""
    from postulo.applications.models import Application, Status

    set_language(search, language)
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 900, "height": 800})
    page.goto(f"{base}{BOARD}")
    (moved,) = on_the_board(person, status="interviewing")
    card = page.locator(f"[data-card='{moved}']")
    card.scroll_into_view_if_needed()
    page.evaluate(
        "(by) => { document.querySelector('[data-board]').scrollLeft += by; }",
        -90 if language == "ar" else 90,
    )
    scrolled = scroll_of(page)
    assert abs(scrolled) > 200, scrolled

    with page.expect_navigation():
        card.locator("select[name=status]").select_option("assessment")

    assert Application.objects.get(pk=moved).status == Status.ASSESSMENT
    page.wait_for_function(f"(scrolled) => Math.abs(({SCROLL})() - scrolled) <= 1", arg=scrolled)
    expect(page.locator(f"[data-board-section='assessment'] [data-card='{moved}']")).to_be_visible()


def test_a_swap_elsewhere_on_the_page_leaves_the_board_where_it_was_scrolled(
    page: Page, live_server, search
):
    """On a phone the open column of a folded board is brought into the scroll box, when
    the page arrives and when the board is swapped. It was brought back after any swap on
    the page: somebody who had scrolled the box back to read the strips, and then changed
    the theme from the account menu, had it taken out of their hands. Only a swap that
    brings a board does it."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 390, "height": 740})
    page.goto(f"{base}{BOARD}&status=offer")
    folded_to(page, person, "offer")
    arrived = scroll_of(page)
    assert arrived > 0, "the open column was brought into the box on arrival"
    assert page.evaluate(OPEN_COLUMN_IN_VIEW)

    page.evaluate("() => { document.querySelector('[data-board]').scrollLeft = 0; }")
    page.get_by_label("Account menu", exact=False).click()
    with page.expect_response(lambda r: "/theme/" in r.url and r.request.method == "POST"):
        page.locator("#theme-switch-button").click()
    settled(page)
    page.wait_for_timeout(200)
    assert scroll_of(page) == 0, "the theme changed, and the board stayed where it was put"

    # The board's own swap still brings the open column in.
    page.keyboard.press("Escape")
    page.locator("#application-filters input[name=quiet]").check()
    expect(page).to_have_url(re.compile(r"[?&]quiet=1(&|$)"))
    folded_to(page, person, "offer", quiet=True)
    page.wait_for_function(OPEN_COLUMN_IN_VIEW)


#: How fast the page itself scrolls the board with a card held `inside` pixels from the
#: board's end edge, in pixels a second: a drag the page is told of, which no browser adds
#: anything to, since a browser scrolls a box only under a drag of its own making.
OWN_SPEED = """async (inside) => {
  const box = document.querySelector('[data-board]');
  const card = document.querySelector('[data-card]');
  box.scrollLeft = 0;
  const around = box.getBoundingClientRect();
  const at = {clientX: around.right - inside, clientY: around.top + around.height / 2};
  const transfer = new DataTransfer();
  const fire = (target, type, more = {}) => target.dispatchEvent(
    new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: transfer, ...more}));
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  fire(card, 'dragstart');
  fire(box, 'dragover', at);
  const over = setInterval(() => fire(box, 'dragover', at), 40);
  await wait(120);
  const began = [performance.now(), box.scrollLeft];
  await wait(400);
  const ended = [performance.now(), box.scrollLeft];
  clearInterval(over);
  fire(card, 'dragend');
  box.scrollLeft = 0;
  return (ended[1] - began[1]) / (ended[0] - began[0]) * 1000;
}"""

#: The fastest the page scrolls the board by itself, in pixels a second, with room for the
#: half pixel a frame a browser rounds a scroll position by. `app.js` says 700.
CEILING = 800


def own_speed(page: Page, inside: float) -> float:
    return page.evaluate(OWN_SPEED, inside)


def test_the_pages_own_scrolling_makes_room_for_the_browsers(page: Page, live_server, search):
    """Chromium scrolls a box under a drag by itself in the last twenty pixels of its edge,
    the faster the nearer. The page's scrolling was fastest there too, and the two together
    were twice the page's fastest. The page's own speed now rises from the far side of its
    band to its fastest where the browser's band begins, falls from there to nothing half
    way into it, and is its fastest again past the edge, where the browser does nothing:
    measured here by itself, never above its ceiling."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    expect(page.locator("[data-card][draggable=true]").first).to_be_visible()

    speeds = {inside: own_speed(page, inside) for inside in (80, 65, 45, 22, 15, 10, 6, 1, -6)}
    assert speeds[80] == 0, "outside the band"
    assert 0 < speeds[65] < speeds[45] < speeds[22], speeds
    assert speeds[22] > 500, f"fastest where the browser's band begins: {speeds}"
    assert 0 < speeds[15] < speeds[22] * 0.75, f"falling inside the browser's band: {speeds}"
    assert speeds[10] == speeds[6] == speeds[1] == 0, f"nothing where the browser scrolls: {speeds}"
    assert speeds[-6] > 500, f"past the edge, where the browser does nothing: {speeds}"
    assert max(speeds.values()) <= CEILING, speeds


def test_a_narrow_board_scrolls_in_a_narrower_band(page: Page, live_server, search):
    """The band is a fifth of the box at most. Seventy-two pixels at either side of a box
    288 wide is half of it, and a card picked up near its end scrolled the board at once.
    At sixty-five pixels from the edge a wide board scrolls and a narrow one does not; well
    inside its own band the narrow one scrolls as before."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    expect(page.locator("[data-card][draggable=true]").first).to_be_visible()
    assert own_speed(page, 65) > 0

    page.set_viewport_size(NARROWEST)
    page.goto(f"{base}{BOARD}")
    expect(page.locator("[data-card][draggable=true]").first).to_be_visible()
    width = page.locator("[data-board]").bounding_box()["width"]
    assert width / 5 < 60, f"a box {width} wide, whose fifth is less than sixty"
    assert own_speed(page, 65) == 0, "outside a fifth of the box"
    assert own_speed(page, width / 5 + 2) == 0
    assert own_speed(page, 40) > 0, "and inside it"
    assert own_speed(page, 22) > own_speed(page, 40)


def measured(page: Page, inside: float, for_ms: int = 400) -> float:
    """The board's speed with a card held by the mouse `inside` pixels from its end edge:
    everything that moves it, the browser's own scrolling included."""
    page.evaluate("() => { document.querySelector('[data-board]').scrollLeft = 0; }")
    x, y = at_the_edge(page, end=True, inside=inside)
    page.mouse.move(x, y)
    page.wait_for_timeout(80)
    began = page.evaluate(f"() => [performance.now(), ({SCROLL})()]")
    hold_at(page, x, y, for_ms=for_ms)
    ended = page.evaluate(f"() => [performance.now(), ({SCROLL})()]")
    return (ended[1] - began[1]) / (ended[0] - began[0]) * 1000


def test_the_page_and_the_browser_together_scroll_no_faster_than_either(
    page: Page, live_server, search
):
    """With a card held by a real mouse, at every depth from the edge: what the browser
    does alone, measured with the page's scrolling off (reduced motion), and what both do
    together. Together they are never more than the page's own ceiling, except where the
    browser alone is already faster than that, and there they are no more than the browser
    alone. Before, eight pixels from the edge was twice the page's fastest.

    Each speed is the slower of two readings for both and the faster of two for the browser
    alone, so a machine that is busy for one reading does not decide it."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    card = page.locator("[data-board-section='draft'] [data-card]").first
    pick_up(page, card)

    found = {}
    for inside in (30, 22, 16, 12, 8, 4):
        page.emulate_media(reduced_motion="reduce")
        alone = max(measured(page, inside), measured(page, inside))
        page.emulate_media(reduced_motion="no-preference")
        both = min(measured(page, inside), measured(page, inside))
        found[inside] = (round(alone), round(both))
    box = page.locator("[data-board]").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] - 4)
    page.mouse.up()

    assert found[30][1] > 0, f"the page scrolls where the browser does not: {found}"
    for inside, (alone, both) in found.items():
        assert both <= max(CEILING, alone * 1.25 + 40), (
            f"{inside} px from the edge: {both} px/s together, {alone} the browser alone; {found}"
        )


#: A drag the page is told of rather than one the browser makes: a card picked up, and a
#: `dragover` every forty milliseconds near the board's end edge, as a browser sends them
#: while a card is held there. What ends it is then sent, or not sent, by the test.
HELD_AT_THE_EDGE = """() => {
  const box = document.querySelector('[data-board]');
  const card = document.querySelector('[data-card]');
  const around = box.getBoundingClientRect();
  const at = {clientX: around.right - 50, clientY: around.top + around.height / 2};
  const transfer = new DataTransfer();
  const fire = (target, type, more = {}) => target.dispatchEvent(
    new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: transfer, ...more}));
  fire(card, 'dragstart');
  window.heldDrag = {
    card,
    fire,
    over: setInterval(() => fire(box, 'dragover', at), 40),
    quiet() { clearInterval(this.over); },
  };
}"""


def held_at_the_edge(page: Page) -> None:
    page.evaluate(HELD_AT_THE_EDGE)
    page.wait_for_function(f"() => ({SCROLL})() > 40")


def test_the_board_stops_when_the_pointer_leaves_the_window_or_the_drag_is_given_up(
    page: Page, live_server, search
):
    """Dropped is covered above. The other three ends: the pointer leaves the window,
    Escape, and a drag that simply stops being reported, which is how a drag that ended
    somewhere else looks from here.

    Each of these is a drag the page is told of, not one the browser makes. So the Escape
    here drives this file's own `keydown` handler, the one for a browser that hands the key
    to the page; it does not show what a browser does with Escape during a drag of its own
    making, which is to end the drag and say so with `dragend` -- the third case, likewise
    told. The browser's own were checked by hand in Chromium when #315 was reviewed, with a
    real mouse: Escape, letting go outside the window and switching tabs all left nothing
    dimmed and the board answering."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)

    # The pointer leaves the window: a `dragleave` for nothing.
    page.goto(f"{base}{BOARD}")
    held_at_the_edge(page)
    page.evaluate(
        """() => {
            window.heldDrag.quiet();
            window.heldDrag.fire(document.documentElement, 'dragleave', {relatedTarget: null});
        }"""
    )
    assert is_still(page), "out of the window, and still"

    # Escape.
    page.goto(f"{base}{BOARD}")
    held_at_the_edge(page)
    page.evaluate("() => window.heldDrag.quiet()")
    page.keyboard.press("Escape")
    assert is_still(page), "given up, and still"
    expect(page.locator("[data-card]").first).not_to_have_class(re.compile(r"opacity-50"))

    # The browser's own end of a drag, wherever it happened.
    page.goto(f"{base}{BOARD}")
    held_at_the_edge(page)
    page.evaluate(
        "() => { window.heldDrag.quiet(); window.heldDrag.fire(window.heldDrag.card, 'dragend'); }"
    )
    assert is_still(page), "ended, and still"

    # And no word at all: within a second it has stopped by itself.
    page.goto(f"{base}{BOARD}")
    held_at_the_edge(page)
    page.evaluate("() => window.heldDrag.quiet()")
    page.wait_for_timeout(1000)
    assert is_still(page), "nothing reported for a second, and still"
    room = page.evaluate(
        "() => { const b = document.querySelector('[data-board]');"
        " return b.scrollWidth - b.clientWidth; }"
    )
    assert scroll_of(page) < room, "it stopped of itself, and not because it ran out of board"


def test_under_reduced_motion_the_board_scrolls_only_by_hand(page: Page, live_server, search):
    """Somebody who asked for less motion gets none of this: a card held where the board
    scrolls for everybody else moves nothing. The board still scrolls by hand, and the card
    still moves by its menu. (What a browser does of its own accord in the last twenty
    pixels is the browser's; the pointer is held outside them.)"""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{base}{BOARD}")

    card = page.locator("[data-board-section='draft'] [data-card]").first
    pick_up(page, card)
    end = at_the_edge(page, end=True)
    hold_at(page, *end, for_ms=500)
    assert scroll_of(page) == 0
    box = page.locator("[data-board]").bounding_box()
    page.mouse.move(end[0], box["y"] + box["height"] - 4)
    page.mouse.up()
    expect(card).not_to_have_class(re.compile(r"(^|\s)opacity-50(\s|$)"))

    page.evaluate("() => { document.querySelector('[data-board]').scrollLeft = 300; }")
    assert scroll_of(page) == 300, "by hand it scrolls"
    expect(card.locator("select[name=status]")).to_be_visible()

    # The same held card, told to the page rather than made by the browser.
    page.evaluate("() => { document.querySelector('[data-board]').scrollLeft = 0; }")
    page.evaluate(HELD_AT_THE_EDGE)
    page.wait_for_timeout(400)
    assert scroll_of(page) == 0


# ---------------------------------------------- a card that is held is not swapped away


def mark_the_board(page: Page) -> None:
    page.evaluate("() => { document.querySelector('[data-board]').dataset.sameBoard = '1'; }")


def is_the_same_board(page: Page) -> bool:
    return page.evaluate("() => document.querySelector('[data-board]').dataset.sameBoard === '1'")


def test_a_held_card_is_not_swapped_away_by_a_filter_or_a_fold(page: Page, live_server, search):
    """A filter chosen while a card is held, and a heading pressed, ask for nothing: the
    board under the card is the board it was picked up from, unfolded. Let go without a
    move, the board is asked for as the filters then stand."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    mark_the_board(page)
    asked_for = []
    page.on("request", lambda request: asked_for.append(request.url))

    card = page.locator("[data-card]").first
    page.evaluate(
        """(card) => {
            window.heldTransfer = new DataTransfer();
            card.dispatchEvent(new DragEvent('dragstart',
                {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}));
        }""",
        card.element_handle(),
    )
    page.locator("#application-filters select[name=tag]").select_option("remote")
    # Pressed from the page: a browser gives the mouse to the drag while a card is held.
    fold(page, "applied").evaluate("(control) => control.click()")
    page.wait_for_timeout(500)

    assert asked_for == [], "nothing is asked for while the card is held"
    assert is_the_same_board(page)
    expect(page.locator("[data-board-strip]")).to_have_count(0)
    assert "tag=remote" not in page.url and "status=" not in page.url, page.url

    # Let go, having moved nothing: the board catches up with the row of filters.
    page.evaluate(
        """(card) => card.dispatchEvent(new DragEvent('dragend',
            {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}))""",
        card.element_handle(),
    )
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    unfolded(page, person, tag="remote")
    assert not is_the_same_board(page)


def test_an_answer_that_lands_while_a_card_is_held_waits_for_the_card(
    page: Page, live_server, search
):
    """A filter chosen a moment before the card was picked up answers while it is held.
    The answer is not put in the board; when the card is let go the board is asked for
    again, and that answer is."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    mark_the_board(page)

    held = []
    page.route(re.compile(r"/applications/\?.*tag=remote"), lambda route: held.append(route))
    page.locator("#application-filters select[name=tag]").select_option("remote")
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1, "the filter's request is out, and held"

    card = page.locator("[data-card]").first
    page.evaluate(
        """(card) => {
            window.heldTransfer = new DataTransfer();
            card.dispatchEvent(new DragEvent('dragstart',
                {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}));
        }""",
        card.element_handle(),
    )
    with page.expect_response(re.compile(r"tag=remote")) as answer:
        held[0].continue_()
    answer.value.finished()
    page.wait_for_timeout(200)

    assert is_the_same_board(page), "the answer was not put under the held card"
    assert "tag=remote" not in page.url, page.url
    expect(page.locator("#applications-table")).not_to_have_attribute("aria-busy", "true")

    page.unroute(re.compile(r"/applications/\?.*tag=remote"))
    page.evaluate(
        """(card) => card.dispatchEvent(new DragEvent('dragend',
            {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}))""",
        card.element_handle(),
    )
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    unfolded(page, person, tag="remote")
    assert not is_the_same_board(page)


def test_a_filter_refused_while_a_card_was_held_is_kept_by_the_move(
    page: Page, live_server, search
):
    """A tag chosen a moment before a card is picked up answers while it is held, and the
    answer is refused. If the card is then moved, the page is loaded by the card's own
    form, which goes back to an address written with the board: before the tag. The tag
    was lost without a word. The form's address is written again from the filter form as
    it stands, so the page that comes back has the card moved and the tag chosen."""
    from postulo.applications.models import Application, Status

    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    (moved,) = on_the_board(person, status="applied", tag="remote")

    held = []
    waits = re.compile(r"/applications/\?.*tag=remote")
    page.route(waits, lambda route: held.append(route))
    page.locator("#application-filters select[name=tag]").select_option("remote")
    wait_until_held(page, held)

    # Picked up with the mouse, as a hand does; the answer lands under it and is refused.
    card = page.locator(f"[data-card='{moved}']")
    pick_up(page, card)
    with page.expect_response(waits) as answer:
        held[0].continue_()
    answer.value.finished()
    page.unroute(waits)
    page.wait_for_timeout(200)
    assert "tag=remote" not in page.url, "the answer was not put under the held card"

    target = page.locator("[data-board-section='screening']").bounding_box()
    x, y = target["x"] + target["width"] / 2, target["y"] + 120
    page.mouse.move(x, y, steps=6)
    page.mouse.move(x, y + 2)
    with page.expect_navigation():
        page.mouse.up()

    assert Application.objects.get(pk=moved).status == Status.SCREENING
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    expect(page).to_have_url(re.compile(r"[?&]view=board(&|$)"))
    expect(page.locator("#filter-tag")).to_have_value("remote")
    unfolded(page, person, tag="remote")
    assert columns(page)["screening"]["cards"] == [moved]


#: A card picked up and held over a column, as the page is told of it: `dragstart` on the
#: card, then `dragenter` and `dragover` on the column. It is left held.
HELD_OVER = """([card, column]) => {
  window.heldTransfer = new DataTransfer();
  const fire = (target, type) => target.dispatchEvent(new DragEvent(
    type, {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}));
  if (!document.querySelector('[data-card].opacity-50')) fire(card, 'dragstart');
  fire(column, 'dragenter');
  fire(column, 'dragover');
}"""

TINTED = re.compile(r"(^|\s)bg-ink-100(\s|$)")


def test_the_column_a_held_card_is_over_is_tinted_where_it_can_be_seen(
    page: Page, live_server, search
):
    """An open column tints its list of cards under a held card. A strip's list is not on
    the screen, so the strip itself is tinted: without that, nothing says a strip will take
    the card. The card's own column is never tinted, the tint goes when the card leaves,
    and nothing is left tinted when it is let go."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    card = page.locator("[data-board-section='applied'] [data-card]").first
    own = page.locator("[data-board-section='applied']")
    strip = page.locator("[data-board-section='interviewing']")
    colour = "(element) => getComputedStyle(element).backgroundColor"
    plain = strip.evaluate(colour)

    page.evaluate(HELD_OVER, [card.element_handle(), strip.element_handle()])
    expect(strip).to_have_class(TINTED)
    assert strip.evaluate(colour) != plain, "the strip is drawn in another colour"

    # Over another strip: that one, and no longer this.
    other = page.locator("[data-board-section='offer']")
    page.evaluate(
        """([from, to]) => from.dispatchEvent(new DragEvent('dragleave',
            {bubbles: true, cancelable: true, relatedTarget: to}))""",
        [strip.element_handle(), other.element_handle()],
    )
    page.evaluate(HELD_OVER, [card.element_handle(), other.element_handle()])
    expect(other).to_have_class(TINTED)
    expect(strip).not_to_have_class(TINTED)
    expect(strip).to_have_css("background-color", plain)

    # Back over its own column: nothing there is tinted.
    page.evaluate(HELD_OVER, [card.element_handle(), own.element_handle()])
    expect(own).not_to_have_class(TINTED)
    expect(own.locator("[data-board-column]")).not_to_have_class(TINTED)

    page.evaluate(
        """(card) => card.dispatchEvent(new DragEvent('dragend',
            {bubbles: true, cancelable: true, dataTransfer: window.heldTransfer}))""",
        card.element_handle(),
    )
    expect(page.locator("[data-board] .bg-ink-100")).to_have_count(0)

    # An open column that is not the card's own tints its list, and not itself.
    page.goto(f"{base}{BOARD}")
    unfolded(page, person)
    card = page.locator("[data-board-section='applied'] [data-card]").first
    column = page.locator("[data-board-section='screening']")
    page.evaluate(HELD_OVER, [card.element_handle(), column.element_handle()])
    expect(column.locator("[data-board-column]")).to_have_class(TINTED)
    expect(column).not_to_have_class(TINTED)


#: The counts on the page: each column's figure, the cards that can be seen in it, the
#: figure under the page's title and the words it is said in.
COUNTED = """() => ({
  columns: Object.fromEntries([...document.querySelectorAll('[data-board-section]')].map((s) => [
    s.dataset.boardSection,
    [Number(s.querySelector('[data-column-count]').textContent),
     [...s.querySelectorAll('[data-card]')].filter((c) => c.checkVisibility())
       .map((c) => Number(c.dataset.card)).sort((a, b) => a - b)],
  ])),
  page: document.querySelector('#applications-count [data-count-words]')
    .textContent.trim().replace(/\\s+/g, ' '),
})"""


@pytest.mark.parametrize(
    "address",
    ["&status=applied&quiet=1", "&quiet=1", "&status=applied", "&status=applied,interviewing", ""],
    ids=["folded and gone quiet", "gone quiet", "folded", "two columns open", "open"],
)
def test_what_a_drop_counts_is_what_the_page_comes_back_with(
    page: Page, live_server, search, address
):
    """The card moves at once and the counts with it, and the page the move loads is the
    truth: so the two are compared. Under *Gone quiet* a card that is moved is quiet no
    longer, so it leaves the board and the column it went to gains nothing; and a card
    that leaves what the address asks for -- that, or out of a folded board's open column
    into a strip -- is one fewer under the page's title, and one moved between two open
    columns is not (#709). Each was wrong for the length of the move."""
    from postulo.applications.models import Application, Status

    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}{address}")
    wanted = {"quiet": True} if "quiet" in address else {}
    moved = on_the_board(person, status="applied", **wanted)[0]
    before = page.evaluate(COUNTED)
    status = re.search(r"status=([^&]*)", address)
    scope = {**wanted, **({"status": status.group(1)} if status else {})}
    assert before["page"] == in_words(len(asked(person, **scope)))

    # The card's form is kept from sending, so the page as the drop left it can be read.
    page.evaluate(
        """() => {
            window.keptBack = (event) => {
                window.sentForm = event.target;
                event.preventDefault();
            };
            document.addEventListener('submit', window.keptBack, true);
        }"""
    )
    card = page.locator(f"[data-card='{moved}']")
    target = page.locator("[data-board-section='interviewing']")
    drag(page, card.element_handle(), target.element_handle())
    dropped = page.evaluate(COUNTED)
    assert dropped != before
    assert dropped["columns"]["applied"][0] == before["columns"]["applied"][0] - 1
    if "interviewing" in address:
        assert dropped["page"] == before["page"], "still what the address asks for"

    with page.expect_navigation():
        page.evaluate(
            """() => {
                document.removeEventListener('submit', window.keptBack, true);
                window.sentForm.requestSubmit();
            }"""
        )
    assert Application.objects.get(pk=moved).status == Status.INTERVIEWING
    expect(page.locator(f"[data-board-section='applied'] [data-card='{moved}']")).to_have_count(0)
    assert dropped == page.evaluate(COUNTED), "what the drop said is what the server says"


def test_a_drag_whose_end_was_never_said_does_not_hold_the_board_for_ever(
    page: Page, live_server, search
):
    """A card counted as held keeps the board from being redrawn, so a drag that ended
    without its `dragend` would leave the filters dead until the page was loaded again. A
    browser gives the page no mouse event while something is dragged: the first one that
    arrives says the drag is over."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    card = page.locator("[data-card]").first
    card.evaluate(
        """(card) => card.dispatchEvent(new DragEvent('dragstart',
            {bubbles: true, cancelable: true, dataTransfer: new DataTransfer()}))"""
    )
    expect(card).to_have_class(re.compile(r"(^|\s)opacity-50(\s|$)"))

    page.mouse.move(300, 300)
    page.mouse.move(320, 310)
    expect(card).not_to_have_class(re.compile(r"(^|\s)opacity-50(\s|$)"))

    page.locator("#application-filters select[name=tag]").select_option("remote")
    expect(page).to_have_url(re.compile(r"[?&]tag=remote(&|$)"))
    unfolded(page, person, tag="remote")


# ----------------------------------------------------------- a status with no column


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts on", "scripts off"])
def test_a_status_with_no_column_folds_every_column_and_says_where_it_is(
    browser: Browser, live_server, search, scripts
):
    """*Rejected* is settled, and the board has no column for it. The address still means
    what it means in the table, so no column is open: every one is a strip, and the page
    says how many applications the status matches, with a link to them in the table. *All
    columns* is the way back, and a strip folds the board to its own column."""
    person = search["applicant"]
    base = live_server.url
    context = browser.new_context(java_script_enabled=scripts, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}{BOARD}&status=rejected")
        folded_to(page, person, "rejected")
        assert cards_shown(page) == []
        said = page.locator("[data-off-board]")
        expect(said).to_have_attribute("data-off-board", str(len(asked(person, status="rejected"))))
        assert page_count(page) == len(asked(person, status="rejected"))

        page.locator("#board-fold-applied").click()
        says_status_once(page, "applied")
        folded_to(page, person, "applied")
        expect(page.locator("[data-off-board]")).to_have_count(0)

        page.goto(f"{base}{BOARD}&status=rejected")
        page.locator("#board-unfold").click()
        says_no_status(page)
        unfolded(page, person)

        page.goto(f"{base}{BOARD}&status=rejected")
        page.locator("[data-off-board] a").click()
        expect(page).to_have_url(re.compile(r"[?&]view=table(&|$)"))
        says_status_once(page, "rejected")
        assert rows_shown(page) == asked(person, status="rejected")
    finally:
        context.close()


def test_the_switch_carries_the_status_between_the_shapes(page: Page, live_server, search):
    """One address, one meaning: the table narrowed to *Applied* is the board folded to
    *Applied*, and the switch carries it either way."""
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}/applications/?status=applied&tag=remote")
    assert rows_shown(page) == asked(person, status="applied", tag="remote")

    page.locator("[data-shape-switch]").get_by_role("button", name="Board", exact=True).click()
    says_status_once(page, "applied")
    folded_to(page, person, "applied", tag="remote")

    page.locator("[data-shape-switch]").get_by_role("button", name="Table", exact=True).click()
    says_status_once(page, "applied")
    expect(page.locator("#applications-table tbody tr")).to_have_count(
        len(asked(person, status="applied", tag="remote"))
    )
    assert rows_shown(page) == asked(person, status="applied", tag="remote")
    expect(page.locator("#filter-status")).to_have_value("applied")


# --------------------------------------------------------------------------- a phone

#: Every strip, measured: whether its name is inside the strip and on one line, and how
#: large it is drawn. Asked of the browser, in whatever font it is drawing in.
STRIPS = """() => [...document.querySelectorAll('[data-board-strip]')].map((strip) => {
  const control = strip.querySelector('[data-board-fold]');
  const name = control.querySelector('span');
  const s = strip.getBoundingClientRect();
  const c = control.getBoundingClientRect();
  const n = name.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(name);
  const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.left))).size;
  const within = (inner) => inner.left >= s.left - 0.5 && inner.right <= s.right + 0.5
      && inner.top >= s.top - 0.5 && inner.bottom <= s.bottom + 0.5;
  return {
    status: strip.dataset.boardSection,
    name: name.textContent.trim(),
    width: Math.round(s.width),
    inside: within(n) && within(c),
    lines,
    upright: getComputedStyle(name).writingMode,
    size: parseFloat(getComputedStyle(name).fontSize),
    cut: name.scrollHeight > name.clientHeight + 1 || name.scrollWidth > name.clientWidth + 1
      || control.scrollHeight > control.clientHeight + 1,
    target: [Math.round(c.width), Math.round(c.height)],
    count: strip.querySelector('[data-column-count]').checkVisibility(),
  };
})"""

#: Whether the open column is inside the board's scroll box, side to side.
OPEN_COLUMN_IN_VIEW = """() => {
  const box = document.querySelector('[data-board]').getBoundingClientRect();
  const open = document.querySelector('[data-board-section]:not([data-board-strip])')
    .getBoundingClientRect();
  return open.left >= box.left - 1 && open.right <= box.right + 1;
}"""


@pytest.mark.parametrize("language", ["en", "de", "el"])
@pytest.mark.parametrize("width", [320, 768, 1024])
def test_folded_the_page_does_not_scroll_sideways_and_no_strip_is_cut(
    page: Page, live_server, search, language, width
):
    """Reflow (SC 1.4.10). The strips' names are written down the strip, so a strip is as
    narrow as a line of text and as tall as its name is long: nothing clips it, in German
    or in Greek. The board stays in its scroll box, and the open column -- the last one
    here, the furthest from where the box starts -- is brought into it."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{base}{BOARD}&status=offer")
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 1)

    result = page.evaluate(SCROLLS_SIDEWAYS)
    assert not result["reached"], f"{language} at {width}: {result}"

    strips = page.evaluate(STRIPS)
    assert [strip["status"] for strip in strips] == statuses()[:-1]
    for strip in strips:
        assert strip["name"], strip
        assert strip["upright"] == "vertical-rl", strip
        assert strip["inside"] and not strip["cut"], (language, width, strip)
        assert strip["lines"] == 1, (language, width, strip)
        assert strip["size"] >= 14, strip
        assert min(strip["target"]) >= 24, strip
        assert strip["count"], strip

    assert page.evaluate(OPEN_COLUMN_IN_VIEW), "the open column is in the box"


def test_without_a_script_on_a_phone_the_folded_board_stays_in_its_box(
    browser: Browser, live_server, search
):
    """Nothing brings the open column into the box then, and nothing has to: the box is
    inside the screen and scrolls, and the column is in it, a sideways scroll away."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=NARROWEST)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}{BOARD}&status=offer")
        expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 1)
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
        box = page.locator("[data-board]").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= NARROWEST["width"], box
        for strip in page.evaluate(STRIPS):
            assert strip["inside"] and not strip["cut"] and strip["lines"] == 1, strip
        reach = page.locator("[data-board]").evaluate(
            "(box) => [box.scrollWidth, box.clientWidth, getComputedStyle(box).overflowX]"
        )
        assert reach[0] > reach[1] and reach[2] == "auto", reach
        expect(page.locator("[data-board-section='offer'] [data-card]")).to_have_count(0)
        expect(page.locator("[data-board-section='offer'] [data-empty]")).to_be_attached()
    finally:
        context.close()


#: Every list in the board's row of filters that is narrower than its widest choice, and
#: every control of the row whose words do not fit it or that is off the screen.
ROW_CUT_SHORT = """() => {
  const short = [];
  const limit = document.documentElement.clientWidth;
  const form = document.querySelector('#application-filters');
  for (const list of form.querySelectorAll('select')) {
    // Where the script built a button for the select (#301), the button is what a person
    // sees: it is whole when nothing in it is cut.
    const built = list.nextElementSibling;
    const button = built && built.matches('[data-select]')
      ? built.querySelector('[data-select-trigger]')
      : null;
    if (button) {
      for (const part of [button, ...button.querySelectorAll('*')]) {
        if (part.scrollWidth > part.clientWidth + 0.5 && part.clientWidth > 0) {
          short.push(`${list.id}'s button cuts "${part.textContent.trim().slice(0, 30)}"`);
          break;
        }
      }
      continue;
    }
    const alone = list.cloneNode(true);
    for (const name of ['id', 'name', 'form', 'class']) alone.removeAttribute(name);
    alone.style.cssText = 'position: absolute; visibility: hidden; width: auto; min-width: 0;';
    list.parentElement.appendChild(alone);
    const wants = alone.getBoundingClientRect().width;
    alone.remove();
    const has = list.getBoundingClientRect().width;
    if (has < wants - 0.5) {
      short.push(`${list.id} is ${Math.round(has)} wide and wants ${Math.round(wants)}`);
    }
  }
  for (const control of form.querySelectorAll('select, button, a, label')) {
    if (!control.checkVisibility()) continue;
    const box = control.getBoundingClientRect();
    const what = `${control.tagName} "${control.textContent.trim().slice(0, 30)}"`;
    if (box.left < -0.5 || box.right > limit + 0.5) {
      short.push(`${what} is off the screen`);
    }
    if (control.tagName !== 'SELECT' && control.scrollWidth > control.clientWidth + 1) {
      short.push(`${what} is cut`);
    }
  }
  return short;
}"""


@pytest.mark.parametrize("language", ["de", "el"])
@pytest.mark.parametrize("width", [320, 768, 1024])
def test_nothing_in_the_boards_row_of_filters_is_cut(
    page: Page, live_server, search, language, width
):
    """The status list was the row's widest control and cut "Zur Kenntnis genommen" in
    German. It is gone; what is left -- outcome, tag, gone quiet, the two buttons -- shows
    all of what it holds, folded and not."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": width, "height": 800})
    for address in (BOARD, f"{BOARD}&status=applied&state=open&tag=dream-job&quiet=1"):
        page.goto(f"{base}{address}")
        expect(page.locator("#application-filters select[name=status]")).to_have_count(0)
        expect(page.locator("#filter-state")).to_be_visible()
        assert page.evaluate(ROW_CUT_SHORT) == [], (language, width, address)
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], (language, width, address)


# -------------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_board_passes_axe_open_and_folded(live_server, page: Page, axe_source, search, scheme):
    """Open, folded by address, folded in place -- where the headings are called buttons --
    folded with no column open, and folded on a phone, in both themes."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    failures = []

    def look(what: str) -> None:
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{what} ({scheme})", found))

    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}")
    expect(page.locator("[data-board-fold][role=button]")).to_have_count(len(statuses()))
    look("the board, every column open")

    fold(page, "applied").click()
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 1)
    expect(fold(page, "applied")).to_be_focused()
    look("the board, folded in place")

    page.goto(f"{base}{BOARD}&status=interviewing&tag=dream-job")
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 1)
    look("the board, folded by its address")

    page.goto(f"{base}{BOARD}&status=applied,interviewing")
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 2)
    look("the board, two columns open")

    page.goto(f"{base}{BOARD}&status=rejected")
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()))
    look("the board, folded with no column open")

    page.set_viewport_size(NARROWEST)
    page.goto(f"{base}{BOARD}&status=offer")
    expect(page.locator("[data-board-strip]")).to_have_count(len(statuses()) - 1)
    look("the board, folded on a phone")

    assert not failures, "\n\n".join(failures)


# ------------------------------------------------------------ centred, and in motion (#709)

#: Where the columns are in the board's box, on the screen: the room between the box's left
#: edge and the first column drawn, and between the last and its right edge; whether the box
#: scrolls; and how far the middle of each column is from the middle of the box.
PLACED = """() => {
  const box = document.querySelector('[data-board]');
  const b = box.getBoundingClientRect();
  const left = b.left + box.clientLeft;
  const right = left + box.clientWidth;
  const drawn = [...box.querySelectorAll('[data-board-section]')]
    .map((section) => [section.dataset.boardSection, section.getBoundingClientRect()]);
  const lefts = drawn.map(([, at]) => at.left);
  const rights = drawn.map(([, at]) => at.right);
  return {
    before: Math.min(...lefts) - left,
    after: right - Math.max(...rights),
    scrolls: box.scrollWidth > box.clientWidth + 1,
    off: Object.fromEntries(drawn.map(([status, at]) =>
      [status, (at.left + at.right) / 2 - (left + right) / 2])),
  };
}"""


def still(page: Page) -> None:
    """Nothing on the board is moving: the swap has settled, its columns have arrived and
    the copies of what they held are gone, and the box has stopped scrolling."""
    settled(page)
    page.wait_for_function(
        """() => !document.querySelector('[data-fold-copy]')
          && !document.querySelector('[data-board]').getAnimations({subtree: true}).length"""
    )
    # The box's scrolling is a frame loop of its own, as long as the columns' motion.
    first = page.evaluate(SCROLL)
    page.wait_for_timeout(300)
    assert page.evaluate(SCROLL) == first, "the box is still scrolling"


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_board_that_fits_in_its_box_is_centred_in_it(page: Page, live_server, search, language):
    """Folded, the board is narrower than the screen, and it sat against the start of its
    box with the rest of the width empty. It is in the middle of the box, after a fold in
    place as on arrival, in either direction of writing. A board wider than its box starts
    at the box's edge -- centred, its first column would be past an edge no scrolling
    reaches."""
    set_language(search, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    placed = page.evaluate(PLACED)
    assert not placed["scrolls"] and placed["before"] > 100, placed
    assert abs(placed["before"] - placed["after"]) <= 1, placed

    fold(page, "interviewing").click()
    expect(fold(page, "interviewing")).to_have_attribute("aria-expanded", "true")
    still(page)
    placed = page.evaluate(PLACED)
    assert not placed["scrolls"] and placed["before"] > 100, placed
    assert abs(placed["before"] - placed["after"]) <= 1, placed

    page.locator("#board-unfold").click()
    expect(page.locator("[data-board-strip]")).to_have_count(0)
    still(page)
    placed = page.evaluate(PLACED)
    assert placed["scrolls"], placed
    start = placed["after"] if language == "ar" else placed["before"]
    assert abs(start + abs(scroll_of(page))) <= 1, ("the first column at the box's edge", placed)


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_open_columns_are_brought_to_the_middle_of_a_box_they_do_not_fit(
    page: Page, live_server, search, language
):
    """On a phone the folded board is wider than its box. The open column is in the middle
    of the box when the page arrives; a strip pressed opens a column the two do not fit
    beside, and the box brings the one pressed to the middle; one of them folded leaves the
    other, which fits, in the middle again."""
    set_language(search, language)
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 390, "height": 740})
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")
    placed = page.evaluate(PLACED)
    assert placed["scrolls"] and abs(placed["off"]["applied"]) <= 1, placed

    fold(page, "interviewing").click()
    folded_to(page, person, "applied,interviewing")
    still(page)
    placed = page.evaluate(PLACED)
    assert abs(placed["off"]["interviewing"]) <= 1, placed

    fold(page, "applied").click()
    folded_to(page, person, "interviewing")
    still(page)
    placed = page.evaluate(PLACED)
    assert abs(placed["off"]["interviewing"]) <= 1, placed
    expect(fold(page, "applied")).to_be_focused()


#: Every frame from now until the flag is read: the width of one column, found afresh each
#: time since a fold replaces the board, and what the copy of an old column was, if one is
#: on the screen.
WATCH = """(status) => {
  window.watched = {widths: [], copies: []};
  const began = performance.now();
  const look = () => {
    const section = document.querySelector(`[data-board-section='${status}']`);
    window.watched.widths.push(section ? section.getBoundingClientRect().width : null);
    for (const copy of document.querySelectorAll('[data-fold-copy]')) {
      window.watched.copies.push({
        hidden: copy.getAttribute('aria-hidden'),
        inert: copy.inert,
        reachable: copy.querySelectorAll('[id], [name], [data-board-fold], [data-card]').length,
      });
    }
    if (performance.now() - began < 2500) {
      requestAnimationFrame(look);
    } else {
      window.watched.done = true;
    }
  };
  requestAnimationFrame(look);
}"""


def watched(page: Page, status: str, press) -> dict:
    page.evaluate(WATCH, status)
    press()
    page.wait_for_function("() => window.watched && window.watched.done", timeout=10_000)
    return page.evaluate("() => window.watched")


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_a_column_opens_and_folds_in_motion_unless_less_motion_is_asked(
    page: Page, live_server, search, motion
):
    """A strip pressed grows to a column's width, and a column folded shrinks to a strip's,
    frame by frame, instead of the board jumping between two drawings. What a column held
    fades out over what it holds now, as a copy nobody can reach or hear, gone once the
    column has arrived. Somebody who asked for less motion sees the board drawn at once."""
    page.emulate_media(reduced_motion=motion)
    person = search["applicant"]
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(WIDE)
    page.goto(f"{base}{BOARD}&status=applied")
    folded_to(page, person, "applied")

    def between(widths: list) -> tuple[float, float, list]:
        drawn = [width for width in widths if width]
        start, end = drawn[0], drawn[-1]
        low, high = sorted((start, end))
        return start, end, [width for width in drawn if low + 2 < width < high - 2]

    seen = watched(page, "interviewing", lambda: fold(page, "interviewing").click())
    folded_to(page, person, "applied,interviewing")
    start, end, on_the_way = between(seen["widths"])
    assert start < 60 and end > 250, (start, end)
    if motion == "reduce":
        assert on_the_way == [] and seen["copies"] == [], seen
    else:
        assert on_the_way, ("a frame between the two widths", seen["widths"])
        assert on_the_way == sorted(on_the_way), "it grows, and only grows"
        assert seen["copies"], "what the strip held fades out"
        for copy in seen["copies"]:
            assert copy == {"hidden": "true", "inert": True, "reachable": 0}, copy
    expect(page.locator("[data-fold-copy]")).to_have_count(0)
    still(page)

    seen = watched(page, "applied", lambda: fold(page, "applied").click())
    folded_to(page, person, "interviewing")
    start, end, on_the_way = between(seen["widths"])
    assert start > 250 and end < 60, (start, end)
    if motion == "reduce":
        assert on_the_way == [] and seen["copies"] == [], seen
    else:
        assert on_the_way, ("a frame between the two widths", seen["widths"])
        assert on_the_way == sorted(on_the_way, reverse=True), "it shrinks, and only shrinks"
    expect(page.locator("[data-fold-copy]")).to_have_count(0)
    # Arrived, nothing is left of the motion on the columns: their widths are the page's,
    # and nothing in them has a style of its own for htmx to carry into the next board.
    assert page.evaluate(
        """() => [...document.querySelectorAll('[data-board-section]')].every((section) =>
          !section.style.width && !section.style.overflow
          && !section.style.getPropertyValue('--board-arrives')
          && !section.querySelector('[id][style]'))"""
    )
    assert page.evaluate(
        """() => { const box = document.querySelector('[data-board]');
          return !box.style.height && !box.style.overflowY && !box.style.columnGap; }"""
    )
