"""Nothing may throw away an unsent letter without asking (#258).

There was no `beforeunload` handler anywhere in `app.js` and no autosave, so the sidebar,
the search box, the back button and every link on the page were one click away from
discarding whatever was in an eighteen-row textarea — silently, with no way back.

This needs a real browser. Whether a page asks before unloading is a decision the engine
makes, from state the script set, and a template test can only say that the script file
contains the words. The dialog itself is the browser's: Playwright dismisses it unless a
handler is attached, which is what these tests attach to see whether it appeared at all.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def asked_before_leaving(page: Page, go) -> bool:
    """Whether the browser put up its "leave site?" question when ``go`` navigated.

    The words are the browser's, so there is nothing to assert about them; what the page
    decides is only whether the question is asked. A `beforeunload` dialog is dismissed
    automatically unless something listens, so listening is how it is seen.
    """
    seen = []

    def remember(dialog):
        seen.append(dialog.type)
        dialog.accept()

    page.on("dialog", remember)
    try:
        # Asked or not, ``go`` ends on another page, and a question has to be answered
        # before the browser may leave: once the navigation lands, any dialog has been seen.
        with page.expect_navigation():
            go()
    finally:
        page.remove_listener("dialog", remember)
    return "beforeunload" in seen


def drawn(page: Page):
    """The box of the dialog: the `<dialog>` itself has no size of its own."""
    return page.locator("#leave-dialog > *")


def leave_dialog(page: Page):
    """Postulo's own question, drawn once on every page (#657)."""
    return page.get_by_role("alertdialog", name="Leave without saving?")


@pytest.fixture
def letter(applicant):
    """A cover letter to open and type into."""
    from postulo.documents.models import CoverLetter

    return CoverLetter.objects.create(owner=applicant, name="For Aperture", body="Dear all,")


def test_typing_into_a_letter_and_leaving_asks_first(page: Page, live_server, letter):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/edit/")
    page.locator("textarea[name=body]").fill("An hour of writing.")

    asked = asked_before_leaving(page, lambda: page.goto(f"{live_server.url}/applications/"))

    assert asked, "an unsent letter was thrown away without a question"


def test_a_page_nobody_has_typed_into_does_not_ask(page: Page, live_server, letter):
    """A guard that fires on every page is a guard everybody learns to click through."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/edit/")

    asked = asked_before_leaving(page, lambda: page.goto(f"{live_server.url}/applications/"))

    assert not asked


def test_submitting_the_form_is_not_leaving_it(page: Page, live_server, letter):
    """The classic way to make this infuriating: asking on the save itself."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/edit/")
    page.locator("textarea[name=body]").fill("Saved on purpose.")

    asked = asked_before_leaving(
        page, lambda: page.get_by_role("button", name="Save").first.click()
    )

    assert not asked
    letter.refresh_from_db()
    assert letter.body == "Saved on purpose."


def test_narrowing_a_list_is_a_control_rather_than_work(page: Page, applicant, live_server):
    """A filter form is not somebody's writing, and must never ask."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/")
    search = page.locator("input[name=q]").first
    if search.count() == 0:  # pragma: no cover - the page always has one today
        pytest.skip("no search box on the list page")
    search.fill("aperture")

    asked = asked_before_leaving(page, lambda: page.goto(f"{live_server.url}/"))

    assert not asked, "a GET form holds a filter, not work"


@pytest.fixture
def captures(applicant):
    """Two waiting, so the review page offers the keys that move between them."""
    from postulo.jobs.models import Capture

    return [
        Capture.objects.create(
            owner=applicant,
            url=f"https://example.org/job-{number}",
            data={"title": f"Engineer {number}", "company_name": "Black Mesa"},
        )
        for number in range(2)
    ]


def test_discarding_with_a_key_does_not_quietly_take_a_correction_with_it(
    page: Page, live_server, captures
):
    """`d` presses the discard button, which is a *different* form on the same page.

    So a submit may only forgive the form that was submitted: the review form still holds
    whatever was corrected, and this key is the fastest way to lose it (#258).
    """
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/captures/{captures[-1].pk}/review/")
    page.locator("input[name=company_name]").fill("Aperture Science")
    page.locator("body").click()

    page.keyboard.press("d")

    # Postulo's own question, not the browser's (#657): the form was not the dirty one, and
    # the page can see it is being submitted.
    expect(drawn(page)).to_be_visible()


def test_skipping_with_a_key_asks_too(page: Page, live_server, captures):
    """`j` is an ordinary link, and was never a special case — which is the point."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/captures/{captures[-1].pk}/review/")
    page.locator("input[name=company_name]").fill("Aperture Science")
    page.locator("body").click()

    page.keyboard.press("j")

    expect(drawn(page)).to_be_visible()


