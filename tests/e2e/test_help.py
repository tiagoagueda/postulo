"""Help where the question is asked, in a real browser (#302).

What the server draws is in `tests/test_help_topics.py`. What needs a browser is everything
the two components *do*:

- **the tooltip**: a field's sentence hidden until the field is hovered or has the focus;
  shown at once for the focus and after a moment for the pointer, and not at all for a
  pointer that only crosses the field; kept while the pointer is on the tooltip itself, and
  reached by a slow hand straight up or on a diagonal from the far end of a wide box; put
  away by Escape, which is the tooltip's alone only when it is the focused field's; gone
  when the pointer and the focus have both left (SC 1.4.13). **Shown only where there is a
  clear place for it** -- wholly in the window that can be seen, and over neither its own
  field, nor a refusal, a mark or a note under a row, a card's question mark, an open select
  list, or the focused control -- and otherwise drawn under its field; and none for a field
  while its select's list is open: walked with Tab and with Shift+Tab in a
  window the size 400% zoom leaves, with and without wider text spacing; over a refused row
  and a mark under a row; under the masthead; and at 320 and 1280 pixels in English, German
  and Greek, where it is also never over what Tab reaches next. The field's accessible
  description is what it always was;
- **the question mark and the drawer**: one sentence on hover and on focus, a button that
  Space presses, and the card's help in a modal drawer at the inline end, closed by Escape,
  by *Close* and by a press outside, with the focus given back;
- **with scripts off**: the help under its field, the sentence under its card's title, and
  the question mark a link that opens a page holding the same help in a new tab, so that
  nothing typed on the form is lost;
- axe in both themes with a tooltip showing and with the drawer open; text spacing; forced
  colours; right to left; and a touch screen.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from .selects import button_of, list_of, open_list
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS
from .test_row_removal import details, sign_in  # noqa: F401
from .test_text_spacing import ADOPT, TEXT_SPACING

pytestmark = pytest.mark.e2e

HEADLINE = "A short professional title, such as “Backend engineer”."
NUMBERS = (
    "A mobile, a desk line, a switchboard — as many as are worth keeping, with one of them "
    "marked as the one to use. Documents print that one."
)

#: The tooltips that are showing, each with its box, the box of the field it is shown for,
#: and whether its words fit inside it. Boxes are in the page's own co-ordinates, so one
#: taken before the page scrolled can be compared with one taken after.
SHOWING = """() => {
  const open = document.querySelectorAll('[data-tooltip][popover]:popover-open');
  return [...open].map((tip) => {
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {
      left: r.left, right: r.right,
      top: r.top + window.scrollY, bottom: r.bottom + window.scrollY,
    };
  };
  const words = document.createRange();
  words.selectNodeContents(tip);
  const w = words.getBoundingClientRect();
  const r = tip.getBoundingClientRect();
  return {
    id: tip.id,
    tip: box(tip),
    field: tip.tooltipFor ? box(tip.tooltipFor) : null,
    cut: w.left < r.left - 1 || w.right > r.right + 1
      || w.top < r.top - 1 || w.bottom > r.bottom + 1,
    window: document.documentElement.clientWidth,
  };
  });
}"""

#: What has the focus: its own box, and the box of the field it is in -- its label and its
#: control -- where it is in one.
FOCUSED = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const box = (node) => {
    const r = node.getBoundingClientRect();
    return {
      left: r.left, right: r.right,
      top: r.top + window.scrollY, bottom: r.bottom + window.scrollY,
    };
  };
  const field = el.closest('.field');
  const label = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
  return {
    what: el.outerHTML.replace(/\\s+/g, ' ').slice(0, 100),
    own: box(el),
    field: field ? box(field) : null,
    label: label ? box(label) : null,
    last: el.matches('button[type=submit]') && !el.closest('dialog'),
    first: el.matches('#section-picture [data-help-mark]'),
  };
}"""

#: The rule, asked of the page as it stands (review of #302): every tooltip showing is in a
#: clear place -- which an open select list is not -- and is not for a field whose list is
#: open; and the focused control's own help, where it is not showing as a tooltip and its
#: list is not open, is drawn under its field -- or, for a card's sentence, under the
#: card's title, which is where that paragraph is. Written here from the rule itself and
#: not from `app.js`, so that the two can disagree. What is wrong, in words; nothing when
#: all is well.
BY_THE_RULE = """() => {
  const r = (el) => el.getBoundingClientRect();
  const hits = (a, b) => a.left < b.right - 1 && b.left < a.right - 1
    && a.top < b.bottom - 1 && b.top < a.bottom - 1;
  const named = (n) => n.id || n.getAttribute('aria-label')
    || n.tagName + ':' + (n.textContent || '').trim().slice(0, 30);
  const root = document.documentElement;
  const seen = {left: 0, top: 0, right: root.clientWidth, bottom: root.clientHeight};
  const header = document.querySelector('[data-site-header]');
  if (header) seen.top = Math.max(seen.top, r(header).bottom);
  const nav = document.querySelector('[data-nav-main]');
  if (nav && getComputedStyle(nav).position === 'fixed') {
    seen.bottom = Math.min(seen.bottom, r(nav).top);
  }
  const never = document.querySelectorAll(
    '[role="alert"], .alert, .errorlist, [data-phone-mark], [data-kept-as-it-was], '
    + '[data-place-note], [data-country-note], [data-help-mark], '
    + '[data-select-panel]:popover-open');
  const listOpenIn = (n) => !!n.querySelector('[data-select-panel]:popover-open');
  const active = document.activeElement;
  const focused = active && active !== document.body ? active : null;
  const wrong = [];
  for (const tip of document.querySelectorAll('[data-tooltip][popover]:popover-open')) {
    const box = r(tip);
    const anchor = tip.tooltipFor;
    if (box.left < seen.left - 1 || box.right > seen.right + 1
        || box.top < seen.top - 1 || box.bottom > seen.bottom + 1) {
      const at = (a, b) => `${Math.round(a)}..${Math.round(b)}`;
      wrong.push(`${tip.id} is not wholly in the window: `
        + `${at(box.top, box.bottom)} of ${at(seen.top, seen.bottom)}`);
    }
    if (!anchor) { wrong.push(`${tip.id} is shown for nothing`); continue; }
    if (hits(box, r(anchor))) wrong.push(`${tip.id} lies over its own field`);
    if (listOpenIn(anchor)) wrong.push(`${tip.id} shows while its field's list is open`);
    for (const n of never) {
      if (n === anchor || anchor.contains(n) || n.contains(anchor)) continue;
      const b = r(n);
      if ((b.width || b.height) && hits(box, b)) wrong.push(`${tip.id} lies over ${named(n)}`);
    }
    if (focused && !anchor.contains(focused) && hits(box, r(focused))) {
      wrong.push(`${tip.id} lies over the focused ${named(focused)}`);
    }
    const words = document.createRange();
    words.selectNodeContents(tip);
    const w = words.getBoundingClientRect();
    if (w.bottom > box.bottom + 1 || w.right > box.right + 1) {
      wrong.push(`${tip.id} cuts its words short`);
    }
  }
  const field = focused && focused.closest('.field');
  if (focused && !(field && listOpenIn(field))) {
    for (const id of (focused.getAttribute('aria-describedby') || '').split(/\\s+/)) {
      const help = id && document.getElementById(id);
      if (!help || !help.hasAttribute('data-tooltip')) continue;
      if (help.matches('[popover]:popover-open')) continue;
      const b = r(help);
      if (help.hasAttribute('popover')) {
        wrong.push(`${id} is neither a tooltip nor in the flow, at ${named(focused)}`);
      } else if (!b.height
          || (!help.hasAttribute('data-help-summary') && b.top < r(focused).top - 1)) {
        wrong.push(`${id} is not drawn under its field, at ${named(focused)}`);
      }
    }
  }
  return wrong;
}"""

