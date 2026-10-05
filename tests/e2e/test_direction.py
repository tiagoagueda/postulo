"""The interface under ``dir="rtl"``, in a real browser.

Postulo offers no right-to-left language yet, and that is exactly why this exists. The
``dir`` attribute has been emitted since the first release and has been ``ltr`` on every
page ever rendered, so what the interface did under ``rtl`` was unknown rather than
known-good. #70 brings Arabic; this is the work that has to be true before it arrives.

The pseudo-locale is a real language tag on a profile with no catalogue behind it. The
words stay English, which makes the layout easier to judge rather than harder: what is
being checked is which edge things sit against, and reading the labels while you check is
a help.
"""

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e

#: Enough of the application to see every kind of layout that names an edge.
PAGES = (
    "/",
    "/applications/",
    "/applications/?view=board",
    "/applications/?view=board&status=applied",
    "/jobs/companies/",
    "/documents/cvs/",
    "/career/",
    "/?arrange=1",
    "/server/plugins/",
    "/settings/language/",
)


@pytest.fixture
def right_to_left(furnished):  # noqa: F811
    """The signed-in account reads Postulo in Arabic. No catalogue; the words stay English."""
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    return furnished


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_page_has_no_violations_right_to_left(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    right_to_left,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)

    failures = []
    for path in PAGES:
        page.goto(f"{base}{path}")
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} rtl ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_the_action_group_moves_to_the_other_end(live_server, page: Page, right_to_left):
    """`ms-auto` is what puts *Record an application* at the far end of a heading row.

    In Arabic the far end is the other one, and this is the check that says so in pixels
    rather than in class names: the button sits left of the heading, which under `ltr` it
    never does.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/?view=board")

    heading = page.get_by_role("heading", name="Applications").bounding_box()
    action = page.get_by_role("link", name="Record an application").bounding_box()

    assert action["x"] < heading["x"], "the actions should sit at the reading-end edge"


def test_the_board_starts_at_the_reading_edge(live_server, page: Page, right_to_left):
    """Columns are a horizontally scrolling row, so the first status must be nearest.

    Flexbox is direction-aware, so this needs no code of its own -- which is worth a test
    precisely because it would be easy to "fix" it later with something that breaks it.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/?view=board")

    columns = page.locator("[data-board-column]")
    expect(columns.first).to_be_visible()
    first = columns.first.bounding_box()
    last = columns.last.bounding_box()

    assert first["x"] > last["x"], "the earliest status should be at the reading-start edge"


def test_the_skip_link_lands_on_the_reading_edge(live_server, page: Page, right_to_left):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")
    page.keyboard.press("Tab")

    skip = page.get_by_role("link", name="Skip to content")
    expect(skip).to_be_focused()
    box = skip.bounding_box()
    width = page.viewport_size["width"]

    assert box["x"] + box["width"] > width / 2, "the skip link should start at the reading edge"


