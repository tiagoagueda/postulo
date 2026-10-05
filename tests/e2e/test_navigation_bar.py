"""The main navigation, measured: the row that never wraps, and the bar a phone gets (#299).

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

#: What the bar's slots are: its four items and the trigger of *More*.
SLOTS = "header [data-nav-main] > a:visible, header [data-nav-main] > [data-nav-more] > button"

#: Every label in the bar that does not fit its slot: wider than its own box, or out past
#: the slot's edges. A label wraps between its words and never inside one, so a slot is at
#: least as wide as its longest word -- this is what says so.
LABELS_THAT_SPILL = """() => {
  const out = [];
  const labels = document.querySelectorAll(
    '[data-nav-main] > a > span, [data-nav-main] > [data-nav-more] > button > span');
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
    expect(page.locator("footer [data-version]")).to_be_in_viewport()
    version = page.locator("footer [data-version]").bounding_box()
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
    base = live_server.url
    set_language(furnished, language)
    sign_in(page, base)

    for width in (PHONE["width"], 320):
        page.set_viewport_size({"width": width, "height": 740})
        page.goto(f"{base}/applications/")
        slots = page.locator(SLOTS)
        expect(slots).to_have_count(5)
        for index in range(5):
            box = slots.nth(index).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, (width, index, box)
        assert not page.evaluate(LABELS_THAT_SPILL), width

    # The criterion's override, on a phone: every word still fits its slot, nothing scrolls
    # sideways, and the page still clears the bar at whatever height it grew to.
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


def test_the_page_you_are_on_is_marked_by_more_than_colour(live_server, page: Page, furnished):  # noqa: F811
    """In the bar: the weight and the underline as well as the shade, and `aria-current` for a
    screen reader. Under *More*: the same on the item, and *More* itself in the weight."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    cues = """el => {
        const style = getComputedStyle(el);
        return {weight: Number(style.fontWeight), line: style.textDecorationLine};
    }"""

    page.goto(f"{base}/applications/")
    here = page.locator('header [data-nav-main] > a[data-nav="applications"]')
    expect(here).to_have_attribute("aria-current", "page")
    marked = here.evaluate(cues)
    assert marked["weight"] >= 600 and "underline" in marked["line"], marked
    other = page.locator('header [data-nav-main] > a[data-nav="listings"]').evaluate(cues)
    assert other["weight"] < 600 and "underline" not in other["line"], other

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
    here, at a phone's width, in both themes, with *More* shut and open -- on a page whose
    item is in the bar and on one whose item is under *More*."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    failures = []
    for path in ("/applications/", "/jobs/companies/"):
        page.goto(f"{base}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} at {PHONE['width']} ({scheme})", found))
        page.locator("header [data-nav-more] > button").click()
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} with More open ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_more_is_not_drawn_when_nothing_is_under_it(live_server, page: Page, furnished):  # noqa: F811
    """A person whose navigation has four items or fewer fills the bar and has nothing to
    open: *More* is not drawn, and the search is the masthead's icon and nothing else (#339)."""
    from postulo.accounts.models import Profile
    from postulo.core import navigation

    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(PHONE)
    page.goto(f"{base}/applications/")
    more = page.locator("header [data-nav-more]")
    expect(more).to_be_visible()

    keys = list(navigation.DEFAULT_ORDER)
    Profile.objects.filter(user=furnished["applicant"]).update(hidden_nav_items=keys[4:])
    page.goto(f"{base}/applications/")
    shown = page.locator("header [data-nav-main] > a:visible")
    assert shown.count() == 4
    expect(more).to_be_hidden()
    expect(page.locator("header [data-nav-search]")).to_have_count(0)
    expect(page.locator("header [data-search-link]")).to_be_visible()


def test_more_opens_with_scripts_off(live_server, browser: Browser, furnished):  # noqa: F811
    """A popover since #310, and a `<details>` before it: either way the browser opens it,
    so it opens for everybody. And with nothing to measure the bar, the page's padding falls
    back to the stylesheet's answer, which is never less than the bar."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport=PHONE)
    page = context.new_page()
    try:
        sign_in(page, base)
        page.goto(f"{base}/career/")
        more = page.locator("header [data-nav-more]")
        more.locator(":scope > button").click()
        expect(more.locator(":scope > [popover]")).to_be_visible()
        expect(more.locator('[data-nav="companies"]')).to_be_visible()
        expect(more.locator("[data-nav-search]")).to_have_count(0)

        padding = page.evaluate("() => parseFloat(getComputedStyle(document.body).paddingBottom)")
        assert padding >= page.locator("header [data-nav-main]").bounding_box()["height"]

        more.locator('[data-nav="companies"]').click()
        expect(page).to_have_url(f"{base}/jobs/companies/")
    finally:
        context.close()