#: How far the page scrolls sideways, asked the way `SCROLLS_SIDEWAYS` asks and without
#: moving it up or down: the walk below is in the middle of the page when it asks.
SIDEWAYS = """() => {
  const down = window.scrollY;
  window.scrollTo(10000, down);
  const reached = Math.round(window.scrollX);
  window.scrollTo(0, down);
  return reached;
}"""

#: Counts each time one tooltip opens and closes, from now on.
WATCH = """(id) => {
  const tip = document.getElementById(id);
  window.__opened = 0;
  window.__closed = 0;
  tip.addEventListener('toggle', (event) => {
    if (event.newState === 'open') window.__opened += 1;
    else window.__closed += 1;
  });
}"""


def drawn(page: Page) -> None:
    """Three frames on. The focus moving scrolls the page, and a tooltip is placed in the
    frame after: asked at once, the page says where things were before."""
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() =>"
        " requestAnimationFrame(() => requestAnimationFrame(done))))"
    )


def overlap(one: dict, other: dict) -> bool:
    """Whether two boxes share any of the page, by more than a pixel of rounding."""
    return (
        one["left"] < other["right"] - 1
        and other["left"] < one["right"] - 1
        and one["top"] < other["bottom"] - 1
        and other["top"] < one["bottom"] - 1
    )


def your_details(page: Page, base: str) -> None:
    sign_in(page, base)
    page.goto(f"{base}/accounts/profile/")


def reads_in(person, language: str) -> None:
    from postulo.accounts.models import Profile

    Profile.objects.filter(user=person).update(language=language)


