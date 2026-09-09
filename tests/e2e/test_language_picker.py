"""Choosing a language with a keyboard, in a control that is not a `<select>` (#119).

Not using a native dropdown costs the keyboard behaviour one gives away, and that cost is
the whole reason this file exists. A `<select>` brings arrow keys, Home and End, type-ahead
and Escape for nothing; a disclosure full of radios brings the arrow keys and the rest has
to be given back. Whether it was is a question about events, which only a browser answers.

The parts a browser cannot see — which element carries `lang`, what is hidden from a screen
reader, that a symbol is never alone — are in `tests/test_language_picker.py`.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


@pytest.fixture
def applicant(db):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    User = get_user_model()
    person = User.objects.create_user(
        email=EMAIL, password=PASSWORD, first_name="Alex", last_name="Morgan"
    )
    EmailAddress.objects.create(user=person, email=EMAIL, verified=True, primary=True)
    return person


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()


def open_picker(page: Page, base: str):
    page.goto(f"{base}/settings/language/")
    picker = page.locator("details[data-language-picker]")
    picker.locator("summary").click()
    return picker


def chosen(page: Page) -> str:
    return page.evaluate(
        "() => document.querySelector('[data-language-picker] input[name=language]:checked').value"
    )


def test_the_list_is_closed_until_it_is_asked_for(page: Page, live_server, applicant):
    """Thirty-nine rows where the time zone beside it is one line was the complaint."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/language/")

    picker = page.locator("details[data-language-picker]")

    expect(picker).not_to_have_attribute("open", "")
    expect(picker.get_by_text("Deutsch", exact=True)).to_be_hidden()


def test_opening_it_puts_the_keyboard_where_the_choosing_happens(
    page: Page, live_server, applicant
):
    """Rather than leaving somebody to tab past the whole list to reach the first row."""
    sign_in(page, live_server.url)
    open_picker(page, live_server.url)

    focused = page.evaluate("() => document.activeElement.getAttribute('name')")

    assert focused == "language"


def test_arrow_keys_move_between_languages(page: Page, live_server, applicant):
    """Which radios give for nothing, and which is why they are radios."""
    sign_in(page, live_server.url)
    open_picker(page, live_server.url)
    first = chosen(page)

    page.keyboard.press("ArrowDown")

    assert chosen(page) != first


def test_end_and_home_go_to_the_last_and_the_first(page: Page, live_server, applicant):
    """The part a `<select>` gives away and radios do not."""
    sign_in(page, live_server.url)
    open_picker(page, live_server.url)

    page.keyboard.press("End")
    last = chosen(page)
    page.keyboard.press("Home")
    first = chosen(page)

    assert last != first
    assert first == "", "the first row is the instance default"


def test_typing_jumps_to_a_language_by_its_own_name(page: Page, live_server, applicant):
    """As a dropdown does — and on the name as it is written in its own language, because
    that is the name on the row and the only one somebody looking for it can see.
    """
    sign_in(page, live_server.url)
    open_picker(page, live_server.url)

    page.keyboard.type("Deu")

    assert chosen(page) == "de"


def test_escape_closes_it_and_gives_the_focus_back(page: Page, live_server, applicant):
    """The same behaviour the account menu has, from the same handful of lines."""
    sign_in(page, live_server.url)
    picker = open_picker(page, live_server.url)

    page.keyboard.press("Escape")

    expect(picker).not_to_have_attribute("open", "")
    assert page.evaluate("() => document.activeElement.tagName") == "SUMMARY"


def test_a_language_chosen_with_the_keyboard_saves(page: Page, live_server, applicant):
    sign_in(page, live_server.url)
    open_picker(page, live_server.url)
    page.keyboard.type("Deu")

    page.get_by_role("button", name="Save").click()
    applicant.profile.refresh_from_db()

    assert applicant.profile.language == "de"


def test_the_closed_control_says_which_language_is_in_use(page: Page, live_server, applicant):
    """Having saved one, the page still answers "what am I using?" without a click."""
    applicant.profile.language = "de"
    applicant.profile.save()
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/language/")

    summary = page.locator("details[data-language-picker] summary")

    expect(summary).to_be_visible()
    expect(summary.locator("[lang=de]")).to_have_text("Deutsch")
