"""The flag beside a telephone field's country chooser, in a real browser.

An `<option>` holds text and nothing else, so the flag cannot live in the list; it sits
over the closed select and a script keeps it pointing at whatever is chosen (#88). The
markup either side of that is covered by unit tests. The script is not, and it is the only
part that needs a browser to be tested honestly: `change` on a native select, an image
element built on the fly where the server rendered none, and a request that has to actually
succeed under the content security policy.
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


def test_the_flag_appears_and_follows_the_country(page: Page, live_server, applicant):
    """A contact form on an account that has not chosen a language starts with no country
    at all, so there is no flag until one is picked -- which exercises the branch where the
    script has to build the image rather than change one."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")

    holder = page.locator("[data-phone-flag]")
    flag = holder.locator("img")
    select = page.locator("[data-phone-country]")

    expect(holder).to_be_attached()
    expect(flag).to_have_count(0)

    select.select_option("PT")
    expect(flag).to_have_attribute("data-flag", "pt")

    select.select_option("JP")
    expect(flag).to_have_attribute("data-flag", "jp")

    # It really loaded. A blocked request or a name the manifest never learned would leave
    # naturalWidth at 0, and neither is visible in the markup alone.
    assert flag.evaluate("image => image.complete && image.naturalWidth > 0")

    # The placeholder is a real choice, and the reserved space stays so nothing shifts.
    select.select_option("")
    expect(flag).to_have_count(0)
    expect(holder).to_be_attached()


def test_the_server_draws_the_flag_before_any_script_runs(page: Page, live_server, applicant):
    """With JavaScript blocked the field is still right about the country it loaded with,
    which is the true answer until the form is saved."""
    applicant.profile.language = "pt-pt"
    applicant.profile.save(update_fields=["language"])

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")

    expect(page.locator("[data-phone-flag] img")).to_have_attribute("data-flag", "pt")
