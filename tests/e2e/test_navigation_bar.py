"""The main navigation, measured: the row that never wraps, and the bar a phone gets (#299, #912).

`test_reflow.py` holds the one property the old *Menu* disclosure was fenced for -- nothing
scrolls sideways at 320 and every destination is reachable. This holds the rest of what
the navigation promises, which a document cannot answer and a browser can:

* **The row never wraps**, at any width from `md` up, in the three languages the reflow walk
  is taken in -- with the script that fits it to its room, and without it, where the
  stylesheet's counts are the whole answer.
* **Nothing on a page is behind the bar**: the page scrolls clear of it, a link focused at
  the foot of the window lands above it, and the failure alert sits on top of it.
* **Its targets are a thumb's**, 44 pixels where there is room and never under 24, and it
  keeps them under the text-spacing override.
* **The bar holds every item on one row, and scrolls sideways** where they do not fit
  (#912) -- with a fade at each end that has more past it, the page you are on in view as
  it opens, and a link reached by Tab brought into view. *More* is the row's alone.
* **The page you are on is marked by more than colour**, in the bar and under *More*.
* **More opens with scripts off**, because a menu somebody cannot open without a script is
  not one this project ships.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)
from .test_reflow import LANGUAGES, SCROLLS_SIDEWAYS
from .test_text_spacing import ADOPT, TEXT_SPACING

pytestmark = pytest.mark.e2e

#: A phone: the width most of them report.
PHONE = {"width": 390, "height": 740}

#: The widths at which the navigation is the masthead's row: each breakpoint's first pixel,
#: the laptop the rest of the suite uses, and a desktop.
ROW_WIDTHS = (768, 1024, 1280, 1440)

#: What the bar's slots are: every item, and no *More* (#912).
SLOTS = "header [data-nav-main] > a"

#: The width of 400% zoom, where the bar has least room.
NARROW = {"width": 320, "height": 740}

#: The languages the bar's scrolling is checked in: the interface's own, and the one whose
#: words for the navigation are the longest of the catalogues -- Estonian's *Kandideerimised*
#: is what used to put *More* on a second row at 320.
BAR_LANGUAGES = ("en", "et")

#: Every label in the bar that does not fit its slot: wider than its own box, or out past
#: the slot's edges. A label wraps between its words and never inside one, so a slot is at
#: least as wide as its longest word -- this is what says so.
LABELS_THAT_SPILL = """() => {
  const out = [];
  const labels = document.querySelectorAll('[data-nav-main] > a > span');
  for (const label of labels) {
    if (!label.getClientRects().length) continue;
    const slot = label.parentElement.getBoundingClientRect();
    const box = label.getBoundingClientRect();
    if (label.scrollWidth > label.clientWidth + 1
        || box.left < slot.left - 1 || box.right > slot.right + 1) {
      out.push({text: label.textContent, needs: label.scrollWidth, box: label.clientWidth});
    }
  }
  return out;
}"""


def bar_top(page: Page) -> float:
    return page.locator("header [data-nav-main]").bounding_box()["y"]


def set_language(furnished, language: str) -> None:  # noqa: F811
    from postulo.accounts.models import Profile

    Profile.objects.filter(user=furnished["applicant"]).update(language=language)


def row_is_one_line(page: Page) -> dict:
    """Whether the masthead is one line: the wordmark, every drawn item of the row, *More*
    and the tools all sharing a middle. A row that wraps puts one of them a line down."""
    return page.evaluate(
        """() => {
            const row = document.querySelector('[data-site-header] > div');
            const drawn = [...row.children].flatMap((child) =>
                child.matches('[data-nav-main]')
                    ? [...child.children].filter((c) => c.getClientRects().length)
                    : [child]);
            const middles = drawn.map((el) => {
                const box = el.getBoundingClientRect();
                return Math.round(box.top + box.height / 2);
            });
            return {
                middles,
                spread: Math.max(...middles) - Math.min(...middles),
                what: drawn.map((el) => el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 20)),
            };
        }"""
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_row_never_wraps(live_server, page: Page, browser: Browser, furnished, language):  # noqa: F811
    """With the script that fits it, and without: the stylesheet's counts are written for the
    widest of these languages, so they must hold on their own."""
    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)
    scriptless = browser.new_context(java_script_enabled=False)
    bare = scriptless.new_page()
    sign_in(bare, base)
    try:
        for shown in (page, bare):
            for width in ROW_WIDTHS:
                shown.set_viewport_size({"width": width, "height": 800})
                shown.goto(f"{base}/applications/")
                line = row_is_one_line(shown)
                where = f"{width}px, {language}, scripts {'on' if shown is page else 'off'}"
                assert line["spread"] <= 2, f"the masthead wraps at {where}: {line}"
                assert not shown.evaluate(SCROLLS_SIDEWAYS)["reached"], where
    finally:
        scriptless.close()


#: What the script that fits the row has made of it, once it has had the frames to run in:
#: how many items are in the line, and whether *More* is drawn in the bolder weight it has
#: while it holds the page you are on.
THE_FIT = """() => new Promise((done) => {
  const read = () => {
    const nav = document.querySelector('[data-nav-main]');
    const more = nav.querySelector('[data-nav-more] > button');
    done({
      line: [...nav.children].filter((c) => c.tagName === 'A' && c.getClientRects().length).length,
      bold: more.getClientRects().length > 0 && Number(getComputedStyle(more).fontWeight) >= 600,
    });
  };
  requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(read)));
})"""


def test_the_row_is_fitted_to_more_in_the_weight_it_is_drawn_in(live_server, page: Page, furnished):  # noqa: F811
    """*More* is drawn in bolder type while the page you are on is one of the items under it,
    and bolder is wider. The script that fits the row measured *More* in the lighter weight
    only, so wherever the current page went under *More* the row was short by the
    difference -- ten pixels in Greek under the text-spacing override -- and focusing the
    search box, which grows into the room the fit sets aside for it, put the masthead on two
    lines (#313).

    The row is tightest at the first width at which the fit lets one more item into the
    line, and where that is depends on the font. So the window is walked from `lg` to `2xl`
    to find those widths, and each is then opened afresh, a pixel on: the fit has between
    one pixel and four to spare there if it measured *More* as it is drawn, and less than
    nothing if it measured it lighter.

    In a font whose two weights are the same width there is nothing to mismeasure, and this
    passes either way: Segoe UI is one, DejaVu Sans, which CI draws in, is not.

    Not under `prefers-reduced-motion`, which would be the quick way to have the box at its
    focused width at once: the stylesheet then gives every element a transition of a
    hundredth of a millisecond, and a weight that has only just changed still reads as the
    old one, so the fit measures *More* in whatever weight it had before -- which hides the
    very thing being measured. The box is waited for instead."""
    base = live_server.url
    set_language(furnished, "el")
    sign_in(page, base)
    page.set_viewport_size({"width": 1024, "height": 800})
    page.goto(f"{base}/jobs/companies/")
    page.evaluate(ADOPT, TEXT_SPACING)
    # A fixed wait on purpose: the time the ResizeObserver and the frame-scheduled fit get to
    # re-fit the row to the override; the count it settles on is what is being found out, so
    # there is no end state to wait for.
    page.wait_for_timeout(100)
    started = page.evaluate(THE_FIT)["line"]

    first: dict[int, int] = {}
    for width in range(1024, 1537, 3):
        page.set_viewport_size({"width": width, "height": 800})
        fit = page.evaluate(THE_FIT)
        if fit["bold"]:
            first.setdefault(fit["line"], width)
    # The count the walk started with was not let in at any width it saw, so it is not one.
    tightest = {count: width for count, width in first.items() if count != started}
    assert tightest, f"the walk never saw another item join the line under More: {first}"

    for count, width in sorted(tightest.items()):
        page.set_viewport_size({"width": width + 1, "height": 800})
        page.goto(f"{base}/jobs/companies/")
        page.evaluate(ADOPT, TEXT_SPACING)
        # A fixed wait on purpose: the time the ResizeObserver and the frame-scheduled fit get
        # to re-fit the row to the override; waiting for `count` items instead could pass on
        # the fit made before the override, which is not the one being checked.
        page.wait_for_timeout(100)
        fit = page.evaluate(THE_FIT)
        assert fit == {"line": count, "bold": True}, (width, fit)
        box = page.locator("#site-search")
        rests = box.bounding_box()["width"]
        box.focus()
        # Half as wide again, which is what the fit counted; the growth is a transition.
        page.wait_for_function(
            "rests => document.querySelector('#site-search').getBoundingClientRect().width"
            " >= rests * 1.5 - 0.01",
            arg=rests,
        )
        line = row_is_one_line(page)
        where = f"{count} items and More at {width + 1}px"
        assert line["spread"] <= 2, f"focusing the search box wraps the masthead, {where}: {line}"


def test_nothing_on_a_page_ends_up_behind_the_bar(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    page.goto(f"{base}/career/")

    # The height the script measured is the height the bar has.
    height = page.locator("header [data-nav-main]").bounding_box()["height"]
    measured = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--bottom-bar-height')"
    )
    assert abs(float(measured.strip().removesuffix("px")) - height) <= 1, (measured, height)

    # The last line of the page scrolls clear of it.
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    expect(page.locator("footer [data-powered]")).to_be_in_viewport()
    version = page.locator("footer [data-powered]").bounding_box()
    assert version["y"] + version["height"] <= bar_top(page), "the footer ends under the bar"

    # A link lying behind the bar is scrolled clear of it when focus lands on it (SC 2.4.11).
    # Set up rather than hoped for: a link somewhere off the screen is centred by the browser
    # when it takes focus, whatever the page says, so the case that needs
    # `scroll-padding-bottom` is the one already inside the window and under the bar.
    last = page.locator("main a:visible").last
    top = last.evaluate("el => el.getBoundingClientRect().top + window.scrollY")
    page.evaluate("y => window.scrollTo(0, y)", top - (PHONE["height"] - 30))
    expect(last).to_be_in_viewport()
    box = last.bounding_box()
    assert box["y"] + box["height"] > bar_top(page), "the link should start out under the bar"
    last.focus()
    expect(last).to_be_focused()
    box = last.bounding_box()
    assert box["y"] + box["height"] <= bar_top(page), "a focused link is under the bar"

    # And the failure alert sits on top of it rather than over it.
    page.evaluate(
        "() => { document.querySelector('[data-htmx-alert]').textContent = 'It failed.' }"
    )
    alert = page.locator(".page-alert")
    expect(alert).to_be_visible()
    box = alert.bounding_box()
    assert box["y"] + box["height"] <= bar_top(page), "the alert is behind the bar"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_bar_is_a_thumbs_size_and_keeps_it_under_the_spacing_override(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    from postulo.core import navigation

    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)

    for width in (PHONE["width"], NARROW["width"]):
        page.set_viewport_size({"width": width, "height": 740})
        page.goto(f"{base}/applications/")
        slots = page.locator(SLOTS)
        expect(slots).to_have_count(len(navigation.DEFAULT_ORDER))
        for index in range(slots.count()):
            box = slots.nth(index).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, (width, index, box)
        assert not page.evaluate(LABELS_THAT_SPILL), width

    # The criterion's override, on a phone: every word still fits its slot, the page does not
    # scroll sideways (the bar may, in its own box), and the page still clears the bar at
    # whatever height it grew to.
    page.set_viewport_size({"width": PHONE["width"], "height": 740})
    page.goto(f"{base}/applications/")
    page.evaluate(ADOPT, TEXT_SPACING)
    # A fixed wait on purpose: the time the ResizeObserver gets to re-measure the bar into
    # `--bottom-bar-height` under the override, read below against the bar's own height.
    page.wait_for_timeout(200)
    assert not page.evaluate(LABELS_THAT_SPILL)
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
    slots = page.locator(SLOTS)
    for index in range(slots.count()):
        box = slots.nth(index).bounding_box()
        assert box["width"] >= 24 and box["height"] >= 24, (index, box)
    height = page.locator("header [data-nav-main]").bounding_box()["height"]
    measured = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--bottom-bar-height')"
    )
    assert abs(float(measured.strip().removesuffix("px")) - height) <= 1, (measured, height)


# ------------------------------------------------------------ the bar scrolls (#912)

#: The bar as a person sees it: how many rows its items are on, whether it is wider inside
#: than out, which ends the script has marked as having more past them, and whether each
#: end's fade is drawn.
THE_BAR = """() => {
  const nav = document.querySelector('[data-nav-main]');
  const links = [...nav.children].filter((c) => c.tagName === 'A');
  const middles = links.map((a) => {
    const box = a.getBoundingClientRect();
    return Math.round(box.top + box.height / 2);
  });
  return {
    fixed: getComputedStyle(nav).position === 'fixed',
    items: links.length,
    rows: new Set(middles).size,
    inside: nav.scrollWidth,
    outside: nav.clientWidth,
    along: Math.abs(nav.scrollLeft),
    start: nav.hasAttribute('data-overflow-start'),
    end: nav.hasAttribute('data-overflow-end'),
    fadeStart: getComputedStyle(nav, '::before').opacity,
    fadeEnd: getComputedStyle(nav, '::after').opacity,
  };
}"""

#: Whether a link of the bar is in view and clear of whichever fade is drawn over its edge:
#: the room the stylesheet's `scroll-padding-inline` keeps at an end with more past it.
CLEAR_IN_THE_BAR = """(link) => {
  const nav = link.parentElement;
  const style = getComputedStyle(nav);
  const bar = nav.getBoundingClientRect();
  const left = bar.left + nav.clientLeft;
  const right = left + nav.clientWidth;
  const box = link.getBoundingClientRect();
  const fade = parseFloat(style.scrollPaddingLeft) || 0;
  const rtl = style.direction === 'rtl';
  const fadedLeft = nav.hasAttribute(rtl ? 'data-overflow-end' : 'data-overflow-start');
  const fadedRight = nav.hasAttribute(rtl ? 'data-overflow-start' : 'data-overflow-end');
  return box.left >= left + (fadedLeft ? fade : 0) - 1
    && box.right <= right - (fadedRight ? fade : 0) + 1;
}"""


@pytest.mark.parametrize("language", BAR_LANGUAGES)
def test_the_bar_is_one_row_that_scrolls_sideways_and_says_so(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    """At 320 every item is in the bar, on one row, and the bar scrolls sideways in its own
    box rather than grow a second row (#912, reversing #299). An end with more past it
    fades the items out, so the last one in view is cut part way through -- not a colour
    alone saying so -- and the fade moves to the other end once the bar is scrolled across.
    The page itself never scrolls sideways, and `--bottom-bar-height` is still the bar's."""
    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)
    page.set_viewport_size(NARROW)
    page.goto(f"{base}/")
    nav = page.locator("header [data-nav-main]")

    bar = page.evaluate(THE_BAR)
    assert bar["fixed"] and bar["items"] == page.locator(SLOTS).count(), bar
    assert bar["rows"] == 1, f"the bar is on {bar['rows']} rows in {language}: {bar}"
    assert bar["inside"] > bar["outside"] + 1, f"nothing to scroll in {language}: {bar}"
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls, not just the bar"

    # At the start, with the page you are on the first item: more past the end, none before.
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('[data-nav-main]'), '::after')"
        ".opacity === '1'"
    )
    bar = page.evaluate(THE_BAR)
    assert bar["along"] == 0 and bar["end"] and not bar["start"], bar
    assert bar["fadeStart"] == "0", bar
    # The fade lies over an item, which is what makes it a shape and not a shade: some link
    # is cut by the end of the bar or runs on under the fade.
    cut = nav.evaluate(
        """nav => {
            const right = nav.getBoundingClientRect().left + nav.clientLeft + nav.clientWidth;
            const fade = parseFloat(getComputedStyle(nav).scrollPaddingRight);
            return [...nav.children].some((a) => a.tagName === 'A' && a.checkVisibility()
                && a.getBoundingClientRect().left < right
                && a.getBoundingClientRect().right > right - fade);
        }"""
    )
    assert cut, "no item runs on under the fade at the end"

    # Scrolled across, the other way round.
    nav.evaluate("nav => nav.scrollBy({left: nav.scrollWidth, behavior: 'instant'})")
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('[data-nav-main]'), '::before')"
        ".opacity === '1'"
    )
    bar = page.evaluate(THE_BAR)
    assert bar["along"] > 0 and bar["start"] and not bar["end"], bar
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]

    # One row, so one height, and the script measured it.
    height = nav.bounding_box()["height"]
    measured = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--bottom-bar-height')"
    )
    assert abs(float(measured.strip().removesuffix("px")) - height) <= 1, (measured, height)