def centre_of(page: Page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def uncovered(page: Page, selector: str) -> bool:
    """Whether what is drawn at the middle of an element is the element itself: nothing in
    the top layer lies over it."""
    return page.locator(selector).evaluate(
        "(el) => { const r = el.getBoundingClientRect();"
        " const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
        " return !!at && (at === el || el.contains(at)); }"
    )


# ------------------------------------------------------------------------ the tooltip


def test_a_fields_help_shows_while_the_field_has_the_focus(page: Page, live_server, details):  # noqa: F811
    """Hidden until the field has the focus, there at once when it does, and gone when the
    focus has. The box is described by the sentence throughout: a screen reader reads it
    with the field exactly as it did when it was drawn under it."""
    your_details(page, live_server.url)
    box = page.locator("#id_headline")
    tip = page.locator("#id_headline_helptext")

    expect(tip).to_be_hidden()
    expect(tip).to_have_attribute("role", "tooltip")
    expect(box).to_have_accessible_description(HEADLINE)

    box.focus()
    expect(tip).to_be_visible()
    expect(tip).to_have_text(HEADLINE)
    expect(box).to_have_accessible_description(HEADLINE)
    expect(box).to_have_accessible_name("Headline")
    drawn(page)
    (shown,) = page.evaluate(SHOWING)
    assert shown["id"] == "id_headline_helptext"
    assert not overlap(shown["tip"], shown["field"]), "the tooltip lies over its own field"
    assert shown["tip"]["bottom"] <= shown["field"]["top"], "it is above its field"
    assert 0 <= shown["tip"]["left"] and shown["tip"]["right"] <= shown["window"], shown

    page.locator("#id_location").focus()
    expect(tip).to_be_hidden()
    expect(page.locator("#id_location_helptext")).to_be_visible()


def test_escape_puts_it_away_and_does_nothing_else(page: Page, live_server, details):  # noqa: F811
    """Dismissible (SC 1.4.13): Escape hides the focused field's tooltip, the focus stays
    where it was, and it stays hidden until the focus has left and come back. Whatever else
    is open stays open -- the masthead's *New* menu here -- and a second Escape is then its
    own."""
    your_details(page, live_server.url)
    box = page.locator("#id_headline")
    tip = page.locator("#id_headline_helptext")
    menu = page.locator(".dropdown-menu:has(> button[aria-label='New']) > [popover]")

    page.get_by_role("button", name="New", exact=True).click()
    expect(menu).to_be_visible()
    box.focus()
    expect(tip).to_be_visible()
    expect(menu).to_be_visible()

    page.keyboard.press("Escape")
    expect(tip).to_be_hidden()
    expect(box).to_be_focused()
    expect(menu).to_be_visible()
    page.keyboard.type("Engineer")
    expect(tip).to_be_hidden()
    expect(box).to_have_value("Engineer")

    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()

    page.locator("#id_location").focus()
    box.focus()
    expect(tip).to_be_visible()


def test_escape_is_not_taken_by_a_tooltip_nobody_is_looking_at(page: Page, live_server, details):  # noqa: F811
    """Two places an Escape went to a tooltip and nowhere else (review of #302). With the
    pointer resting on a field and the focus in the masthead's search, the one Escape puts
    the tooltip away and is the search's as well. And with the pointer rested on a question
    mark until its sentence showed, a press opens the drawer and one Escape closes it."""
    page.set_viewport_size({"width": 1280, "height": 800})
    your_details(page, live_server.url)
    tip = page.locator("#id_headline_helptext")
    page.locator("label[for=id_headline]").hover()
    expect(tip).to_be_visible()
    search = page.locator("#site-search")
    search.focus()
    page.keyboard.type("abc")
    expect(tip).to_be_visible()

    page.keyboard.press("Escape")
    expect(search).to_have_value("")
    expect(search).to_be_focused()
    expect(tip).to_be_hidden()

    mark = page.locator("#section-phones [data-help-mark]")
    mark.scroll_into_view_if_needed()
    mark.hover()
    expect(page.locator("#section-phones [data-help-summary]")).to_be_visible()
    mark.click()
    panel = page.locator("#help-telephone-numbers > div")
    expect(panel).to_be_visible()
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()


def test_resting_the_pointer_on_a_field_shows_it_and_the_pointer_can_go_onto_it(
    page: Page,
    live_server,
    details,  # noqa: F811
):
    """Hoverable and persistent (SC 1.4.13): resting on the label or the box shows the
    sentence, the pointer can travel onto the sentence without losing it, and it goes once
    the pointer has left both. Escape puts it away under a pointer that has not moved, and
    it stays away until the pointer has left -- for longer than the half second a hand is
    allowed to slip off -- and come back."""
    your_details(page, live_server.url)
    tip = page.locator("#id_headline_helptext")
    label = page.locator("label[for=id_headline]")

    expect(tip).to_be_hidden()
    label.hover()
    expect(tip).to_be_visible()
    assert not page.evaluate("() => document.activeElement.id"), "hovering moved the focus"

    onto = tip.bounding_box()
    page.mouse.move(onto["x"] + onto["width"] / 2, onto["y"] + onto["height"] / 2, steps=8)
    page.wait_for_timeout(700)
    expect(tip).to_be_visible()

    page.keyboard.press("Escape")
    expect(tip).to_be_hidden()
    page.wait_for_timeout(700)
    expect(tip).to_be_hidden()

    page.mouse.move(5, 400, steps=4)
    page.wait_for_timeout(700)
    page.locator("#id_headline").hover()
    expect(tip).to_be_visible()
    page.mouse.move(5, 400, steps=4)
    expect(tip).to_be_hidden()


def test_a_pointer_that_only_crosses_a_field_shows_nothing(page: Page, live_server, details):  # noqa: F811
    """The pointer has to rest on a field before its sentence shows, so that crossing a
    form on the way somewhere else does not light every sentence on the way."""
    page.set_viewport_size({"width": 1280, "height": 800})
    your_details(page, live_server.url)
    page.locator("#id_headline").scroll_into_view_if_needed()
    page.evaluate(WATCH, "id_headline_helptext")
    field = page.locator("#id_headline").evaluate(
        "(el) => { const r = el.closest('.field').getBoundingClientRect();"
        " return {x: r.left + 40, top: r.top, bottom: r.bottom}; }"
    )

    page.mouse.move(field["x"], field["bottom"] + 30)
    page.mouse.move(field["x"], field["top"] - 60, steps=4)
    page.mouse.move(5, 300)
    page.wait_for_timeout(900)

    assert page.evaluate("() => window.__opened") == 0, "a pointer crossing the field showed it"


def test_a_slow_hand_reaches_the_tooltip_straight_up_and_on_a_diagonal(
    page: Page,
    live_server,
    details,  # noqa: F811
):
    """Hoverable for any hand, not only a quick one (review of #302): the gap between the
    field and its tooltip is covered by a strip that is part of the tooltip, as wide as the
    two together, so a pointer moving a pixel at a time from the label up onto the tooltip,
    or on a long diagonal from the far end of a wide box, never loses it. Each move is many
    small steps over more than a second."""
    page.set_viewport_size({"width": 1280, "height": 900})
    your_details(page, live_server.url)
    label = page.locator("label[for=id_headline]")
    label.scroll_into_view_if_needed()
    tip = page.locator("#id_headline_helptext")
    start = label.bounding_box()
    page.mouse.move(start["x"] + 30, start["y"] + 3)
    expect(tip).to_be_visible()
    page.evaluate(WATCH, "id_headline_helptext")
    onto = tip.bounding_box()

    x, y = start["x"] + 30, start["y"] + 3
    target = onto["y"] + onto["height"] / 2
    while y > target:
        y -= 1
        page.mouse.move(x, y)
        page.wait_for_timeout(30)
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__closed") == 0, "lost on the way straight up"
    expect(tip).to_be_visible()

    box = page.locator("#id_headline").bounding_box()
    far = (box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
    page.mouse.move(*far, steps=10)
    page.wait_for_timeout(200)
    expect(tip).to_be_visible()
    page.evaluate(WATCH, "id_headline_helptext")
    onto = tip.bounding_box()
    middle = (onto["x"] + onto["width"] / 2, onto["y"] + onto["height"] / 2)
    steps = 60
    for step in range(1, steps + 1):
        page.mouse.move(
            far[0] + (middle[0] - far[0]) * step / steps,
            far[1] + (middle[1] - far[1]) * step / steps,
        )
        page.wait_for_timeout(25)
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__closed") == 0, "lost on the diagonal"
    expect(tip).to_be_visible()


def test_one_sentence_two_menus_share_goes_to_the_one_in_use(page: Page, live_server, details):  # noqa: F811
    """*Your name*'s two menus are described by the card's one sentence (#309). It is shown
    for whichever of them has the focus."""
    your_details(page, live_server.url)
    tip = page.locator("#name-addressing-help")

    for menu in ("#id_form_of_address", "#id_pronouns"):
        page.locator(menu).focus()
        expect(tip).to_be_visible()
        drawn(page)
        (shown,) = page.evaluate(SHOWING)
        field = page.locator(menu).evaluate(
            "(el) => { const r = el.closest('.field').getBoundingClientRect();"
            " return {left: r.left, right: r.right}; }"
        )
        assert abs(shown["field"]["left"] - field["left"]) < 1, menu
        assert not overlap(shown["tip"], shown["field"]), menu
        assert 0 <= shown["tip"]["left"] and shown["tip"]["right"] <= shown["window"], menu
        expect(page.locator(menu)).to_have_accessible_description(tip.inner_text())


# ----------------------------------------------------- only where there is a clear place


@pytest.mark.parametrize("language", ["en", "el"])
def test_a_refused_row_is_never_under_a_tooltip(page: Page, live_server, details, language):  # noqa: F811
    """A refused telephone row, then the next row's *Phone* at 1280 (review of #302, H1):
    the next row's sentence lay over the first row's refusal. Refused is the page's, so no
    help on it is a tooltip until it is saved, and the refusal is in sight."""
    reads_in(details["numbers"][0].owner, language)
    page.set_viewport_size({"width": 1280, "height": 800})
    your_details(page, live_server.url)
    page.locator("#id_phone_numbers-0-number_1").fill("12")
    page.locator("main form button[type=submit]").first.click()
    error = page.locator("#id_phone_numbers-0-number_error")
    expect(error).to_be_visible()

    page.locator("#id_phone_numbers-1-number_1").focus()
    drawn(page)
    assert page.evaluate(SHOWING) == [], "a tooltip on a page that was refused"
    expect(page.locator("#id_phone_numbers-1-number_helptext")).to_be_visible()
    assert uncovered(page, "#id_phone_numbers-0-number_error")
    assert page.evaluate(BY_THE_RULE) == []


@pytest.mark.parametrize("language", ["en", "el"])
def test_a_refused_name_keeps_the_cards_sentence_in_sight(
    page: Page,
    live_server,
    details,  # noqa: F811
    language,
):
    """*Your name* refused at 320 (review of #302, H1): the card's sentence is the tooltip
    of both menus, and lay over *Last name* and its refusal. On a refused page it is the
    sentence under the title, and nothing lies over the refusal."""
    reads_in(details["numbers"][0].owner, language)
    page.set_viewport_size({"width": 320, "height": 800})
    your_details(page, live_server.url)
    page.locator("#id_last_name").fill("")
    page.locator("main form button[type=submit]").first.click()
    expect(page.locator("#id_last_name_error")).to_be_visible()

    page.locator("#id_pronouns").focus()
    drawn(page)
    assert page.evaluate(SHOWING) == []
    sentence = page.locator("#name-addressing-help")
    expect(sentence).to_be_visible()
    assert sentence.get_attribute("popover") is None
    page.locator("#id_last_name_error").scroll_into_view_if_needed()
    assert uncovered(page, "#id_last_name_error")


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("width", [1280, 768])
def test_a_tooltip_never_lies_over_a_mark_under_a_row(
    page: Page,
    live_server,
    details,  # noqa: F811
    language,
    width,
):
    """A number the plan cannot place wears *Not checked* under its row, and the next row's
    sentence lay over it (review of #302, H1). By focus and by the pointer, at 1280 and 768,
    the sentence is somewhere clear or under its field."""
    from postulo.core.models import PhoneNumber

    first = details["numbers"][0]
    PhoneNumber.objects.filter(pk=first.pk).update(number="3949", normalised="")
    reads_in(first.owner, language)
    page.set_viewport_size({"width": width, "height": 800})
    your_details(page, live_server.url)
    expect(page.locator("#section-phones [data-phone-mark]").first).to_be_visible()

    page.locator("#id_phone_numbers-1-number_1").focus()
    drawn(page)
    assert page.evaluate(BY_THE_RULE) == []
    page.locator("#id_phone_numbers-1-number_1").blur()

    page.locator("label[for=id_phone_numbers-1-number_1]").hover()
    page.wait_for_timeout(500)
    drawn(page)
    assert page.evaluate(BY_THE_RULE) == []


@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "spaced"])
@pytest.mark.parametrize("key", ["Tab", "Shift+Tab"])
def test_in_a_window_400_percent_zoom_leaves_every_stop_is_by_the_rule(
    page: Page,
    live_server,
    details,  # noqa: F811
    key,
    spacing,
):
    """A 1280 by 1024 window at 400% is 320 by 256 (review of #302, H2): a tooltip lay over
    its own field, ran out of the window, and lay over the next stop. Walked from the top
    to *Save* and back, with and without the text-spacing override, every tooltip showing is
    in a clear place, and where none is the help is drawn under its field."""
    page.set_viewport_size({"width": 320, "height": 256})
    your_details(page, live_server.url)
    if spacing:
        page.evaluate(ADOPT, TEXT_SPACING)
    page.locator(
        "[data-picture] [data-help-mark]" if key == "Tab" else "main form button[type=submit]"
    ).first.focus()

    failures: list[str] = []
    in_flow = 0
    for _stop in range(200):
        drawn(page)
        here = page.evaluate(FOCUSED)
        assert here, "the focus left the page"
        failures += page.evaluate(BY_THE_RULE)
        in_flow += page.evaluate("() => document.querySelectorAll('[data-tooltip-in-flow]').length")
        if here["last" if key == "Tab" else "first"]:
            break
        page.keyboard.press(key)
    else:
        pytest.fail(f"{key} never reached the end of the form")

    assert not failures, "\n".join(sorted(set(failures)))
    assert in_flow, "no help went under its field in a window this short"
    assert not page.evaluate(SIDEWAYS)


