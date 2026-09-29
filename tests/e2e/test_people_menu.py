"""The People table, at a width where it used to scroll sideways and at one where it cannot.

Server settings → People needed 967 pixels in a card that is about 730 whatever the window
does, so it scrolled at every width — a 1440-pixel desktop as much as a phone. The actions
column was 335 of those pixels: four buttons spelled out in words, laid end to end.

They are a menu now, and below the md breakpoint each row becomes a card. Both halves need a
browser to be checked honestly: whether the table actually stops overflowing is a question
about layout, and whether a menu opens and submits is a question about events.

**The menu at the foot of the table (#310).** The table sits in a box that scrolls sideways,
and a box that scrolls on one axis clips on both: the menu used to be laid out inside it, so
the last row's menu was cut off by the table's own edge and its lower items could be reached
only by scrolling the table. The menu is a popover now, drawn in the top layer and tied to
its trigger, and what is checked here is the promise rather than the mechanism: with the
last row at the foot of the window, every item of its menu is inside the window, drawn, and
the thing a click at its centre would land on -- in the wide layout and on a phone, where
the browser ties the panel to its trigger and where the script has to.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.e2e.conftest import EMAIL, PASSWORD

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e

WIDE = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
LAYOUTS = {"wide": WIDE, "phone": PHONE}

#: Enough people that the table is taller than either window.
CROWD = 14

#: What a browser with no anchor positioning says when asked: the script then places the
#: panel itself. Chromium has anchor positioning, so the question is answered for it.
NO_ANCHORS = """(() => {
  const asked = CSS.supports.bind(CSS);
  CSS.supports = (...question) =>
    question.join(" ").includes("position-") ? false : asked(...question);
})();"""

#: Where an open panel is, and whether each of its items can be reached where it is drawn.
REACHABLE = """(panel) => {
  const trigger = document.querySelector('[popovertarget="' + CSS.escape(panel.id) + '"]');
  const width = document.documentElement.clientWidth;
  const height = document.documentElement.clientHeight;
  const box = panel.getBoundingClientRect();
  const from = trigger.getBoundingClientRect();
  const items = [...panel.querySelectorAll('[role="menuitem"]')].map((item) => {
    const r = item.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return {
      text: item.textContent.trim(),
      inside: r.top >= 0 && r.left >= 0 && r.bottom <= height && r.right <= width,
      drawn: item.checkVisibility({visibilityProperty: true, opacityProperty: true}),
      hit: !!hit && item.contains(hit),
      instead: hit && !item.contains(hit) ? hit.outerHTML.slice(0, 100) : null,
    };
  });
  const rtl = getComputedStyle(panel).direction === "rtl";
  return {
    open: panel.matches(":popover-open"),
    items,
    below: box.top >= from.bottom - 1 && box.top - from.bottom <= 8,
    above: box.bottom <= from.top + 1 && from.top - box.bottom <= 8,
    endsWithTrigger: Math.abs(rtl ? box.left - from.left : box.right - from.right) <= 1,
  };
}"""


#: Whether an open panel touches its trigger, above or below it.
BESIDE = """(panel) => {
  const from = document.querySelector('[popovertarget="' + CSS.escape(panel.id) + '"]')
    .getBoundingClientRect();
  const box = panel.getBoundingClientRect();
  return Math.abs(box.top - from.bottom) <= 8 || Math.abs(from.top - box.bottom) <= 8;
}"""


@pytest.fixture
def administrator(db):
    """An administrator, and somebody to administer."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    User = get_user_model()
    admin = User.objects.create_user(
        email=EMAIL, password=PASSWORD, first_name="Alex", last_name="Morgan", is_staff=True
    )
    EmailAddress.objects.create(user=admin, email=EMAIL, verified=True, primary=True)
    other = User.objects.create_user(
        email="jordan.mccafferty@a-fairly-long-domain.example",
        password=PASSWORD,
        first_name="Jordan",
        last_name="McCafferty",
    )
    EmailAddress.objects.create(user=other, email=other.email, verified=True, primary=True)
    return admin, other