def test_a_latin_name_keeps_its_own_direction(live_server, page: Page, right_to_left):
    """The name renders left to right inside a right-to-left line, which is what <bdi> is for."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/jobs/companies/")

    expect(page.get_by_text("Aperture Science").first).to_be_visible()
    assert page.locator("html").get_attribute("dir") == "rtl"


def test_a_handle_keeps_its_own_direction_in_a_right_to_left_page(
    live_server, page: Page, right_to_left
):
    """A handle is Latin text with an @, a colon and dots in it, and typed beside Arabic
    words it must read in the order it was written (#682): on the company page it sits in a
    `<bdi>`, and in the box on *Your details* the text is left to right whatever the page."""
    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/accounts/profile/")
    box = page.locator("#section-messaging input[name$='-handle']").first
    expect(box).to_have_value("@alex:example.org")
    assert page.locator("html").get_attribute("dir") == "rtl"
    assert box.evaluate("(box) => getComputedStyle(box).direction") == "ltr"

    page.goto(f"{base}/jobs/companies/{right_to_left['company'].pk}/")
    handle = page.locator("bdi", has_text="cave.42")
    expect(handle).to_be_visible()


@pytest.mark.parametrize("placed_by", ["anchor", "script"])
def test_a_menu_opens_from_its_triggers_reading_end(
    live_server, page: Page, right_to_left, placed_by
):
    """A menu's panel is lined up with the inline end of its trigger (#310), which under
    `rtl` is the trigger's left edge, and it runs on towards the reading start -- rightwards
    -- rather than off the window's left edge. The stylesheet says it in logical terms and
    the script, where there is no anchor positioning, reads the direction."""
    from .test_people_menu import NO_ANCHORS

    if placed_by == "script":
        page.context.add_init_script(NO_ANCHORS)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/")

    trigger = page.get_by_label("Account menu", exact=False)
    trigger.click()
    panel = page.locator("header [data-menu]:has([aria-label^='Account menu']) > [popover]")
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    from_ = trigger.bounding_box()

    assert abs(box["x"] - from_["x"]) <= 1, "its inline end, which is its left, on the trigger's"
    assert box["x"] + box["width"] <= page.viewport_size["width"], "and it stays in the window"
    assert 0 <= box["y"] - (from_["y"] + from_["height"]) <= 8, "below the trigger"


KNOB = """async (input) => {
    // The knob is the first flex item, so it starts at the input's inline-start content edge
    // and the paint moves it by `translate`; the pseudo-element has no box to ask for.
    // The knob slides when the switch changes: read it once the slide has finished.
    getComputedStyle(input, '::before').translate;
    await Promise.all(document.getAnimations().map((animation) => animation.finished));
    const rtl = getComputedStyle(input).direction === 'rtl';
    const knob = getComputedStyle(input, '::before');
    const box = input.getBoundingClientRect();
    const width = parseFloat(knob.width);
    const shift = parseFloat(knob.translate) || 0;
    const edge = rtl
        ? box.right - parseFloat(getComputedStyle(input).borderRightWidth)
            - parseFloat(getComputedStyle(input).paddingRight)
        : box.left + parseFloat(getComputedStyle(input).borderLeftWidth)
            + parseFloat(getComputedStyle(input).paddingLeft);
    const left = (rtl ? edge - width : edge) + shift;
    return {left, right: left + width, inputLeft: box.left, inputRight: box.right};
}"""


@pytest.mark.parametrize("on", [True, False])
def test_a_switch_knob_stays_inside_its_track(live_server, page: Page, right_to_left, on):
    """The knob's travel follows the reading direction (#517): it is the switch's cue to its
    state besides colour, and a physical `translate-x` drew it outside the track in Arabic."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/server/plugins/")

    switch = page.locator("input[role='switch']").first
    switch.evaluate("(input, on) => { input.checked = on; }", on)
    knob = switch.evaluate(KNOB)

    assert knob["left"] >= knob["inputLeft"], "the knob overhangs the track's left end"
    assert knob["right"] <= knob["inputRight"], "the knob overhangs the track's right end"
    # And it sits at the end that means this state: on is the inline-end, which
    # is the left in Arabic, and off the inline-start.
    near_left = knob["left"] - knob["inputLeft"] < knob["inputRight"] - knob["right"]
    assert near_left is on, "the knob is at the wrong end for the state"


def test_the_default_language_row_keeps_its_tag_whole(live_server, page: Page, right_to_left):
    """The grey tag on the first row of the language picker is isolated left to right, so a
    right-to-left page does not reorder `pt-PT` into `PT-pt` (#698)."""
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"default_language": "pt-PT"})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/language/")

    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    tag = page.locator("[data-language-list] bdi").first
    expect(tag).to_have_text("pt-PT")
    expect(tag).to_have_attribute("dir", "ltr")
    assert tag.evaluate("e => getComputedStyle(e).unicodeBidi") in ("isolate", "isolate-override")