def test_a_tooltip_never_lies_over_the_masthead(page: Page, live_server, details):  # noqa: F811
    """The masthead floats, and a field scrolled up towards it with the focus in it took
    its tooltip over the masthead (review of #302, L5). The window a tooltip has is the part
    under the masthead: as the field comes up to it the tooltip goes elsewhere, and is
    never over it."""
    page.set_viewport_size({"width": 1280, "height": 600})
    your_details(page, live_server.url)
    page.locator("#id_location").focus()
    page.mouse.move(640, 300)
    for top in (240, 160, 130, 110, 90):
        page.evaluate(
            "(top) => { const field = document.querySelector('#id_location').closest('.field');"
            " window.scrollBy(0, field.getBoundingClientRect().top - top); }",
            top,
        )
        page.wait_for_timeout(100)
        drawn(page)
        assert page.evaluate(BY_THE_RULE) == [], top


def test_the_first_fields_tooltip_leaves_its_cards_question_mark_alone(
    page: Page,
    live_server,
    details,  # noqa: F811
):
    """The sentence of *Your name* ran past its card's edge over the card's question mark,
    and a press there only put the tooltip away (review of #302, M2). The mark is somewhere
    no tooltip goes: one stops short of it or takes another place, and a press on the mark
    opens its drawer."""
    page.set_viewport_size({"width": 1280, "height": 800})
    your_details(page, live_server.url)
    page.locator("#id_pronouns").focus()
    drawn(page)
    assert page.evaluate(BY_THE_RULE) == []
    mark = page.locator("#section-name [data-help-mark]")
    assert uncovered(page, "#section-name [data-help-mark]")
    page.mouse.click(*centre_of(page, "#section-name [data-help-mark]"))
    expect(page.locator("#help-your-name > div")).to_be_visible()
    page.keyboard.press("Escape")
    expect(mark).to_be_focused()


