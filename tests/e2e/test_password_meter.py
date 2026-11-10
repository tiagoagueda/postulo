"""The strength meter, in a browser: it reacts as a person types, and says so in words."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

from .conftest import PASSWORD

pytestmark = pytest.mark.e2e


def test_the_meter_speaks_as_you_type(live_server, page: Page, applicant) -> None:
    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/accounts/password/change/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button", name="Confirm").click()
        page.goto(f"{base}/accounts/password/change/")

    field = page.locator("input[name=password1]")
    expect(field).to_have_attribute("data-password-meter", "true")
    # Two regions, not one, since #225: the word is the live region, announced when it
    # changes, and zxcvbn's advice is a sibling outside it. Announcing the advice too meant
    # re-reading the whole sentence on every keystroke to somebody still typing.
    status = page.get_by_role("status")
    advice = page.locator("[data-suggestion]")

    # The region is in the page, empty, as soon as the field has focus (#1150): one that is
    # created and filled in the same breath is not announced by every screen reader.
    expect(status).to_have_count(0)
    field.focus()
    expect(status).to_have_count(1)
    expect(status).to_have_text("")

    field.fill("password123")
    expect(status).to_contain_text("Very weak")

    # It knows this is the person's own name.
    field.fill("alex.morgan")
    expect(advice).to_contain_text("personal")
    expect(status).not_to_contain_text("Strong")

    field.fill("correct horse battery staple crossing")
    expect(status).to_contain_text("Strong")


def test_the_dictionaries_come_on_focus_and_score_what_was_typed_meanwhile(
    live_server, page: Page, applicant
) -> None:
    """The page fetched 1.7 MB of zxcvbn whether or not a password was typed (#1150).

    Nothing of it is asked for until the field has focus. A person who types before the
    last file has arrived is told nothing, rather than something untrue, and then gets the
    word for what they typed without having to press another key.
    """
    base = live_server.url
    sign_in(page, base)

    asked: list[str] = []
    held: list = []
    page.on(
        "request", lambda request: asked.append(request.url) if "zxcvbn" in request.url else None
    )
    page.route("**/zxcvbn/language-en*", lambda route: held.append(route))

    page.goto(f"{base}/accounts/password/change/")
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button", name="Confirm").click()
        page.goto(f"{base}/accounts/password/change/")
    page.wait_for_load_state("networkidle")
    assert not asked, "nobody has touched the password field yet"

    field = page.locator("input[name=password1]")
    status = page.get_by_role("status")
    field.focus()
    expect(status).to_have_text("")
    expect(page.locator("script[src*='zxcvbn']")).to_have_count(3)
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1, "the last file is on its way, and held back here"

    field.press_sequentially("password123")
    page.wait_for_timeout(300)
    expect(status).to_have_text("")

    held[0].continue_()
    expect(status).to_have_text("Very weak")
    assert len(asked) == 3, asked

    # Leaving and coming back asks for nothing again.
    page.locator("input[name=password2]").focus()
    field.focus()
    expect(page.locator("script[src*='zxcvbn']")).to_have_count(3)
