"""The identifier registry's settings, in a dialog and as a page (#311).

What the server does with the text -- what a scheme can say, what a pattern may be, who may
save it -- is in `tests/test_identifier_schemes.py`. What needs a browser is the two ways
in. With a script, the registry's row on *Plugins* opens a dialog: modal, the box holding
the focus, and a text that is refused comes back into the same dialog, still open over what
was typed, with the sentence that says which line and the caret put on that line. Without
one, the same control is a link to a page that does the same job by reloading.

Either way what was saved is then offered where the scheme says: on *Your details*.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

BADGE = {"key": "badge", "label": "Badge", "subjects": ["person"], "pattern": "[A-Z]{2}-\\d{4}"}
#: One name to a line, as somebody would type it. The pattern is on line 8.
GOOD = json.dumps([{**BADGE, "example": "AB-1234"}], indent=2)
BROKEN = json.dumps([{**BADGE, "pattern": "[A-Z]+"}], indent=2)
REFUSAL = "Line 8, scheme “badge”: the pattern cannot be used, at character 6."

BOX = "Schemes of this instance, in JSON"
#: The registry's button: the word, and the plugin it belongs to for whoever cannot see the row.
OPENER = re.compile(r"^Settings\s*: External identifiers$")
#: Which line of the box the caret is on, counted from one.
CARET_LINE = "(box) => box.value.slice(0, box.selectionStart).split('\\n').length"


@pytest.fixture
def administrator(applicant):
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save(update_fields=["is_staff", "is_superuser"])
    return applicant


def open_plugins(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")
    page.goto(f"{base}/server/plugins/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button").first.click()
        page.goto(f"{base}/server/plugins/")


def offered_on_your_details(page: Page, base: str) -> list[str]:
    page.goto(f"{base}/accounts/profile/")
    return page.locator("select[name='identifiers-0-scheme'] option").all_inner_texts()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_with_a_script_the_registrys_settings_are_a_dialog(
    page: Page,
    live_server,
    administrator,
    axe_source,  # noqa: F811
    scheme,
):
    base = live_server.url
    page.emulate_media(color_scheme=scheme)
    open_plugins(page, base)
    opener = page.get_by_role("link", name=OPENER)
    dialog = page.get_by_role("dialog", name="Identifiers of your own")
    drawn = page.locator("#settings-identifiers > *")
    box = dialog.get_by_label(BOX)

    # A link to the page, which says it opens a dialog only where a script makes it true.
    expect(opener).to_have_attribute("href", "/server/plugins/identifiers/")
    expect(opener).to_have_attribute("aria-haspopup", "dialog")
    expect(drawn).to_be_hidden()

    opener.focus()
    page.keyboard.press("Enter")
    expect(drawn).to_be_visible()
    expect(page).to_have_url(f"{base}/server/plugins/")
    assert page.locator("#settings-identifiers").evaluate("(dialog) => dialog.matches(':modal')")
    # The box is what the dialog was opened for, so it has the focus, not *Cancel*.
    expect(box).to_be_focused()

    # Escape closes it and gives focus back to the link; what was typed is still there.
    box.fill("[")
    page.keyboard.press("Escape")
    expect(drawn).to_be_hidden()
    expect(opener).to_be_focused()
    page.keyboard.press("Enter")
    expect(box).to_have_value("[")

    # A text that is refused comes back into the dialog, which stays open over it.
    box.fill(BROKEN)
    dialog.get_by_role("button", name="Save", exact=True).click()
    said = dialog.locator("#id_identifier_schemes_error")
    expect(said).to_contain_text(REFUSAL)
    expect(drawn).to_be_visible()
    expect(page).to_have_url(f"{base}/server/plugins/")
    expect(box).to_have_value(BROKEN)
    expect(box).to_be_focused()
    expect(box).to_have_attribute("aria-invalid", "true")
    assert "id_identifier_schemes_error" in box.get_attribute("aria-describedby").split()
    expect(said).to_have_attribute("role", "alert")
    # A box has no line numbers to count by: the caret is on the line the sentence names.
    assert box.evaluate(CARET_LINE) == 8
    # And nothing is left dimmed and announced as loading once the answer is in.
    expect(page.locator("#settings-identifiers [aria-busy='true']")).to_have_count(0)

    found = violations_on(page, axe_source)
    assert not found, describe(f"the registry's dialog, refused ({scheme})", found)

    # Put right and saved, it goes back to the page behind, which says so.
    box.fill(GOOD)
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved.")).to_be_visible()
    expect(page).to_have_url(f"{base}/server/plugins/")
    expect(drawn).to_be_hidden()

    assert "Badge" in offered_on_your_details(page, base)


def test_a_click_that_asks_for_a_new_tab_is_left_to_open_the_page(
    page: Page, live_server, administrator
):
    """The dialog is in place of following the link, and only of following it here."""
    open_plugins(page, live_server.url)
    opener = page.get_by_role("link", name=OPENER)

    with page.context.expect_page() as opened:
        opener.click(modifiers=["ControlOrMeta"])

    expect(page.locator("#settings-identifiers > *")).to_be_hidden()
    expect(opened.value).to_have_url(f"{live_server.url}/server/plugins/identifiers/")


def test_the_dialog_fits_a_phone(page: Page, live_server, administrator):
    """Wider than the others where the window has the room, and the window less a gutter
    where it has not: nothing scrolls the page sideways at 320 pixels."""
    open_plugins(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 700})
    page.get_by_role("link", name=OPENER).click()
    drawn = page.locator("#settings-identifiers > *")
    expect(drawn).to_be_visible()

    box = drawn.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]

    page.set_viewport_size({"width": 1440, "height": 900})
    assert drawn.bounding_box()["width"] > 32 * 16, "wide, where there is room"


def test_without_a_script_the_same_control_leads_to_a_page(
    browser: Browser, live_server, administrator
):
    base = live_server.url
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        open_plugins(page, base)
        opener = page.get_by_role("link", name=OPENER)
        assert opener.get_attribute("aria-haspopup") is None, "a link, and it says no more"

        opener.click()
        expect(page).to_have_url(f"{base}/server/plugins/identifiers/")
        expect(page.get_by_role("heading", name="Identifiers of your own", level=1)).to_be_visible()
        box = page.get_by_label(BOX)

        box.fill(BROKEN)
        page.locator("main form").get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(f"{base}/server/plugins/identifiers/")
        expect(page.locator("#id_identifier_schemes_error")).to_contain_text(REFUSAL)
        box = page.get_by_label(BOX)
        expect(box).to_have_value(BROKEN)
        expect(box).to_be_focused()
        expect(box).to_have_attribute("aria-invalid", "true")

        box.fill(GOOD)
        page.locator("main form").get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(f"{base}/server/plugins/")
        expect(page.get_by_text("Saved.")).to_be_visible()

        assert "Badge" in offered_on_your_details(page, base)
    finally:
        context.close()


# ----------------------------------------------- a text longer than the window (#311)
#
# The first tests use a text of nine lines, and nine lines hid this: the box grew with its
# text, so with a hundred the sentence that refused them, the caret on the line it named
# and *Save* were two screens further down, the script that scrolled the box to that line
# scrolled nothing, and the only thing seen to change was a label gone red. Now the box has
# a height of its own and scrolls inside itself, and what is wrong is said above it.

#: Thirteen schemes, one name to a line: more than a hundred lines, with the mistake in
#: the last scheme, where a box that grew with its text had it furthest from the window.
LONG = json.dumps(
    [{**BADGE, "key": f"k{number}"} for number in range(12)]
    + [{**BADGE, "key": "last", "pattern": "a+"}],
    indent=2,
)
LONG_LINE = LONG.split("\n").index('    "pattern": "a+"') + 1
LONG_REFUSAL = f"Line {LONG_LINE}, scheme “last”: the pattern cannot be used, at character 2."

#: Where things are once a text has been refused, measured in the window and in the box.
WHERE = r"""(root) => {
  const box = root.querySelector('textarea');
  const said = root.querySelector('#id_identifier_schemes_error');
  const style = getComputedStyle(box);
  const lineHeight = parseFloat(style.lineHeight);
  const line = box.value.slice(0, box.selectionStart).split('\n').length;
  const caretTop = parseFloat(style.paddingTop) + (line - 1) * lineHeight - box.scrollTop;
  const drawn = box.getBoundingClientRect();
  const sentence = said.querySelector('p').getBoundingClientRect();
  return {
    line: line,
    lines: box.value.split('\n').length,
    caretInsideTheBox: caretTop >= 0 && caretTop + lineHeight <= box.clientHeight,
    boxScrollsInsideItself: box.scrollHeight > box.clientHeight + 1 && box.scrollTop > 0,
    boxHeight: drawn.height,
    windowHeight: window.innerHeight,
    sentenceInTheWindow: sentence.top >= 0 && sentence.bottom <= window.innerHeight,
    sentenceBeforeTheBox: sentence.bottom <= drawn.top + 1,
    focused: document.activeElement === box,
    pageScrolledTo: Math.round(window.scrollY),
  };
}"""

IN_THE_WINDOW = r"""(element) => {
  const drawn = element.getBoundingClientRect();
  return drawn.top >= 0 && drawn.bottom <= window.innerHeight && drawn.width > 0;
}"""


@pytest.mark.parametrize("where", ["dialog", "page"])
@pytest.mark.parametrize("window", [(1280, 720), (320, 640)], ids=["1280x720", "320x640"])
def test_after_a_refusal_the_sentence_the_line_and_save_are_within_reach(
    page: Page, live_server, administrator, where, window
):
    """A hundred lines, refused on the last scheme. What is wrong is in the window, said
    before the box; the box has scrolled inside itself to the line the sentence names, with
    the caret on it; and *Save* is reached without the page moving under the dialog."""
    base = live_server.url
    width, height = window
    page.set_viewport_size({"width": width, "height": height})
    open_plugins(page, base)
    if where == "dialog":
        page.get_by_role("link", name=OPENER).click()
        root = page.locator("#settings-identifiers")
        save = root.get_by_role("button", name="Save", exact=True)
        behind = page.evaluate("() => Math.round(window.scrollY)")
    else:
        page.goto(f"{base}/server/plugins/identifiers/")
        root = page.locator("main")
        save = root.locator("form").get_by_role("button", name="Save", exact=True)
    box = root.get_by_label(BOX)

    # Before anything is refused: the box does not grow with what is typed into it.
    box.fill(LONG)
    assert box.evaluate("(box) => box.scrollHeight > box.clientHeight + 1"), "it scrolls"
    assert box.bounding_box()["height"] <= height / 2 + 1, "under half the window"

    save.click()
    expect(root.locator("#id_identifier_schemes_error")).to_contain_text(LONG_REFUSAL)
    # The caret is put on the line once the answer has landed and settled.
    page.wait_for_function(
        "(line) => { const box = document.querySelector("
        "'#settings-identifiers[open] textarea, main textarea');"
        " return box && box.hasAttribute('data-caret-placed') &&"
        " box.value.slice(0, box.selectionStart).split('\\n').length === line; }",
        arg=LONG_LINE,
    )
    page.wait_for_timeout(150)  # past htmx's own settling, which hands the focus over

    found = root.evaluate(WHERE)
    assert found["lines"] > 100 and found["line"] == LONG_LINE, found
    assert found["sentenceInTheWindow"], found
    assert found["sentenceBeforeTheBox"], found
    assert found["boxScrollsInsideItself"] and found["caretInsideTheBox"], found
    assert found["boxHeight"] <= found["windowHeight"] / 2 + 1, found
    assert found["focused"], found

    # *Save* is within reach: in the window, or brought into it without the page behind a
    # dialog being moved, and without the dialog closing.
    save.scroll_into_view_if_needed()
    assert save.evaluate(IN_THE_WINDOW)
    if where == "dialog":
        expect(page.locator("#settings-identifiers > *")).to_be_visible()
        assert page.evaluate("() => Math.round(window.scrollY)") == behind
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_without_a_script_the_sentence_is_above_the_box_and_in_the_window(
    browser: Browser, live_server, administrator
):
    """No script puts a caret anywhere. The browser gives the box the focus and brings it
    into view, and the sentences are what stands directly above it: the box is under half
    the window, so there is room for them."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False, viewport={"width": 320, "height": 640})
    page = context.new_page()
    try:
        open_plugins(page, base)
        page.goto(f"{base}/server/plugins/identifiers/")
        box = page.get_by_label(BOX)
        box.fill(LONG)
        page.locator("main form").get_by_role("button", name="Save", exact=True).click()

        said = page.locator("#id_identifier_schemes_error")
        expect(said).to_contain_text(LONG_REFUSAL)
        box = page.get_by_label(BOX)
        expect(box).to_be_focused()
        sentence, drawn = said.locator("p").first.bounding_box(), box.bounding_box()
        assert sentence["y"] >= 0 and sentence["y"] + sentence["height"] <= 640, sentence
        assert sentence["y"] + sentence["height"] <= drawn["y"] + 1, "before the box"
        assert drawn["height"] <= 320 + 1, "the box is under half the window"
    finally:
        context.close()