def test_a_finger_on_the_first_field_can_still_reach_the_question_mark(
    browser: Browser,
    live_server,
    details,  # noqa: F811
):
    """At 360 pixels a tap on *Headline* showed a tooltip over the *Contact block* title and
    its question mark, and a tap on the mark opened nothing (review of #302, M2)."""
    context = browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 360, "height": 640}
    )
    page = context.new_page()
    try:
        your_details(page, live_server.url)
        page.locator("#id_headline").tap()
        drawn(page)
        assert page.evaluate(BY_THE_RULE) == []
        assert uncovered(page, "#section-contact [data-help-mark]")
        page.touchscreen.tap(*centre_of(page, "#section-contact [data-help-mark]"))
        expect(page.locator("#help-contact-block > div")).to_be_visible()
    finally:
        context.close()


#: The tooltip a select's field is described by: its id, or nothing.
TIP_OF = """(select) => {
  const ids = (select.getAttribute('aria-describedby') || '').split(/\\s+/);
  const tip = ids.map((id) => id && document.getElementById(id))
    .find((el) => el && el.matches('[data-tooltip]'));
  return tip ? tip.id : null;
}"""


@pytest.mark.parametrize("width", [320, 1280])
def test_a_fields_tooltip_stays_away_while_its_list_is_open(
    page: Page,
    live_server,
    details,  # noqa: F811
    width,
):
    """A select's list and its field's tooltip were both open at once, and at 320 pixels
    one lay over the other (#301 with #302). Opening the list puts the tooltip away at
    once, in the same breath as the press; it stays away while the list is open, for the
    focus and for a pointer resting on the list; and when the list has closed and the focus
    is back on the button, it shows again by the usual rule."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/accounts/profile/")
    select = page.locator("select[name=form_of_address]")
    button = button_of(select)
    tip_id = select.evaluate(TIP_OF)
    assert tip_id, "the form of address has a sentence of help"
    tip = page.locator(f"[id='{tip_id}']")
    panel = list_of(page, select)

    button.focus()
    expect(tip).to_be_visible()

    # Pressed from a script, so that nothing runs between the press and the question.
    both = button.evaluate(
        """(button) => {
            button.click();
            return document.querySelectorAll(':popover-open').length;
        }"""
    )
    assert both == 1, "the list and its field's tooltip were open together"
    expect(panel).to_be_visible()
    expect(tip).to_be_hidden()

    page.mouse.move(*centre_of(page, f"[id='{panel.get_attribute('id')}']"))
    page.wait_for_timeout(600)
    drawn(page)
    expect(tip).to_be_hidden()
    assert page.evaluate("() => document.querySelectorAll(':popover-open').length") == 1
    assert page.evaluate(BY_THE_RULE) == []

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(button).to_be_focused()
    expect(tip).to_be_visible()
    drawn(page)
    assert page.evaluate(BY_THE_RULE) == []

    # Opened with the keys, the same.
    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    drawn(page)
    expect(tip).to_be_hidden()
    assert page.evaluate(BY_THE_RULE) == []


def test_no_tooltip_lies_over_an_open_list(page: Page, live_server, details):  # noqa: F811
    """An open select list is on the list of what no tooltip may lie over (#301 with #302).
    *Headline*'s tooltip has its first place above the field. With the list of *Form of
    address* open and lying just there, the pointer resting on *Headline* shows its
    tooltip somewhere clear of the list -- or puts the help under the field."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{live_server.url}/accounts/profile/")
    field = page.locator("#id_headline")
    field.scroll_into_view_if_needed()
    page.evaluate("() => window.scrollBy(0, -200)")

    page.locator("label[for=id_headline]").hover()
    tip = page.locator("#id_headline_helptext")
    expect(tip).to_be_visible()
    drawn(page)
    first = tip.bounding_box()
    page.mouse.move(0, 0)
    expect(tip).to_be_hidden()

    # The list opened, and put where that tooltip was: what a list is drawn over is not up
    # to the tooltips, and somewhere a list will open there.
    panel = open_list(page, page.locator("select[name=form_of_address]"))
    panel.evaluate(
        """(panel, at) => {
            for (const [name, value] of Object.entries({
                position: 'fixed', inset: 'auto', margin: '0',
                left: at.x + 'px', top: at.y + 'px',
                width: at.width + 'px', height: at.height + 'px',
            })) panel.style.setProperty(name, value, 'important');
        }""",
        first,
    )
    page.locator("label[for=id_headline]").hover()
    page.wait_for_timeout(600)
    drawn(page)
    expect(panel).to_be_visible()
    assert page.evaluate(BY_THE_RULE) == []