@pytest.mark.parametrize("language", BAR_LANGUAGES)
def test_the_page_you_are_on_is_in_view_in_the_bar_as_it_opens(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    """The last item in the person's order is past the end of the bar at 320; on its own
    page the bar opens scrolled to it, clear of the fade."""
    from postulo.core import navigation

    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)
    page.set_viewport_size(NARROW)
    last = navigation.DEFAULT_ORDER[-1]
    page.goto(f"{base}/")
    link = page.locator(f'header [data-nav-main] > a[data-nav="{last}"]')
    assert not link.evaluate(CLEAR_IN_THE_BAR), "on the dashboard it should be out of view"

    link.click()
    expect(link).to_have_attribute("aria-current", "page")
    page.wait_for_function(
        f"() => ({CLEAR_IN_THE_BAR})("
        f"document.querySelector('[data-nav-main] > a[data-nav=\"{last}\"]'))"
    )
    assert page.evaluate(THE_BAR)["along"] > 0


@pytest.mark.parametrize("language", BAR_LANGUAGES)
def test_a_link_reached_by_tab_is_scrolled_into_view(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    """Every item is a link, so Tab reaches the ones scrolled out of sight, and each is
    brought into view clear of the fades as it takes focus (SC 2.4.11) -- forwards to the
    end of the bar and back again."""
    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)
    page.set_viewport_size(NARROW)
    page.goto(f"{base}/")
    links = page.locator(SLOTS)
    count = links.count()

    links.first.focus()
    previous = 0
    for index in [*range(count), *reversed(range(count - 1))]:
        if index != previous:
            page.keyboard.press("Tab" if index > previous else "Shift+Tab")
        previous = index
        link = links.nth(index)
        expect(link).to_be_focused()
        assert link.evaluate(CLEAR_IN_THE_BAR), f"item {index} in {language} is not in view"
    assert page.evaluate(THE_BAR)["along"] == 0, "back at the first item, back at the start"