@pytest.fixture
def crowd(administrator):
    """A table taller than the window. Their usernames sort after everybody else's, so the
    last of them is the table's last row."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return [
        User.objects.create_user(
            email=f"person{number:02}@example.org",
            username=f"zz-person-{number:02}",
            first_name="Person",
            last_name=f"Number {number}",
        )
        for number in range(1, CROWD + 1)
    ]


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()


def overflow(page: Page) -> int:
    """How far the table sticks out of the card that holds it."""
    return page.evaluate("""() => {
        const card = document.querySelector('.card.scroll-x');
        return Math.round(card.scrollWidth - card.clientWidth);
    }""")


def to_the_foot(page: Page, row) -> None:
    """The row at the foot of the window, and the table's box scrolled as far as it goes --
    the place the menu was cut off from."""
    row.evaluate("row => row.scrollIntoView({block: 'end'})")
    page.evaluate("""() => {
        const box = document.querySelector('.card.scroll-x');
        box.scrollLeft = box.scrollWidth;
    }""")


def settled(page: Page, panel, side: str) -> dict:
    """Where the panel is once it has been placed. The browser places an anchored one as it
    draws it; the script turns one over on `toggle`, a task after the click, so this waits a
    moment for the side it should be on -- and reports wherever it is if it never gets there.
    """
    try:
        page.wait_for_function(
            f"(panel) => ({REACHABLE})(panel).{side}", arg=panel.element_handle(), timeout=3000
        )
    except PlaywrightTimeoutError:
        pass
    return panel.evaluate(REACHABLE)


def assert_reachable(found: dict, where: str) -> None:
    assert found["open"], f"{where}: the menu did not open"
    assert found["items"], f"{where}: no items"
    for item in found["items"]:
        assert item["inside"], f"{where}: {item['text']!r} is outside the window"
        assert item["drawn"], f"{where}: {item['text']!r} is not drawn"
        assert item["hit"], f"{where}: a click on {item['text']!r} lands on {item['instead']}"
    assert found["endsWithTrigger"], f"{where}: the panel is not lined up with its trigger"


@pytest.mark.parametrize("width", [1440, 1024, 768])
def test_the_table_no_longer_scrolls_sideways(page: Page, live_server, administrator, width):
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/server/people/")

    assert overflow(page) == 0, f"still {overflow(page)}px of sideways scroll at {width}"


def test_a_phone_gets_cards_instead_of_columns(page: Page, live_server, administrator):
    """Five columns have nowhere to go at 390 pixels, so the rows stop being rows."""
    sign_in(page, live_server.url)
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/server/people/")

    assert overflow(page) == 0
    # The header row is what goes away; the labels on each cell replace it.
    expect(page.locator("thead")).to_be_hidden()
    assert page.locator("td[data-label='Role']").first.evaluate(
        "cell => getComputedStyle(cell, '::before').content.includes('Role')"
    )


@pytest.mark.parametrize("placed_by", ["anchor", "script"])
@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_last_rows_menu_is_whole_and_every_item_can_be_clicked(
    page: Page, live_server, crowd, layout, placed_by
):
    """The defect (#310): the last row's menu, with the table scrolled to it, cut off by the
    box the table scrolls in. Now every item is inside the window, drawn and on top where it
    is drawn. With no room below the row, the panel opens upwards; the first row, with room
    below it, still opens downwards. `script` is a browser with no anchor positioning, where
    `app.js` places the panel by the same rules."""
    if placed_by == "script":
        page.context.add_init_script(NO_ANCHORS)
    sign_in(page, live_server.url)
    page.set_viewport_size(LAYOUTS[layout])
    page.goto(f"{live_server.url}/server/people/")
    if placed_by == "script":
        assert page.evaluate("() => !CSS.supports('position-area: block-end')")

    rows = page.locator("tbody tr")
    first = rows.first
    first.locator("button[popovertarget]").click()
    found = settled(page, first.locator("[popover]"), "below")
    assert_reachable(found, f"first row, {layout}, {placed_by}")
    assert found["below"], "with room below it, the menu opens downwards"
    page.keyboard.press("Escape")

    last = rows.last
    assert last.get_attribute("data-person") == crowd[-1].username
    to_the_foot(page, last)
    last.locator("button[popovertarget]").click()
    found = settled(page, last.locator("[popover]"), "above")
    assert_reachable(found, f"last row, {layout}, {placed_by}")
    assert found["above"], "at the foot of the window it opens upwards, beside its trigger"

    # And scrolling the page takes the panel along with its trigger rather than leaving it
    # where the trigger used to be.
    before = last.bounding_box()["y"]
    page.evaluate("() => window.scrollBy(0, 60)")
    page.wait_for_function(
        "([row, before]) => row.getBoundingClientRect().top < before - 30",
        arg=[last.element_handle(), before],
    )
    page.wait_for_function(BESIDE, arg=last.locator("[popover]").element_handle())
    assert last.locator("[popover]").evaluate(REACHABLE)["open"]

    last.get_by_role("menuitem", name="Make administrator").click()
    expect(page.locator(f"tr[data-person='{crowd[-1].username}']")).to_contain_text("Administrator")


def test_a_panel_the_browser_leaves_unanchored_is_placed_by_the_script(
    page: Page, live_server, crowd
):
    """`CSS.supports` can say a browser has anchor positioning; it cannot say whether that
    browser takes a `popovertarget` for the panel's anchor, which is the half the stylesheet
    relies on. So when a menu first opens the script looks, and a panel that is not beside
    its trigger is placed by hand from then on. A panel pointed at an anchor that does not
    exist is what a browser without that half draws."""
    sign_in(page, live_server.url)
    page.set_viewport_size(WIDE)
    page.goto(f"{live_server.url}/server/people/")
    page.evaluate("""() => document.querySelectorAll('[data-popover][popover]')
        .forEach((panel) => panel.style.setProperty('position-anchor', '--nowhere'))""")

    last = page.locator("tbody tr").last
    to_the_foot(page, last)
    last.locator("button[popovertarget]").click()
    found = settled(page, last.locator("[popover]"), "above")
    assert_reachable(found, "unanchored")
    assert found["above"], "turned over, beside its trigger"

    page.keyboard.press("Escape")
    first = page.locator("tbody tr").first
    first.scroll_into_view_if_needed()
    first.locator("button[popovertarget]").click()
    found = settled(page, first.locator("[popover]"), "below")
    assert_reachable(found, "unanchored, the next menu")
    assert found["below"]


def test_the_menu_opens_and_works_with_scripts_off(browser: Browser, live_server, crowd):
    """The browser opens a popover for its `popovertarget` and closes it on Escape and on a
    click elsewhere, so none of it waits for a script."""
    context = browser.new_context(java_script_enabled=False, viewport=WIDE)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/server/people/")
        last = page.locator("tbody tr").last
        to_the_foot(page, last)
        panel = last.locator("[popover]")

        last.locator("button[popovertarget]").click()
        assert_reachable(panel.evaluate(REACHABLE), "with scripts off")
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()

        last.locator("button[popovertarget]").click()
        page.mouse.click(5, 300)
        expect(panel).to_be_hidden()

        last.locator("button[popovertarget]").click()
        last.get_by_role("menuitem", name="Make administrator").click()
        crowd[-1].refresh_from_db()
        assert crowd[-1].is_staff, "the form inside the menu did not submit"
    finally:
        context.close()


def test_the_menu_opens_and_its_actions_still_work(page: Page, live_server, administrator):
    _admin, other = administrator
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/people/")

    row = page.locator(f"tr[data-person='{other.username}']")

    expect(row.get_by_role("menuitem", name="Change username")).to_be_hidden()
    row.locator("button[popovertarget]").click()
    expect(row.get_by_role("menuitem", name="Change username")).to_be_visible()

    row.get_by_role("menuitem", name="Make administrator").click()

    other.refresh_from_db()
    assert other.is_staff, "the form inside the menu did not submit"


def test_the_menu_answers_to_the_arrow_keys(page: Page, live_server, administrator):
    """It says it is a menu (#262), so it has to behave like one: ArrowDown on the trigger
    opens it onto the first item, End goes to the last, ArrowDown wraps, and Escape closes
    it and puts focus back where it came from."""
    _admin, other = administrator
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/people/")

    row = page.locator(f"tr[data-person='{other.username}']")
    trigger = row.locator("button[popovertarget]")
    panel = row.locator("[popover]")
    trigger.focus()
    page.keyboard.press("ArrowDown")

    def focused() -> str:
        return page.evaluate("() => document.activeElement.textContent.trim()")

    expect(panel).to_be_visible()
    assert focused() == "Change username"
    assert panel.evaluate(REACHABLE)["below"], "opened by a key, it is still by its trigger"
    page.keyboard.press("End")
    assert focused() == "Delete account"
    page.keyboard.press("ArrowDown")
    assert focused() == "Change username", "wraps"
    page.keyboard.press("ArrowUp")
    assert focused() == "Delete account", "and back"
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(trigger).to_be_focused()

    # Enter opens it as well, and Tab walks into it: the panel follows its trigger.
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    page.keyboard.press("Tab")
    assert focused() == "Change username"


def test_only_one_menu_is_ever_open(page: Page, live_server, administrator):
    """Two panels overlapping each other is nobody's idea of a menu."""
    admin, other = administrator
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/people/")

    page.locator(f"tr[data-person='{admin.username}'] button[popovertarget]").click()
    # From the keyboard, because the first panel lies over the second row's trigger: a
    # pointer aimed there lands on the menu, which is what a panel over the page is for.
    page.locator(f"tr[data-person='{other.username}'] button[popovertarget]").focus()
    page.keyboard.press("Enter")

    assert page.evaluate("() => document.querySelectorAll(':popover-open').length") == 1
    expect(page.locator(f"tr[data-person='{other.username}'] [popover]")).to_be_visible()


# ------------------------------------------------------------------ the pictures (#310)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_has_no_violations_with_the_pictures_and_a_menu_open(
    page: Page,
    live_server,
    crowd,
    axe_source,  # noqa: F811
    scheme,
):
    """The column of pictures, and the last row's menu open over the page, in both themes
    and both layouts."""
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    failures = []
    for layout, size in LAYOUTS.items():
        page.set_viewport_size(size)
        page.goto(f"{live_server.url}/server/people/")
        expect(page.locator("td[data-avatar] .avatar").first).to_be_visible()
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"people, {layout} ({scheme})", found))
        last = page.locator("tbody tr").last
        to_the_foot(page, last)
        last.locator("button[popovertarget]").click()
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"people with a menu open, {layout} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_the_pictures_reflow_at_320_pixels_and_head_each_card(page: Page, live_server, crowd):
    """At 320 nothing scrolls sideways and no words run out of their box, and each card
    starts with its picture, on one line with the username it pictures."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{live_server.url}/server/people/")

    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"

    heads = page.evaluate("""() => [...document.querySelectorAll('tbody tr')].map((row) => {
        const card = row.getBoundingClientRect();
        const picture = row.querySelector('td[data-avatar] .avatar').getBoundingClientRect();
        const name = row.querySelector('td[data-avatar] + td').getBoundingClientRect();
        const cells = [...row.querySelectorAll('td:not([data-avatar]):not([data-actions])')];
        const first = Math.min(...cells.map((cell) => cell.getBoundingClientRect().top));
        return {
            person: row.dataset.person,
            inCard: picture.left >= card.left && picture.right <= card.right
                && picture.top >= card.top,
            onTop: picture.top <= first + 1,
            beside: picture.bottom > name.top && name.bottom > picture.top,
        };
    })""")
    assert len(heads) == CROWD + 2
    for head in heads:
        assert head["inCard"] and head["onTop"], head
        assert head["beside"], f"{head['person']}: the picture and the username are not one line"