@pytest.mark.parametrize("language", ["en", "de", "el"])
@pytest.mark.parametrize("width", [320, 1280])
def test_tabbing_down_your_details_never_covers_what_is_being_read(
    page: Page,
    live_server,
    details,  # noqa: F811
    language,
    width,
):
    """Tab from the top of the form to *Save*. At every stop, each tooltip showing is in a
    clear place by the rule, and clear of the next thing Tab reaches -- its label and its
    control -- so the page can be read a step ahead of the focus. Nothing scrolls sideways
    on the way."""
    sign_in(page, live_server.url)
    reads_in(details["numbers"][0].owner, language)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/accounts/profile/")
    page.locator("[data-picture] [data-help-mark]").focus()

    failures: list[str] = []
    seen: set[str] = set()
    before: list[dict] = []
    for _stop in range(140):
        drawn(page)
        here = page.evaluate(FOCUSED)
        assert here, "the focus left the page"
        for was in before:
            for part in ("own", "field", "label"):
                if here[part] and overlap(was["tip"], here[part]):
                    failures.append(f"{was['id']} lies over the next stop, {here['what']}")
        showing = page.evaluate(SHOWING)
        failures += page.evaluate(BY_THE_RULE)
        for shown in showing:
            seen.add(shown["id"])
            if shown["cut"]:
                failures.append(f"{shown['id']} cuts its own words short")
        if page.evaluate(SIDEWAYS):
            failures.append(f"the page scrolls sideways at {here['what']}")
        if here["last"]:
            break
        before = showing
        page.keyboard.press("Tab")
    else:
        pytest.fail("Tab never reached Save")

    assert not failures, "\n".join(sorted(set(failures)))
    # The walk met the tooltips it is about: a field's, a row's and a card's.
    assert {"id_headline_helptext", "id_phone_numbers-0-number_helptext"} <= seen, seen
    assert "name-addressing-help" in seen, seen