def test_cancelling_the_question_leaves_the_discard_button_working(
    page: Page, live_server, captures
):
    """The double-submit guard marks a form as sent, and the question can cancel that send.

    The dialog asks before the guard sees the submit, so there is no dead form to leave
    behind after *Cancel*: the same button asks again, and *Leave this page* discards (#518).
    """
    from postulo.jobs.models import CaptureStatus

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/captures/{captures[-1].pk}/review/")
    page.locator("input[name=company_name]").fill("Aperture Science")
    button = page.get_by_role("button", name="Discard this capture", exact=True)

    button.click()
    expect(drawn(page)).to_be_visible()
    leave_dialog(page).get_by_role("button", name="Cancel").click()
    expect(page).to_have_url(re.compile(r"/review/$"))
    captures[-1].refresh_from_db()
    assert captures[-1].status == CaptureStatus.PENDING
    expect(button).not_to_have_attribute("aria-disabled", "true")

    button.click()
    expect(drawn(page)).to_be_visible()
    with page.expect_navigation():
        leave_dialog(page).get_by_role("button", name="Leave this page").click()
    captures[-1].refresh_from_db()
    assert captures[-1].status == CaptureStatus.DISCARDED


def a_link(page: Page):
    """A link in the main navigation, one the page itself does not go to."""
    return page.locator("a[href='/applications/']:visible").first


def typed_letter(page: Page, live_server, letter) -> None:
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/edit/")
    page.locator("textarea[name=body]").fill("An hour of writing.")


def test_a_link_asks_in_postulos_own_dialog_and_not_the_browsers(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)
    seen = []
    page.on("dialog", lambda dialog: (seen.append(dialog.type), dialog.dismiss()))

    a_link(page).click()

    expect(drawn(page)).to_be_visible()
    assert seen == []


def test_cancel_keeps_the_text_and_gives_focus_back_to_the_link(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)
    link = a_link(page)

    link.click()
    expect(drawn(page)).to_be_visible()
    expect(leave_dialog(page).get_by_role("button", name="Cancel")).to_be_focused()
    leave_dialog(page).get_by_role("button", name="Cancel").click()

    expect(drawn(page)).to_be_hidden()
    expect(page.locator("textarea[name=body]")).to_have_value("An hour of writing.")
    expect(link).to_be_focused()


def test_escape_closes_the_question_and_changes_nothing(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)
    a_link(page).click()
    expect(drawn(page)).to_be_visible()

    page.keyboard.press("Escape")

    expect(drawn(page)).to_be_hidden()
    expect(page).to_have_url(re.compile(r"/edit/$"))


def test_leaving_goes_and_the_browser_asks_nothing_more(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)
    seen = []
    page.on("dialog", lambda dialog: (seen.append(dialog.type), dialog.accept()))

    a_link(page).click()
    with page.expect_navigation():
        leave_dialog(page).get_by_role("button", name="Leave this page").click()

    assert "/documents/letters/" not in page.url
    assert seen == []


def test_sign_out_asks_and_still_works_after_cancel(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)
    page.get_by_role("button", name=re.compile("Account menu")).first.click()
    page.get_by_role("button", name="Sign out").click()
    expect(drawn(page)).to_be_visible()
    leave_dialog(page).get_by_role("button", name="Cancel").click()
    expect(drawn(page)).to_be_hidden()

    page.get_by_role("button", name=re.compile("Account menu")).first.click()
    page.get_by_role("button", name="Sign out").click()
    expect(drawn(page)).to_be_visible()
    with page.expect_navigation():
        leave_dialog(page).get_by_role("button", name="Leave this page").click()
    expect(page.get_by_role("link", name="Sign in").first).to_be_visible()


def test_an_untouched_page_does_not_ask_in_the_dialog_either(page: Page, live_server, letter):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/edit/")

    with page.expect_navigation():
        a_link(page).click()

    assert "/documents/letters/" not in page.url


def test_closing_the_tab_still_raises_beforeunload(page: Page, live_server, letter):
    typed_letter(page, live_server, letter)

    asked = asked_before_leaving(page, lambda: page.goto(f"{live_server.url}/applications/"))

    assert asked
