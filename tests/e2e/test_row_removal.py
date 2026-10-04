"""A row off *Your details* at once, asked about in a dialog (#303).

The server's half -- whose row, who inherits the primary, which number may not go -- is in
`tests/test_row_removal.py`. What needs a browser is everything that happens around the
question: the dialog opening by keyboard, as a modal with a script and as a popover without
one; the row going without the page reloading or losing what was typed elsewhere; where focus
lands once the button that was pressed has gone with its row; the star moving to the row that
inherited *Primary*; and the page with the dialog open passing axe in both themes, reflowing at
320 pixels and keeping its shapes under forced colours.

And what a second copy of the page does once a row has gone from the first: a second tab, and
the page the Back button brings back, both still draw it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD
from tests.e2e.signing_in import sign_in

from .selects import drawn
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS, SPILLS
from .test_unsaved_work import asked_before_leaving

pytestmark = pytest.mark.e2e

NUMBERS = ("+351912345670", "+351912345671", "+351912345672")


@pytest.fixture
def details(applicant):
    """Three numbers, the first the primary; a social profile; an address; one identifier."""
    from postulo.accounts.models import PersonIdentifier
    from postulo.core.models import PhoneNumber, PostalAddress, WebLink

    profile = applicant.profile
    numbers = [
        PhoneNumber.objects.create(
            owner=applicant, holder=profile, number=digits, is_primary=index == 0
        )
        for index, digits in enumerate(NUMBERS)
    ]
    WebLink.objects.create(
        owner=applicant,
        holder=profile,
        kind=WebLink.Kind.SOCIAL,
        url="https://example.org/alex",
        is_primary=True,
    )
    PostalAddress.objects.create(
        owner=applicant,
        holder=profile,
        street="Rua do Exemplo 1",
        postcode="1000-001",
        municipality="Lisboa",
        country="PT",
        is_primary=True,
    )
    identifier = PersonIdentifier.objects.create(profile=profile, scheme="wikidata", value="Q95")
    return {"numbers": numbers, "identifier": identifier}


def panel(dialog):
    """What is drawn of a dialog. Basecoat's `<dialog>` is a box with no size whose one
    child is fixed in the middle of the window, so it is the child that is visible or not."""
    return dialog.locator(":scope > *")


def said(dialog) -> str:
    """The dialog's sentence as it is drawn. A `<noscript>` is drawn only where no script
    runs, and Playwright's text assertions leave one out either way, so this asks the page."""
    return dialog.locator("[id$=-description]").evaluate("(p) => p.innerText")


def removed(words: str) -> str:
    """The sentence said once a row has gone. The value sits between FIRST STRONG ISOLATE
    and POP DIRECTIONAL ISOLATE, which draw nothing and keep its direction to itself."""
    return f"\u2068{words}\u2069 removed."


def already_removed(words: str) -> str:
    return f"\u2068{words}\u2069 had already been removed."


def bin_for(page: Page, words: str):
    return page.get_by_role("button", name=f"Remove {words}", exact=True)


def open_dialog_by_keyboard(page: Page, words: str):
    """Focus the row's bin, and press Enter on it: the dialog opens with focus on Cancel."""
    bin_for(page, words).focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("alertdialog", name=f"Remove {words}?")
    expect(panel(dialog)).to_be_visible()
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    return dialog