def test_a_tooltip_grows_with_wider_text_spacing(page: Page, live_server, details):  # noqa: F811
    """SC 1.4.12: under the criterion's own override the sentence takes more lines, and the
    box takes them with it. Nothing is cut, nothing leaves the window, and it is still
    clear of its field."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{live_server.url}/accounts/profile/")
    number = page.locator("#id_phone_numbers-0-number_1")
    tip = page.locator("#id_phone_numbers-0-number_helptext")

    number.focus()
    expect(tip).to_be_visible()
    drawn(page)
    box = tip.bounding_box()
    plain = box["width"] * box["height"]

    page.evaluate(ADOPT, TEXT_SPACING)
    number.blur()
    number.focus()
    expect(tip).to_be_visible()
    drawn(page)
    (shown,) = page.evaluate(SHOWING)
    # By area: where the room it is given differs, so does its width.
    box = tip.bounding_box()
    assert box["width"] * box["height"] > plain, "wider spacing took no more room"
    assert not shown["cut"], "the tooltip cuts its words short under the override"
    assert page.evaluate(BY_THE_RULE) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_a_tooltip_goes_back_to_being_one_when_the_window_widens(page: Page, live_server, details):  # noqa: F811
    """The help that went under its field in a window with no room is a tooltip again once
    the window is another width: the room is looked for again."""
    page.set_viewport_size({"width": 320, "height": 256})
    your_details(page, live_server.url)
    page.locator("#id_location").focus()
    drawn(page)
    help_text = page.locator("#id_location_helptext")
    expect(help_text).to_have_attribute("data-tooltip-in-flow", "")
    expect(help_text).to_be_visible()

    page.locator("#id_headline").focus()
    page.set_viewport_size({"width": 1280, "height": 800})
    drawn(page)
    expect(help_text).to_be_hidden()
    page.locator("#id_location").focus()
    expect(help_text).to_be_visible()
    expect(help_text).to_have_attribute("popover", "manual")
    assert page.evaluate(BY_THE_RULE) == []


def test_a_tooltip_has_an_edge_in_forced_colours(page: Page, live_server, details):  # noqa: F811
    """A high-contrast theme throws the dark ground away, and the border is then all that
    says where the box is."""
    page.emulate_media(forced_colors="active")
    your_details(page, live_server.url)
    page.locator("#id_headline").focus()
    tip = page.locator("#id_headline_helptext")
    expect(tip).to_be_visible()

    edge = tip.evaluate(
        "(el) => { const s = getComputedStyle(el);"
        " return {colour: s.borderTopColor, width: s.borderTopWidth, style: s.borderTopStyle}; }"
    )
    assert edge["style"] == "solid" and edge["width"] != "0px", edge
    assert edge["colour"] not in ("rgba(0, 0, 0, 0)", "transparent"), edge


# ----------------------------------------------------- the question mark and the drawer


def test_the_question_mark_shows_one_sentence_and_opens_the_cards_help(
    page: Page,
    live_server,
    details,  # noqa: F811
):
    """Focus shows the card's one sentence. Pressing it opens the drawer in place of
    following the link: modal, at the inline end, with the focus on *Close* and kept
    inside. Escape, *Close* and a press outside each close it, and each time the focus is
    back on the question mark. The page is never left. Where the script takes the link
    over it is a button, it no longer opens a new tab, and its name no longer says so."""
    your_details(page, live_server.url)
    address = page.url
    mark = page.get_by_role("button", name="Help: Telephone numbers", exact=True)
    sentence = page.locator("#section-phones [data-help-summary]")
    drawer = page.locator("#help-telephone-numbers")
    panel = drawer.locator(":scope > div")

    expect(mark).to_have_attribute("aria-haspopup", "dialog")
    assert mark.get_attribute("target") is None
    expect(mark).to_have_accessible_description(NUMBERS)
    expect(sentence).to_be_hidden()
    mark.focus()
    expect(sentence).to_be_visible()
    expect(sentence).to_have_text(NUMBERS)

    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    expect(sentence).to_be_hidden()
    assert drawer.evaluate("(dialog) => dialog.matches(':modal')")
    expect(page.get_by_role("dialog", name="Help: Telephone numbers")).to_be_visible()
    expect(drawer.get_by_role("button", name="Close")).to_be_focused()
    expect(panel).to_contain_text("is kept once on this instance.")
    box = panel.bounding_box()
    window = page.viewport_size
    assert abs(box["x"] + box["width"] - window["width"]) < 1, "the drawer is not at the inline end"
    assert box["x"] > 0 and box["y"] == 0 and abs(box["height"] - window["height"]) < 1, box
    assert (
        drawer.evaluate("(dialog) => getComputedStyle(dialog, '::backdrop').backgroundColor")
        != "rgba(0, 0, 0, 0)"
    ), "the page behind a modal is out of reach, and undimmed"

    # The page behind is inert: Tab goes round the drawer's own two stops, by way of the
    # browser's own controls, and the question mark cannot take the focus even when asked.
    for _press in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.activeElement === document.body"
            " || !!document.activeElement.closest('#help-telephone-numbers')"
        ), "focus left the drawer"
    assert not mark.evaluate("(link) => { link.focus(); return link.matches(':focus'); }")
    drawer.get_by_role("button", name="Close").focus()

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(mark).to_be_focused()

    page.keyboard.press("Enter")
    expect(panel).to_be_visible()
    drawer.get_by_role("button", name="Close").click()
    expect(panel).to_be_hidden()
    expect(mark).to_be_focused()

    mark.click()
    expect(panel).to_be_visible()
    # Inside the panel is not outside it: a press on the words leaves it open.
    panel.locator("p").first.click()
    expect(panel).to_be_visible()
    page.mouse.click(20, 300)
    expect(panel).to_be_hidden()
    expect(mark).to_be_focused()
    assert page.url == address


def test_space_presses_the_question_mark_and_scrolls_nothing(page: Page, live_server, details):  # noqa: F811
    """Taken over, the question mark is a button, and Space presses it (review of #302, L4):
    as a link, Space scrolled the page and opened nothing. Held down, it opens the drawer
    once."""
    page.set_viewport_size({"width": 1280, "height": 800})
    your_details(page, live_server.url)
    mark = page.locator("#section-phones [data-help-mark]")
    expect(mark).to_have_attribute("role", "button")
    mark.focus()
    drawn(page)
    down = page.evaluate("() => window.scrollY")

    page.keyboard.down(" ")
    page.keyboard.down(" ")
    page.keyboard.down(" ")
    page.keyboard.up(" ")

    panel = page.locator("#help-telephone-numbers > div")
    expect(panel).to_be_visible()
    expect(
        page.locator("#help-telephone-numbers").get_by_role("button", name="Close")
    ).to_be_focused()
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    assert page.evaluate("() => window.scrollY") == down, "Space scrolled the page"


def test_three_cards_share_one_drawer_and_each_gets_the_focus_back(
    page: Page,
    live_server,
    details,  # noqa: F811
):
    """Social profiles, code repositories and websites share their help: three question
    marks, each named by its card, one drawer."""
    your_details(page, live_server.url)
    panel = page.locator("#help-links > div")

    for card in ("Social profiles", "Code repositories", "Websites"):
        mark = page.get_by_role("button", name=f"Help: {card}", exact=True)
        mark.focus()
        page.keyboard.press("Enter")
        expect(panel).to_be_visible()
        expect(
            page.get_by_role("dialog", name="Help: Social profiles, code repositories and websites")
        ).to_be_visible()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(mark).to_be_focused()


def test_the_drawer_fits_the_window_400_percent_zoom_leaves(page: Page, live_server, details):  # noqa: F811
    """A 1280 by 1024 window at 400% is 320 by 256. The drawer is inside it, its body
    scrolls and the keyboard scrolls it; and under the text-spacing override *Close* keeps
    the 24 pixels a target needs beside a heading that wraps (review of #302, L3)."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 256})
    page.goto(f"{live_server.url}/accounts/profile/")
    page.evaluate(ADOPT, TEXT_SPACING)

    page.locator("#section-phones [data-help-mark]").click()
    drawer = page.locator("#help-telephone-numbers")
    panel = drawer.locator(":scope > div")
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320, box
    assert box["y"] >= 0 and box["y"] + box["height"] <= 256, box
    close = drawer.get_by_role("button", name="Close")
    target = close.bounding_box()
    assert target["width"] >= 24 and target["height"] >= 24, target
    body = drawer.locator("section")
    assert body.evaluate("(el) => el.scrollHeight > el.clientHeight"), "the help does not scroll"
    expect(close).to_be_focused()
    page.keyboard.press("Tab")
    expect(body).to_be_focused()
    page.keyboard.press("End")
    page.wait_for_function(
        "() => document.querySelector('#help-telephone-numbers section').scrollTop > 0"
    )
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_the_question_mark_sits_on_its_titles_line(page: Page, live_server, details):  # noqa: F811
    """In a card headed by a title, the question mark's middle is the middle of the title's
    first line (review of #302, L10), at a phone's width and a desktop's."""
    sign_in(page, live_server.url)
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{live_server.url}/accounts/profile/")
        for card in ("section-picture", "section-name", "section-contact"):
            off = page.locator(f"#{card} [data-help-mark]").evaluate(
                "(mark) => { const title = mark.closest('header').querySelector('h2');"
                " const words = document.createRange(); words.selectNodeContents(title);"
                " const line = words.getClientRects()[0]; const m = mark.getBoundingClientRect();"
                " return (m.top + m.height / 2) - (line.top + line.height / 2); }"
            )
            assert abs(off) <= 1, (width, card, off)


def test_the_drawer_and_the_tooltip_mirror_right_to_left(page: Page, live_server, details):  # noqa: F811
    """Read right to left, the inline end is the left: the question mark is at the left of
    its card, the drawer against the left of the window, and a field's tooltip starts at the
    field's right edge. The question mark itself is turned, as Arabic writes it."""
    sign_in(page, live_server.url)
    reads_in(details["numbers"][0].owner, "ar")
    page.goto(f"{live_server.url}/accounts/profile/")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")

    card = page.locator("#section-phones").bounding_box()
    mark = page.locator("#section-phones [data-help-mark]")
    at = mark.bounding_box()
    assert at["x"] + at["width"] / 2 < card["x"] + card["width"] / 2, "the mark did not mirror"
    turned = mark.locator("svg").evaluate("(icon) => getComputedStyle(icon).transform")
    assert turned.startswith("matrix(-1"), turned

    page.locator("#id_headline").focus()
    expect(page.locator("#id_headline_helptext")).to_be_visible()
    drawn(page)
    (shown,) = page.evaluate(SHOWING)
    assert abs(shown["tip"]["right"] - shown["field"]["right"]) < 1, shown
    assert not overlap(shown["tip"], shown["field"])

    mark.click()
    panel = page.locator("#help-telephone-numbers > div")
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box["x"] == 0 and box["x"] + box["width"] < page.viewport_size["width"], box


def test_a_touch_screen_gets_both(browser: Browser, live_server, details):  # noqa: F811
    """A finger does not hover. Touching a box gives it the focus, which is what shows its
    sentence; touching the question mark opens the card's help, and touching the page
    beside the drawer closes it."""
    context = browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 360, "height": 740}
    )
    page = context.new_page()
    try:
        your_details(page, live_server.url)
        tip = page.locator("#id_headline_helptext")
        expect(tip).to_be_hidden()
        page.locator("#id_headline").tap()
        expect(tip).to_be_visible()
        drawn(page)
        assert page.evaluate(BY_THE_RULE) == []

        page.get_by_role("button", name="Help: Telephone numbers").tap()
        panel = page.locator("#help-telephone-numbers > div")
        expect(panel).to_be_visible()
        expect(tip).to_be_hidden()
        beside = panel.bounding_box()["x"] / 2
        assert beside >= 4, "no strip of the page is left beside the drawer to touch"
        page.touchscreen.tap(beside, 300)
        expect(panel).to_be_hidden()
    finally:
        context.close()


