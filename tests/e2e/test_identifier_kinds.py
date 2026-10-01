"""One identifier of each kind, kept in step while the rows change (#307).

The server switches off, in each row's choice of kind, the kinds the other rows hold; that
half is covered by `tests/test_one_of_each_kind.py` and is the whole behaviour with scripts
off. The script only keeps it true while somebody changes a row, which takes a browser:
`change` on a native select, a row taken off at once from its dialog (#303), and the
`disabled` property of an option.
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


def test_changing_a_rows_kind_moves_what_the_other_rows_offer(page: Page, live_server, applicant):
    from postulo.accounts.models import PersonIdentifier

    PersonIdentifier.objects.create(profile=applicant.profile, scheme="wikidata", value="Q95")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/accounts/profile/")

    saved = page.locator("select[name=identifiers-0-scheme]")
    new = page.locator("select[name=identifiers-1-scheme]")
    expect(new.locator("option[value=wikidata]")).to_be_disabled()
    expect(saved.locator("option[value=wikidata]")).to_be_enabled()

    # The saved row becomes a LinkedIn: Wikidata is free again, LinkedIn is not.
    saved.select_option("linkedin")
    expect(new.locator("option[value=wikidata]")).to_be_enabled()
    expect(new.locator("option[value=linkedin]")).to_be_disabled()

    # The new row takes Wikidata, so the saved row may not go back to it; the new row
    # keeps its own.
    new.select_option("wikidata")
    expect(saved.locator("option[value=wikidata]")).to_be_disabled()
    expect(new.locator("option[value=wikidata]")).to_be_enabled()

    # Removing the saved row -- at once, from its dialog, since #303 -- gives its kind back;
    # Other was never taken at all.
    page.get_by_role("button", name="Remove Wikidata Q95", exact=True).click()
    page.get_by_role("alertdialog").get_by_role("button", name="Remove", exact=True).click()
    expect(saved).to_have_count(0)
    expect(new.locator("option[value=linkedin]")).to_be_enabled()
    expect(new.locator("option[value=other]")).to_be_enabled()