def test_the_text_as_it_is_kept_says_what_is_wrong_with_it_when_the_dialog_opens(
    page: Page,
    live_server,
    administrator,
    axe_source,  # noqa: F811
):
    """A text that reached the row past the page is used in part, and the dialog says
    which part is not, with the caret on the line -- placed when the dialog opens, because
    a box inside a dialog that is shut has no size to scroll."""
    from postulo.core.models import SiteSettings

    row = SiteSettings.get()
    row.identifier_schemes = LONG
    row.save()
    open_plugins(page, live_server.url)

    page.get_by_role("link", name=OPENER).click()
    dialog = page.get_by_role("dialog", name="Identifiers of your own")
    said = dialog.locator("#id_identifier_schemes_error")
    expect(said).to_contain_text("This is wrong in the text as it is kept.")
    expect(said).to_contain_text(LONG_REFUSAL)
    box = dialog.get_by_label(BOX)
    expect(box).to_be_focused()
    expect(box).to_have_attribute("aria-invalid", "true")
    assert box.evaluate(CARET_LINE) == LONG_LINE
    found = page.locator("#settings-identifiers").evaluate(WHERE)
    assert found["caretInsideTheBox"] and found["sentenceInTheWindow"], found

    found = violations_on(page, axe_source)
    assert not found, describe("the registry's dialog, over a kept text with a problem", found)


