"""`<c-dialog>` by itself, on the gallery, where it asks about nothing (#303).

*Your details* is the page that uses it, and `test_row_removal.py` walks it there. What is
checked here is the component any other page gets by writing the tag: with a script the
question is modal -- the page behind cannot be reached until it is answered -- and without
one the same button opens the same dialog as a popover. Either way focus starts on *Cancel*,
Escape and both answers close it, and focus goes back to the button that asked. The page is
dimmed only behind the modal, where it is out of reach; behind the popover it still works.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.conftest import PASSWORD
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


@pytest.fixture
def administrator(applicant):
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save(update_fields=["is_staff", "is_superuser"])
    return applicant


#: What is painted over the page behind the dialog. A browser's own answer is nothing.
SCRIM = "(dialog) => getComputedStyle(dialog, '::backdrop').backgroundColor"
NOTHING = "rgba(0, 0, 0, 0)"


def open_the_gallery(page: Page, base: str) -> None:
    sign_in(page, base)
    page.goto(f"{base}/server/design/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button").first.click()
        page.goto(f"{base}/server/design/")


def test_with_a_script_the_dialog_is_modal_and_gives_focus_back(
    page: Page, live_server, administrator
):
    open_the_gallery(page, live_server.url)
    opener = page.get_by_role("button", name="Open the dialog", exact=True)
    dialog = page.get_by_role("alertdialog", name="Remove this example?")
    drawn = page.locator("#design-dialog > *")

    opener.focus()
    page.keyboard.press("Enter")
    expect(drawn).to_be_visible()
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')")
    assert dialog.evaluate(SCRIM) != NOTHING, "the page behind a modal is out of reach, undimmed"
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    expect(dialog).to_have_accessible_description(
        "A confirmation asks one question and says what answering it does. Here, nothing."
    )

    # The page behind is inert: Tab stays among the dialog's two buttons, and the button
    # that opened it cannot take focus even when a script asks.
    for _press in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.activeElement === document.body"
            " || !!document.activeElement.closest('#design-dialog')"
        ), "focus left the dialog"
    assert not opener.evaluate("(button) => { button.focus(); return button.matches(':focus'); }")

    page.keyboard.press("Escape")
    expect(drawn).to_be_hidden()
    expect(opener).to_be_focused()

    # Each answer closes it too, and focus comes back every time.
    for answer in ("Cancel", "Remove"):
        page.keyboard.press("Enter")
        expect(drawn).to_be_visible()
        dialog.get_by_role("button", name=answer, exact=True).click()
        expect(drawn).to_be_hidden()
        expect(opener).to_be_focused()


def test_without_a_script_the_same_button_opens_it_as_a_popover(
    browser: Browser, live_server, administrator
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        open_the_gallery(page, live_server.url)
        opener = page.get_by_role("button", name="Open the dialog", exact=True)
        dialog = page.locator("#design-dialog")
        drawn = page.locator("#design-dialog > *")
        expect(drawn).to_be_hidden()

        opener.click()
        expect(drawn).to_be_visible()
        assert dialog.evaluate("(dialog) => dialog.matches(':popover-open')")
        assert not dialog.evaluate("(dialog) => dialog.matches(':modal')")
        expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
        # Not modal, so the page behind is live: a click on it closes the dialog and lands
        # on whatever is there. It is not dimmed, because dimmed says out of reach.
        assert dialog.evaluate(SCRIM) == NOTHING, "the page behind a popover is dimmed"
        page.keyboard.press("Escape")
        expect(drawn).to_be_hidden()
        expect(opener).to_be_focused()

        for answer in ("Cancel", "Remove"):
            opener.click()
            expect(drawn).to_be_visible()
            dialog.get_by_role("button", name=answer, exact=True).click()
            expect(drawn).to_be_hidden()
    finally:
        context.close()