def test_more_is_the_rows_alone(live_server, page: Page, furnished):  # noqa: F811
    """Below `md` there is no *More*: every item is in the bar. From `md` up the row puts what
    it has no room for under *More*, as it always has."""
    from postulo.core import navigation

    base = live_server.url
    sign_in(page, base)
    more = page.locator("header [data-nav-more]")
    for width in (NARROW["width"], PHONE["width"], 767):
        page.set_viewport_size({"width": width, "height": 740})
        page.goto(f"{base}/applications/")
        expect(more).to_be_hidden()
        slots = page.locator(SLOTS)
        expect(slots).to_have_count(len(navigation.DEFAULT_ORDER))
        assert all(slots.nth(i).is_visible() for i in range(slots.count())), width
    # Where the row has no room for all six, which is `md` and `lg` in any language.
    for width in (768, 1024):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base}/applications/")
        expect(more, f"More at {width}").to_be_visible()
        assert page.evaluate(THE_BAR)["fixed"] is False


def test_the_page_you_are_on_is_marked_by_more_than_colour(live_server, page: Page, furnished):  # noqa: F811
    """In the bar: the weight and the underline as well as the shade, and `aria-current` for a
    screen reader. Under the row's *More*: the same on the item, and *More* itself in the
    weight."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    cues = """el => {
        const style = getComputedStyle(el);
        return {weight: Number(style.fontWeight), line: style.textDecorationLine};
    }"""

    for path, key in (("/applications/", "applications"), ("/jobs/companies/", "companies")):
        page.goto(f"{base}{path}")
        here = page.locator(f'header [data-nav-main] > a[data-nav="{key}"]')
        expect(here).to_have_attribute("aria-current", "page")
        marked = here.evaluate(cues)
        assert marked["weight"] >= 600 and "underline" in marked["line"], marked
        other = page.locator('header [data-nav-main] > a[data-nav="listings"]').evaluate(cues)
        assert other["weight"] < 600 and "underline" not in other["line"], other

    # *More* is the row's, from `md` up, where Companies is under it.
    page.set_viewport_size({"width": 768, "height": 800})
    page.goto(f"{base}/jobs/companies/")
    more = page.locator("header [data-nav-more]")
    assert more.locator(":scope > button").evaluate(cues)["weight"] >= 600, "More holds the page"
    more.locator(":scope > button").click()
    inside = more.locator('[data-nav="companies"]')
    expect(inside).to_have_attribute("aria-current", "page")
    marked = inside.evaluate(cues)
    assert marked["weight"] >= 600 and "underline" in marked["line"], marked


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_bar_has_no_violations(live_server, page: Page, axe_source, furnished, scheme):  # noqa: F811
    """The accessibility walk runs at 1280, where there is no bar to read. So the bar is read
    here, at a phone's width and at 320, where it scrolls, in both themes -- on the page of
    its first item and of one further along."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    failures = []
    for size in (PHONE, NARROW):
        page.set_viewport_size(size)
        for path in ("/applications/", "/jobs/companies/"):
            page.goto(f"{base}{path}")
            found = violations_on(page, axe_source)
            if found:
                failures.append(describe(f"{path} at {size['width']} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_more_is_not_drawn_when_nothing_is_under_it(live_server, page: Page, furnished):  # noqa: F811
    """A person whose row holds every item they keep has nothing to open: *More* is not
    drawn, and the search is the masthead's icon and nothing else (#339)."""
    from postulo.accounts.models import Profile
    from postulo.core import navigation

    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 768, "height": 800})
    page.goto(f"{base}/applications/")
    more = page.locator("header [data-nav-more]")
    expect(more).to_be_visible()

    keys = list(navigation.DEFAULT_ORDER)
    Profile.objects.filter(user=furnished["applicant"]).update(hidden_nav_items=keys[2:])
    page.goto(f"{base}/applications/")
    shown = page.locator("header [data-nav-main] > a:visible")
    assert shown.count() == 2
    expect(more).to_be_hidden()
    expect(page.locator("header [data-nav-search]")).to_have_count(0)
    expect(page.locator("header [data-search-link]")).to_be_visible()