# ---------------------------------------------------------------------- with scripts off


def test_with_scripts_off_the_help_is_on_the_page_and_the_question_mark_opens_a_tab(
    browser: Browser,
    live_server,
    details,  # noqa: F811
):
    """Nothing is hidden that no script will show: the sentence is under its field and the
    card's under its title, as they always were. The question mark is a link to a page
    holding the card's help, and it opens that page in a new tab and says so, so that what
    was typed on the form is still there (review of #302, M3). The page leads back to the
    card for somebody who opened it on its own."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        your_details(page, live_server.url)
        help_text = page.locator("#id_headline_helptext")
        expect(help_text).to_be_visible()
        assert help_text.get_attribute("popover") is None
        under = help_text.bounding_box()["y"]
        box = page.locator("#id_headline").bounding_box()
        assert under >= box["y"] + box["height"], "the help is not under its box"
        expect(page.locator("#section-phones [data-help-summary]")).to_be_visible()
        expect(page.locator("#name-addressing-help")).to_be_visible()
        expect(page.locator("#help-telephone-numbers > div")).to_be_hidden()

        page.locator("#id_headline").fill("Typed and not saved")
        mark = page.get_by_role("link", name="Help: Telephone numbers (opens in a new tab)")
        assert mark.get_attribute("aria-haspopup") is None
        with context.expect_page() as opened:
            mark.click()
        helped = opened.value
        helped.wait_for_load_state()
        expect(helped).to_have_url(
            f"{live_server.url}/help/telephone-numbers/"
            "?next=%2Faccounts%2Fprofile%2F%23section-phones"
        )
        expect(helped.get_by_role("heading", level=1)).to_have_text("Help: Telephone numbers")
        expect(helped.locator("[data-help-text]")).to_contain_text("is kept once on this instance.")
        expect(page.locator("#id_headline")).to_have_value("Typed and not saved")

        helped.get_by_role("link", name="Back to Your details").click()
        expect(helped).to_have_url(f"{live_server.url}/accounts/profile/#section-phones")
    finally:
        context.close()


# ------------------------------------------------------------------- axe, in both themes


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_tooltip_showing_and_the_drawer_open_have_no_violations(
    page: Page,
    live_server,
    details,  # noqa: F811
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    your_details(page, live_server.url)
    failures = []

    page.locator("#id_headline").focus()
    expect(page.locator("#id_headline_helptext")).to_be_visible()
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"a field's tooltip showing ({scheme})", found))

    mark = page.locator("#section-phones [data-help-mark]")
    mark.focus()
    expect(page.locator("#section-phones [data-help-summary]")).to_be_visible()
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"a card's sentence showing ({scheme})", found))

    page.keyboard.press("Enter")
    expect(page.locator("#help-telephone-numbers > div")).to_be_visible()
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"the drawer open ({scheme})", found))
    page.keyboard.press("Escape")

    for topic in ("your-picture", "links"):
        page.goto(f"{live_server.url}/help/{topic}/")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"/help/{topic}/ ({scheme})", found))
    assert not failures, "\n\n".join(failures)
