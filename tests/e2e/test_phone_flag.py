"""The flag of a telephone field's country, and of an address's, in a real browser.

An `<option>` holds text and nothing else, so a native select has no flag in its list. The
server draws the flag of the country the field loaded with beside the closed select (#88),
which is what a page without scripts shows. Where scripts run, the select's own control
draws the flag itself -- in the button, following the choice, and beside every country in
the list (#301) -- and the server's is put away, so it is never drawn twice.

The markup either side of that is covered by unit tests. The script is not, and it is the
part that needs a browser to be tested honestly: a choice made on the native select, an
image element built on the fly where there was none, and a request that has to actually
succeed under the content security policy.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in

from .selects import button_of

pytestmark = pytest.mark.e2e


def test_an_addresss_flag_follows_its_country_too(page: Page, live_server, applicant):
    """The same control draws the postal address's chooser (#214)."""
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(
        owner=applicant, holder=applicant.profile, kind="home", street="Rua A", country=""
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")
    select = page.locator("select[name$='-country'][data-flag-select]").first
    flag = button_of(select).locator("img.flag")
    expect(flag).to_have_count(0)
    select.select_option("PT")
    expect(flag).to_have_attribute("src", page_flag("pt"))
    select.select_option("DE")
    expect(flag).to_have_attribute("src", page_flag("de"))
    # The one the server draws beside the native select is put away, never drawn twice.
    expect(select.locator("xpath=..").locator("[data-flag-holder]")).to_be_hidden()
    expect(select.locator("xpath=..").locator("img.flag:visible")).to_have_count(1)


def page_flag(country: str):
    """A flag's address, whatever content hash it is served under."""
    return re.compile(rf"/flags/{country}(\.[0-9a-f]+)?\.svg$")


def test_the_flag_appears_and_follows_the_country(page: Page, live_server, applicant):
    """A contact form on an account that has not chosen a language starts with no country
    at all, so there is no flag until one is picked -- which exercises the branch where the
    script has to build the image rather than change one."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")

    select = page.locator("[data-phone-country]")
    button = button_of(select)
    flag = button.locator("img.flag")
    room = button.locator("[data-select-mark]")

    expect(button).to_be_visible()
    expect(flag).to_have_count(0)
    empty = room.bounding_box()["width"]
    assert empty >= 20, "the room for a flag is kept while there is none, so nothing shifts"

    select.select_option("PT")
    expect(flag).to_have_attribute("src", page_flag("pt"))

    select.select_option("JP")
    expect(flag).to_have_attribute("src", page_flag("jp"))

    # It really loaded. A blocked request or a name the manifest never learned would leave
    # naturalWidth at 0, and neither is visible in the markup alone.
    #
    # Waited for rather than asserted outright: setting the attribute is synchronous and
    # fetching the image is not, so reading `complete` the instant the attribute changes is
    # a race. It was won on the machine this was written on and lost in CI (#117).
    page.wait_for_function(
        "(image) => image.complete && image.naturalWidth > 0", arg=flag.element_handle()
    )
    assert room.bounding_box()["width"] == empty

    # The placeholder is a real choice, and the reserved space stays so nothing shifts.
    select.select_option("")
    expect(flag).to_have_count(0)
    assert room.bounding_box()["width"] == empty
    # Decorative: the country's name is beside it.
    select.select_option("PT")
    expect(flag).to_have_attribute("alt", "")
    expect(flag).to_have_attribute("aria-hidden", "true")


def test_the_server_draws_the_flag_before_any_script_runs(browser: Browser, live_server, applicant):
    """With JavaScript blocked the field is still right about the country it loaded with,
    which is the true answer until the form is saved: the flag is the server's, beside the
    native select, and it is on the screen."""
    applicant.profile.language = "pt-PT"
    applicant.profile.save(update_fields=["language"])

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/jobs/contacts/new/")
        flag = page.locator("[data-phone-flag] img")
        expect(flag).to_have_attribute("data-flag", "pt")
        expect(flag).to_be_visible()
        expect(page.locator("[data-phone-country]")).to_be_visible()
        expect(page.locator("[data-select]")).to_have_count(0)
    finally:
        context.close()


def test_with_a_script_the_same_field_draws_that_flag_in_its_button(
    page: Page, live_server, applicant
):
    applicant.profile.language = "pt-PT"
    applicant.profile.save(update_fields=["language"])

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/contacts/new/")

    button = button_of(page.locator("[data-phone-country]"))
    expect(button.locator("img.flag")).to_have_attribute("src", page_flag("pt"))
    expect(page.locator("[data-phone-flag]")).to_be_hidden()