def test_more_opens_with_scripts_off(live_server, browser: Browser, furnished):  # noqa: F811
    """A popover since #310, and a `<details>` before it: either way the browser opens it,
    so it opens for everybody. Below `md`, with nothing to measure the bar, every item is
    still in it and it still scrolls, and the page's padding falls back to the stylesheet's
    answer, which is never less than the bar."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=NARROW)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/career/")
        expect(page.locator("header [data-nav-more]")).to_be_hidden()
        bar = page.evaluate(THE_BAR)
        assert bar["rows"] == 1 and bar["inside"] > bar["outside"] + 1, bar
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
        padding = page.evaluate("() => parseFloat(getComputedStyle(document.body).paddingBottom)")
        assert padding >= page.locator("header [data-nav-main]").bounding_box()["height"]

        page.set_viewport_size({"width": 768, "height": 800})
        page.goto(f"{base}/career/")
        more = page.locator("header [data-nav-more]")
        more.locator(":scope > button").click()
        expect(more.locator(":scope > [popover]")).to_be_visible()
        expect(more.locator('[data-nav="companies"]')).to_be_visible()
        expect(more.locator("[data-nav-search]")).to_have_count(0)
        more.locator('[data-nav="companies"]').click()
        expect(page).to_have_url(f"{base}/jobs/companies/")
    finally:
        context.close()


def test_the_source_link_is_the_mark_alone_on_a_phone_and_keeps_its_name(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """Mark and words on a laptop; on a phone the mark alone, 24 pixels and still named (#837)."""
    link = page.locator("footer [data-source]")

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{live_server.url}/accounts/login/")
    expect(link.locator("svg[data-brand=forgejo]")).to_be_visible()
    expect(link.get_by_text("Source code")).to_be_visible()

    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/accounts/login/")
    expect(link.locator("svg[data-brand=forgejo]")).to_be_visible()
    box = link.bounding_box()
    assert box["width"] >= 24 and box["height"] >= 24, box
    expect(page.locator("footer").get_by_role("link", name="Source code")).to_be_visible()
    assert link.get_attribute("title") == "Source code"


def test_the_help_link_is_the_icon_alone_on_a_phone_and_keeps_its_name(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """The book and the word on a laptop; on a phone the book, 24 pixels, still named (#838)."""
    link = page.locator("footer [data-help]")

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{live_server.url}/accounts/login/")
    expect(link.locator("svg[data-icon=book-text]")).to_be_visible()
    expect(link.get_by_text("Help")).to_be_visible()

    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/accounts/login/")
    expect(link.locator("svg[data-icon=book-text]")).to_be_visible()
    box = link.bounding_box()
    assert box["width"] >= 24 and box["height"] >= 24, box
    expect(page.locator("footer").get_by_role("link", name="Help")).to_be_visible()
