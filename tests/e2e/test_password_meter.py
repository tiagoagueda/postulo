"""The strength meter, in a browser: it reacts as a person types, and says so in words."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def test_the_meter_speaks_as_you_type(live_server, page: Page, applicant) -> None:
    base = live_server.url
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")

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

    field.fill("password123")
    expect(status).to_contain_text("Very weak")

    # It knows this is the person's own name.
    field.fill("alex.morgan")
    expect(advice).to_contain_text("personal")
    expect(status).not_to_contain_text("Strong")

    field.fill("correct horse battery staple crossing")
    expect(status).to_contain_text("Strong")
