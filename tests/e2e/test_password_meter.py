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
    status = page.get_by_role("status")

    field.fill("password123")
    expect(status).to_contain_text("Very weak")

    field.fill("alex.morgan")
    expect(status).to_contain_text("personal"), "it knows this is the person's own name"
    expect(status).not_to_contain_text("Strong")

    field.fill("correct horse battery staple crossing")
    expect(status).to_contain_text("Strong")
