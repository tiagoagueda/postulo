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

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


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
        go()
        page.wait_for_timeout(300)
    finally:
        page.remove_listener("dialog", remember)
    return "beforeunload" in seen


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

    asked = asked_before_leaving(page, lambda: page.keyboard.press("d"))

    assert asked, "a correction was discarded along with the capture, without a question"


def test_skipping_with_a_key_asks_too(page: Page, live_server, captures):
    """`j` is an ordinary link, and was never a special case — which is the point."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/captures/{captures[-1].pk}/review/")
    page.locator("input[name=company_name]").fill("Aperture Science")
    page.locator("body").click()

    asked = asked_before_leaving(page, lambda: page.keyboard.press("j"))

    assert asked