def test_a_row_goes_by_keyboard_alone_and_focus_moves_to_the_next_row(
    page: Page, live_server, details
):
    """Open, Tab to *Remove*, Enter: the row is gone without a reload, the next row's first
    control has focus, the block says what went, and the sidebar counts one fewer."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.evaluate("() => { window.stillThisPage = true; }")

    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')"), "with a script it is modal"
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Remove", exact=True)).to_be_focused()
    page.keyboard.press("Enter")

    expect(bin_for(page, "+351 912 345 671")).to_have_count(0)
    expect(dialog).to_have_count(0)
    block = page.locator("#section-phones")
    expect(block.locator("[data-removed-said]")).to_have_text(removed("+351 912 345 671"))
    third = block.locator("li:not([hidden])").nth(1)
    expect(third.locator("input[name$='-number_1']")).to_have_value("912 345 672")
    # The row's first control, as a person meets it: its kind, at the button built for the
    # select (#301). One move of the focus, straight there.
    first_control = drawn(third.locator("input:not([type=hidden]), select").first)
    expect(first_control).to_be_focused()
    count = page.locator('[data-section-link="section-phones"] .badge')
    expect(count).to_have_text("2")
    assert page.evaluate("() => window.stillThisPage === true"), "the page was reloaded"

    from postulo.core.models import PhoneNumber

    assert not PhoneNumber.objects.filter(pk=details["numbers"][1].pk).exists()


def test_the_last_row_of_a_block_hands_focus_to_the_blocks_heading(
    page: Page, live_server, details
):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    open_dialog_by_keyboard(page, "Wikidata Q95")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    expect(bin_for(page, "Wikidata Q95")).to_have_count(0)
    heading = page.locator("#section-identifiers > legend")
    expect(heading).to_be_focused()
    expect(page.locator('[data-section-link="section-identifiers"] .badge')).to_have_text("0")
    # The kind the row held is free for the new row again (#307).
    expect(page.locator("select[name$='-scheme'] option[value=wikidata]").first).to_be_enabled()


def test_removing_the_primary_moves_the_star(page: Page, live_server, details):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    stars = page.locator("#section-phones input[name='phone_numbers-primary']")
    expect(stars.first).to_be_checked()

    open_dialog_by_keyboard(page, "+351 912 345 670")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    expect(bin_for(page, "+351 912 345 670")).to_have_count(0)
    heir = page.locator("#section-phones li:not([hidden])").first
    expect(heir.locator("input[name$='-number_1']")).to_have_value("912 345 671")
    expect(heir.locator("input[name='phone_numbers-primary']")).to_be_checked()


def test_what_was_typed_elsewhere_stays_and_is_still_guarded(page: Page, live_server, details):
    """The removal is its own request, so nothing typed on the page is sent, saved or lost --
    and leaving the page afterwards still asks, because the typing is still not saved (#258)."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.locator("input[name=headline]").fill("Typed and not saved")

    open_dialog_by_keyboard(page, "+351 912 345 672")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(bin_for(page, "+351 912 345 672")).to_have_count(0)

    expect(page.locator("input[name=headline]")).to_have_value("Typed and not saved")
    asked = asked_before_leaving(page, lambda: page.goto(f"{live_server.url}/applications/"))
    assert asked, "removing a row became a way round the unsaved-work guard"


def test_the_page_saves_after_a_row_went_at_once(page: Page, live_server, details):
    """What the row left behind -- its key, and its removal -- keeps the formset whole."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    open_dialog_by_keyboard(page, "+351 912 345 671")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(bin_for(page, "+351 912 345 671")).to_have_count(0)

    page.locator("input[name=headline]").fill("Saved after a removal")
    page.get_by_role("button", name="Save", exact=True).click()

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    from postulo.core.models import PhoneNumber

    applicant = details["numbers"][0].owner
    applicant.profile.refresh_from_db()
    assert applicant.profile.headline == "Saved after a removal"
    kept = PhoneNumber.objects.filter(owner=applicant).values_list("number", flat=True)
    assert sorted(kept) == [NUMBERS[0], NUMBERS[2]]


@pytest.mark.parametrize("how", ["Cancel", "Escape"])
def test_cancel_and_escape_leave_the_row_and_give_focus_back(page: Page, live_server, details, how):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")
    if how == "Cancel":
        page.keyboard.press("Enter")  # Cancel has the focus
    else:
        page.keyboard.press("Escape")

    expect(panel(dialog)).to_be_hidden()
    expect(bin_for(page, "+351 912 345 671")).to_be_focused()
    from postulo.core.models import PhoneNumber

    assert PhoneNumber.objects.filter(pk=details["numbers"][1].pk).exists()


def test_with_scripts_off_the_dialog_opens_by_itself_and_the_row_goes(
    browser: Browser, live_server, details
):
    """No script: the bin's `popovertarget` opens the dialog, Escape and Cancel close it, and
    confirming posts the dialog's own form and comes back to the block with a message."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        dialog = page.locator(f"#remove-number-{details['numbers'][1].pk}")

        bin_for(page, "+351 912 345 671").click()
        expect(panel(dialog)).to_be_visible()
        assert dialog.evaluate("(dialog) => dialog.matches(':popover-open')")
        expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
        assert "Anything else you have changed on this page" in said(dialog)
        page.keyboard.press("Escape")
        expect(panel(dialog)).to_be_hidden()

        bin_for(page, "+351 912 345 671").click()
        dialog.get_by_role("button", name="Cancel").click()
        expect(panel(dialog)).to_be_hidden()

        bin_for(page, "+351 912 345 671").click()
        dialog.get_by_role("button", name="Remove", exact=True).click()

        expect(page).to_have_url(re.compile(r"/accounts/profile/#section-phones$"))
        expect(page.get_by_text(removed("+351 912 345 671"))).to_be_visible()
        expect(bin_for(page, "+351 912 345 671")).to_have_count(0)
        from postulo.core.models import PhoneNumber

        assert not PhoneNumber.objects.filter(pk=details["numbers"][1].pk).exists()
    finally:
        context.close()


def test_with_scripts_on_the_sentence_about_unsaved_work_is_not_said(
    page: Page, live_server, details
):
    """It is true only without a script, where confirming reloads the page."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")

    assert "cannot be put back" in said(dialog)
    assert "Anything else you have changed" not in said(dialog)
    # And what a screen reader is given as the dialog's description is that sentence alone:
    # neither the one for no script nor the one about the primary, which is another row's.
    expect(dialog).to_have_accessible_description(
        "It comes off your details now, without waiting for Save, and cannot be put back."
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_with_a_dialog_open_has_no_violations(
    page: Page,
    live_server,
    details,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    failures = []

    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"your details ({scheme})", found))
    open_dialog_by_keyboard(page, "+351 912 345 670")
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"your details with a dialog open ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_the_dialog_reflows_at_320_pixels(page: Page, live_server, details):
    """At 400% zoom the dialog is inside the window, nothing scrolls sideways, and no words
    run out of their box -- the value in the heading included."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto(f"{live_server.url}/accounts/profile/")
    assert page.evaluate(SPILLS) == []

    dialog = open_dialog_by_keyboard(page, "Rua do Exemplo 1, 1000-001, Lisboa, Portugal")
    box = dialog.evaluate(
        "(dialog) => { const r = dialog.firstElementChild.getBoundingClientRect();"
        " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; }"
    )
    assert box["left"] >= 0 and box["right"] <= 320, box
    assert box["top"] >= 0 and box["bottom"] <= 640, box
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"


def test_the_star_keeps_its_shape_in_forced_colours(page: Page, live_server, details):
    """The chosen star is filled and the others are outlines, so which row is primary is a
    shape as well as a colour -- and forced colours throw the colour away."""
    page.emulate_media(forced_colors="active")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    fills = page.evaluate("""() => [...document.querySelectorAll(
        '#section-phones .primary-choice')].map((label) => ({
            chosen: label.querySelector('input').checked,
            fill: getComputedStyle(label.querySelector('svg')).fill,
        }))""")
    chosen = [star["fill"] for star in fills if star["chosen"]]
    others = [star["fill"] for star in fills if not star["chosen"]]
    assert len(chosen) == 1 and chosen[0] != "none", fills
    assert others and all(fill == "none" for fill in others), fills


def test_the_star_and_the_bin_are_24_pixels(page: Page, live_server, details):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    sizes = page.evaluate("""() => [...document.querySelectorAll(
        '.primary-choice, [data-remove-trigger]')].map((el) => {
            const r = el.getBoundingClientRect();
            return [r.width, r.height];
        })""")
    assert sizes and all(w >= 24 and h >= 24 for w, h in sizes), sizes


def test_a_star_is_chosen_with_the_keyboard(page: Page, live_server, details):
    """Still radios: arrowing along the stars chooses the next one, as it always did."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    stars = page.locator("#section-phones input[name='phone_numbers-primary']")

    stars.first.focus()
    outline = page.evaluate(
        "() => getComputedStyle(document.activeElement.closest('label')).outlineStyle"
    )
    assert outline != "none", "the focus ring is drawn round the star"
    page.keyboard.press("ArrowDown")

    expect(stars.nth(1)).to_be_checked()
    expect(stars.first).not_to_be_checked()


def test_a_star_moved_and_not_saved_stays_where_it_was_put(page: Page, live_server, details):
    """The star follows the row that inherited *Primary* only when it went with the removed
    row; a choice made on the page and not saved yet is left as it was."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    stars = page.locator("#section-phones input[name='phone_numbers-primary']")
    page.locator("#section-phones .primary-choice").nth(2).click()
    expect(stars.nth(2)).to_be_checked()

    open_dialog_by_keyboard(page, "+351 912 345 671")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(bin_for(page, "+351 912 345 671")).to_have_count(0)

    expect(stars.nth(1)).to_be_checked()  # the third row, now the second
    expect(stars.first).not_to_be_checked()


def test_the_number_that_gets_you_back_in_is_refused_in_the_dialog(
    page: Page, live_server, details
):
    """Refused with the reason, where the question was asked, and the row stays."""
    from postulo.core import phone_numbers
    from postulo.core.models import PhoneNumber
    from tests.test_recovery_number import a_gateway

    way_in = details["numbers"][2]
    way_in.record_verified()
    with a_gateway():
        phone_numbers.set_recovery(way_in, owner=way_in.owner)
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")

        dialog = open_dialog_by_keyboard(page, "+351 912 345 672")
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")

        refusal = dialog.get_by_role("alert")
        expect(refusal).to_contain_text("Choose another number to get you back in first.")
        expect(panel(dialog)).to_be_visible()
        expect(bin_for(page, "+351 912 345 672")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(panel(dialog)).to_be_hidden()
    assert PhoneNumber.objects.filter(pk=way_in.pk).exists()


def test_the_dialog_goes_on_saying_who_hands_the_primary_on(page: Page, live_server, details):
    """ "The next one in the list becomes the primary" is true of one row, and which row that
    is changes as rows go without a reload: the sentence follows the primary to the row that
    inherited it, and stops being said when no other row is left to take it."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    hands_on = "The next one in the list becomes the primary."

    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")
    assert hands_on not in said(dialog)
    page.keyboard.press("Escape")

    dialog = open_dialog_by_keyboard(page, "+351 912 345 670")
    assert hands_on in said(dialog)
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(bin_for(page, "+351 912 345 670")).to_have_count(0)

    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")
    assert hands_on in said(dialog), "the row that inherited the primary hands it on in turn"
    page.keyboard.press("Escape")

    open_dialog_by_keyboard(page, "+351 912 345 672")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(bin_for(page, "+351 912 345 672")).to_have_count(0)

    dialog = open_dialog_by_keyboard(page, "+351 912 345 671")
    assert hands_on not in said(dialog), "nobody is left to take it"


#: One word, wider than a phone: a host is as long as its owner made it.
LONG_HOST = "averyveryverylongsubdomainnamethatdoesnotbreakanywhere.example.org"
#: What a link nobody named is called by: its address, less the scheme.
LONG_LINK = f"{LONG_HOST}/alex"


@pytest.fixture
def long_link(details):
    from postulo.core.models import WebLink

    applicant = details["numbers"][0].owner
    return WebLink.objects.create(
        owner=applicant,
        holder=applicant.profile,
        kind=WebLink.Kind.SOCIAL,
        url=f"https://{LONG_HOST}/alex",
    )


BOX = """(dialog) => {
  const box = dialog.firstElementChild;
  const r = box.getBoundingClientRect();
  return {left: r.left, right: r.right, needs: box.scrollWidth, has: box.clientWidth};
}"""


def test_a_value_wider_than_the_window_breaks_rather_than_scrolls(
    page: Page, live_server, details, long_link
):
    """The heading and the sentence said afterwards quote what somebody typed, and a host or
    an identifier is one word. At 320 pixels it breaks inside the dialog -- whose box scrolls,
    so words too wide for it would be behind a sideways scroll bar rather than out of it --
    and inside the block that says the row went."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto(f"{live_server.url}/accounts/profile/")

    dialog = open_dialog_by_keyboard(page, LONG_LINK)
    box = dialog.evaluate(BOX)
    assert box["left"] >= 0 and box["right"] <= 320, box
    assert box["needs"] <= box["has"] + 1, f"the dialog scrolls sideways inside itself: {box}"
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    expect(bin_for(page, LONG_LINK)).to_have_count(0)
    block = page.locator("#section-links-social")
    expect(block.locator("[data-removed-said]")).to_have_text(removed(LONG_LINK))
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"


def test_with_scripts_off_the_message_about_a_long_value_fits_at_320(
    browser: Browser, live_server, details, long_link
):
    """Without a script the sentence is a message at the top of the page drawn again."""
    context = browser.new_context(java_script_enabled=False, viewport={"width": 320, "height": 640})
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        dialog = page.locator(f"#remove-link-{long_link.pk}")

        bin_for(page, LONG_LINK).click()
        expect(panel(dialog)).to_be_visible()
        box = dialog.evaluate(BOX)
        assert box["needs"] <= box["has"] + 1, f"the dialog scrolls sideways inside itself: {box}"
        dialog.get_by_role("button", name="Remove", exact=True).click()

        expect(page).to_have_url(re.compile(r"/accounts/profile/#section-links-social$"))
        expect(page.get_by_text(removed(LONG_LINK))).to_be_visible()
        assert page.evaluate(SPILLS) == []
        assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], "the page scrolls sideways at 320"
    finally:
        context.close()


def take_off(page: Page, words: str) -> None:
    """Press the row's bin and confirm, with the pointer."""
    bin_for(page, words).click()
    dialog = page.get_by_role("alertdialog", name=f"Remove {words}?")
    dialog.get_by_role("button", name="Remove", exact=True).click()
    expect(bin_for(page, words)).to_have_count(0)


# ------------------------------------------------------- said so that it can be read


def test_with_scripts_off_a_refusal_is_drawn_where_it_can_be_read(
    browser: Browser, live_server, details
):
    """Refused with no script, the page is drawn again -- at its top, where the reason is.
    Sent to the row's block, the reason was a screen above what was in view and the block
    was unchanged, as though the button had done nothing."""
    from postulo.core import phone_numbers
    from postulo.core.models import PhoneNumber
    from tests.test_recovery_number import a_gateway

    way_in = details["numbers"][2]
    way_in.record_verified()
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        with a_gateway():
            phone_numbers.set_recovery(way_in, owner=way_in.owner)
            sign_in(page, live_server.url)
            page.goto(f"{live_server.url}/accounts/profile/")
            bin_for(page, "+351 912 345 672").click()
            dialog = page.locator(f"#remove-number-{way_in.pk}")
            dialog.get_by_role("button", name="Remove", exact=True).click()

            expect(page).to_have_url(f"{live_server.url}/accounts/profile/")
            refusal = page.get_by_role("alert").filter(
                has_text="Choose another number to get you back in first."
            )
            expect(refusal).to_be_in_viewport()
        assert PhoneNumber.objects.filter(pk=way_in.pk).exists()
    finally:
        context.close()


def test_a_refusal_in_the_dialog_is_drawn_as_an_alert(page: Page, live_server, details):
    """The vocabulary's alert in the error tone, not a bare paragraph: a box that is seen as
    well as heard. With nothing to say it is not drawn at all."""
    from postulo.core import phone_numbers
    from tests.test_recovery_number import a_gateway

    way_in = details["numbers"][2]
    way_in.record_verified()
    with a_gateway():
        phone_numbers.set_recovery(way_in, owner=way_in.owner)
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")

        dialog = open_dialog_by_keyboard(page, "+351 912 345 672")
        refusal = dialog.locator("[data-dialog-said]")
        expect(refusal).to_be_hidden()
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")

        expect(refusal).to_be_visible()
        expect(refusal).to_have_role("alert")
        expect(refusal).to_have_class(re.compile(r"(^|\s)alert(\s|$)"))
        expect(refusal).to_have_attribute("data-variant", "error")
        look = refusal.evaluate(
            "(alert) => { const s = getComputedStyle(alert);"
            " return {ground: s.backgroundColor, border: parseFloat(s.borderTopWidth)}; }"
        )
        assert look["ground"] != "rgba(0, 0, 0, 0)" and look["border"] >= 1, look
        # Still inside its box at 320 pixels, where the dialog is the window less a gutter.
        page.set_viewport_size({"width": 320, "height": 640})
        box = dialog.evaluate(BOX)
        assert box["left"] >= 0 and box["right"] <= 320, box
        assert box["needs"] <= box["has"] + 1, f"the dialog scrolls sideways inside itself: {box}"


#: Where the number's plus sign and its last digit are drawn, in the element holding the
#: sentence. Read left to right, the plus is to the left of the last digit.
PLUS_AND_LAST_DIGIT = """(element) => {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !node.data.includes('+')) { node = walker.nextNode(); }
  const at = (index) => {
    const range = document.createRange();
    range.setStart(node, index);
    range.setEnd(node, index + 1);
    const box = range.getBoundingClientRect();
    return {left: box.left, top: box.top};
  };
  return {plus: at(node.data.indexOf('+')), last: at(node.data.indexOf('671') + 2)};
}"""


def test_in_a_right_to_left_page_the_number_said_afterwards_reads_as_a_number(
    page: Page, live_server, details
):
    """The sentence quotes the value. With the page's direction right to left, a number that
    is not isolated is laid out last group first -- "671 345 912 351+". Isolated, it reads
    as it was typed, whatever the sentence around it does."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.evaluate("() => { document.documentElement.dir = 'rtl'; }")

    take_off(page, "+351 912 345 671")

    region = page.locator("#section-phones [data-removed-said]")
    expect(region).to_have_text(removed("+351 912 345 671"))
    drawn = region.evaluate(PLUS_AND_LAST_DIGIT)
    assert drawn["plus"]["top"] == drawn["last"]["top"], f"the number is on two lines: {drawn}"
    assert drawn["plus"]["left"] < drawn["last"]["left"], f"the number reads backwards: {drawn}"


def test_with_scripts_off_the_message_reads_as_a_number_right_to_left_too(
    browser: Browser, live_server, details
):
    """The same sentence as the message of the page drawn again."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/accounts/profile/")
        bin_for(page, "+351 912 345 671").click()
        dialog = page.locator(f"#remove-number-{details['numbers'][1].pk}")
        dialog.get_by_role("button", name="Remove", exact=True).click()
        message = page.get_by_role("status").filter(has_text="removed.")
        expect(message).to_be_attached()
        page.evaluate("() => { document.documentElement.dir = 'rtl'; }")

        drawn = message.evaluate(PLUS_AND_LAST_DIGIT)
        assert drawn["plus"]["top"] == drawn["last"]["top"], f"the number is on two lines: {drawn}"
        assert drawn["plus"]["left"] < drawn["last"]["left"], f"the number reads backwards: {drawn}"
    finally:
        context.close()