def test_an_identifier_its_kind_has_outgrown_is_marked_on_your_details(
    page: Page,
    live_server,
    administrator,
    axe_source,  # noqa: F811
):
    """Typed while its kind took it, and the kind's pattern changed since: the row is
    marked *Kept as it was* with what the kind expects now, the page saves over it, and
    the mark is drawn at 320 pixels without pushing the page sideways."""
    from postulo.accounts.models import PersonIdentifier
    from postulo.core.models import SiteSettings

    row = SiteSettings.get()
    row.identifier_schemes = GOOD
    row.save()
    PersonIdentifier.objects.create(profile=administrator.profile, scheme="badge", value="AB-1234")
    row.identifier_schemes = json.dumps([{**BADGE, "pattern": "[A-Z]{3}-\\d{4}"}], indent=2)
    row.save()

    base = live_server.url
    page.set_viewport_size({"width": 320, "height": 640})
    open_plugins(page, base)
    page.goto(f"{base}/accounts/profile/")
    mark = page.locator("[data-identifiers] [data-kept-as-it-was]")
    expect(mark).to_have_count(1)
    expect(mark).to_contain_text("Kept as it was")
    expect(mark).to_contain_text("That does not look like a Badge identifier.")
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]
    found = violations_on(page, axe_source)
    assert not found, describe("Your details, with an identifier kept as it was", found)

    page.locator("input[name=first_name]").fill("Alexandra")
    page.locator("main form").get_by_role("button", name="Save", exact=True).first.click()
    expect(page.locator("input[name=first_name]")).to_have_value("Alexandra")
    expect(page.locator("[data-identifiers] [data-kept-as-it-was]")).to_have_count(1)
    kept = PersonIdentifier.objects.get(profile=administrator.profile)
    assert (kept.scheme, kept.value) == ("badge", "AB-1234")