def test_focus_moves_first_and_the_block_says_what_went_once(page: Page, live_server, details):
    """A screen reader that drops what it is saying when focus moves would cut a sentence
    written before the move. So focus lands on the next row while the block is still silent,
    and the sentence is written afterwards -- one change to the region, so it is read once."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.evaluate("""() => {
        const region = document.querySelector('#section-phones [data-removed-said]');
        window.heard = {changes: 0, landed: 0, saidWhenFocusLanded: null};
        new MutationObserver((records) => { window.heard.changes += records.length; })
            .observe(region, {childList: true, characterData: true, subtree: true});
        document.addEventListener('focusin', (event) => {
            const row = event.target.closest('#section-phones li');
            if (row && !event.target.matches('[data-remove-trigger]')) {
                window.heard.landed += 1;
                window.heard.saidWhenFocusLanded = region.textContent;
            }
        });
    }""")

    open_dialog_by_keyboard(page, "+351 912 345 671")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    region = page.locator("#section-phones [data-removed-said]")
    expect(region).to_have_text(removed("+351 912 345 671"))
    page.wait_for_timeout(300)  # long enough for a second write to have happened
    heard = page.evaluate("() => window.heard")
    assert heard["landed"] == 1, heard
    assert heard["saidWhenFocusLanded"] == "", f"the block spoke before focus had moved: {heard}"
    assert heard["changes"] == 1, f"the sentence was written more than once: {heard}"


# ------------------------------------------------- a copy of the page that is out of date


def test_a_second_tab_takes_a_row_that_has_gone_off_its_own_copy(page: Page, live_server, details):
    """The row went in one tab, and the other still draws it. Its bin is told there is no
    such row -- which is what was asked for, not a failure -- so the row goes from that copy
    too, the block says it had already been removed, nothing typed is lost, and the page
    then saves."""
    from postulo.core.models import PhoneNumber

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    other = page.context.new_page()
    other.goto(f"{live_server.url}/accounts/profile/")
    take_off(page, "+351 912 345 671")

    other.locator("input[name=headline]").fill("Typed in the other tab")
    bin_for(other, "+351 912 345 671").click()
    dialog = other.get_by_role("alertdialog", name="Remove +351 912 345 671?")
    dialog.get_by_role("button", name="Remove", exact=True).click()

    expect(bin_for(other, "+351 912 345 671")).to_have_count(0)
    expect(dialog).to_have_count(0)
    block = other.locator("#section-phones")
    expect(block.locator("[data-removed-said]")).to_have_text(already_removed("+351 912 345 671"))
    expect(other.locator("[data-htmx-alert]")).to_be_empty()
    expect(other.locator('[data-section-link="section-phones"] .badge')).to_have_text("2")
    third = block.locator("li:not([hidden])").nth(1)
    expect(drawn(third.locator("input:not([type=hidden]), select").first)).to_be_focused()
    expect(other.locator("input[name=headline]")).to_have_value("Typed in the other tab")

    other.get_by_role("button", name="Save", exact=True).click()

    expect(other.get_by_text("Your details have been saved.")).to_be_visible()
    applicant = details["numbers"][0].owner
    applicant.profile.refresh_from_db()
    assert applicant.profile.headline == "Typed in the other tab"
    kept = PhoneNumber.objects.filter(owner=applicant).values_list("number", flat=True)
    assert sorted(kept) == [NUMBERS[0], NUMBERS[2]]


def test_a_second_tab_that_is_out_of_date_still_saves(page: Page, live_server, details):
    """The other tab's *Save*, without its bin being pressed: the copy still carries the row
    that went, and used to be refused for it with nothing on the page saying why, as often
    as it was sent. The row has already been removed; the rest is saved as typed."""
    from postulo.core.models import PhoneNumber

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    other = page.context.new_page()
    other.goto(f"{live_server.url}/accounts/profile/")
    take_off(page, "+351 912 345 670")  # the primary, whose star the other tab still shows

    other.locator("input[name=headline]").fill("Typed in the other tab")
    other.get_by_role("button", name="Save", exact=True).click()

    expect(other.get_by_text("Your details have been saved.")).to_be_visible()
    expect(bin_for(other, "+351 912 345 670")).to_have_count(0)
    applicant = details["numbers"][0].owner
    applicant.profile.refresh_from_db()
    assert applicant.profile.headline == "Typed in the other tab"
    kept = PhoneNumber.objects.filter(owner=applicant).order_by("pk")
    assert [row.number for row in kept] == [NUMBERS[1], NUMBERS[2]]
    assert [row.is_primary for row in kept] == [True, False]


def test_the_page_the_back_button_brings_back_can_still_be_used(page: Page, live_server, details):
    """Back after a removal. The browser may bring the page back as it was first drawn, row
    and bin included; that bin then finds the row gone and takes it off this copy, and the
    page saves whichever copy it is."""
    from postulo.core.models import PhoneNumber

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    take_off(page, "+351 912 345 671")
    page.goto(f"{live_server.url}/applications/")
    page.go_back()
    expect(page).to_have_url(f"{live_server.url}/accounts/profile/")
    expect(page.locator("#section-phones")).to_be_visible()

    brought_back = bin_for(page, "+351 912 345 671")
    if brought_back.count():
        brought_back.click()
        dialog = page.get_by_role("alertdialog", name="Remove +351 912 345 671?")
        dialog.get_by_role("button", name="Remove", exact=True).click()
        said = page.locator("#section-phones [data-removed-said]")
        expect(said).to_have_text(already_removed("+351 912 345 671"))
    expect(brought_back).to_have_count(0)

    page.locator("input[name=headline]").fill("Saved after Back")
    page.get_by_role("button", name="Save", exact=True).click()

    expect(page.get_by_text("Your details have been saved.")).to_be_visible()
    applicant = details["numbers"][0].owner
    applicant.profile.refresh_from_db()
    assert applicant.profile.headline == "Saved after Back"
    kept = PhoneNumber.objects.filter(owner=applicant).values_list("number", flat=True)
    assert sorted(kept) == [NUMBERS[0], NUMBERS[2]]


def test_signing_in_again_after_the_session_ended_leads_back_to_the_block(
    page: Page, live_server, details
):
    """Confirmed after the session had ended: the removal is refused and sign-in is asked
    for, and sign-in then sends the person on to the address that refused them -- with a
    GET, which removes nothing. It used to answer with a page that had nothing on it; it
    leads back to the row's block, and the row is still there to remove."""
    from postulo.core.models import PhoneNumber

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    page.context.clear_cookies(name="sessionid")

    bin_for(page, "+351 912 345 671").click()
    dialog = page.get_by_role("alertdialog", name="Remove +351 912 345 671?")
    dialog.get_by_role("button", name="Remove", exact=True).click()
    expect(page).to_have_url(re.compile(r"/accounts/login/\?next=.*/numbers/\d+/remove/$"))
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()

    expect(page).to_have_url(f"{live_server.url}/accounts/profile/#section-phones")
    expect(bin_for(page, "+351 912 345 671")).to_have_count(1)
    assert PhoneNumber.objects.filter(pk=details["numbers"][1].pk).exists()
