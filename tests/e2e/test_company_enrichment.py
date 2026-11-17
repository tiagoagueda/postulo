"""*Find details* in a real browser (#1256).

What the server draws and keeps is in `tests/test_company_enrichment.py`. What needs a browser
is the dialog and the review: the button opens the dialog, the press lands on the review, the
review passes axe on a phone and on a desk in both themes, and what is ticked is kept.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on
from tests.test_company_enrichment import ITEM, QID, Details, installed, says, value
from tests.test_company_enrichment import web as web

pytestmark = pytest.mark.e2e

WIDTHS = [(390, 844), (1280, 900)]


@pytest.fixture
def details(db):
    from django.core.cache import caches

    caches["ratelimit"].clear()
    with installed():
        yield Details
    caches["ratelimit"].clear()


def a_company(applicant):
    from postulo.jobs.models import Company, CompanyIdentifier

    company = Company.objects.create(owner=applicant, name="Black Mesa")
    CompanyIdentifier.objects.create(owner=applicant, company=company, scheme="wikidata", value=QID)
    return company


def everything(web):
    says(
        web,
        value("careers_url", "https://blackmesa.test/jobs", "P9999", url=ITEM),
        value("email", "jobs@blackmesa.test", "P968"),
        value("acronym", "BMRF", "P1813"),
        value("legal_state", "dissolved", "P576", as_of="2001-05-16"),
        value("founded", "1958", "P571"),
        value("size", [1000, 4999], "P1128", as_of="1998"),
        value("name", "Black Mesa Research Facility", "label"),
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width,height", WIDTHS)
def test_the_dialog_and_the_review_fit_and_pass_axe(
    page: Page, live_server, applicant, details, web, scheme, width, height
):
    company = a_company(applicant)
    everything(web)
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")

    page.locator("[data-find-details]").click()
    dialog = page.locator("#details-dialog")
    # The `<dialog>` lends its box to what is inside it, as the logo dialog's does.
    expect(page.locator("#details-dialog > *")).to_be_visible()
    expect(dialog).to_contain_text("data.details.test")
    if AXE.is_file():
        found = violations_on(page, AXE.read_text(encoding="utf-8"))
        assert not found, describe(f"the Find details dialog at {width} ({scheme})", found)
    dialog.locator('button[type="submit"]').click()

    expect(page.locator("[data-enrichment-offers]")).to_be_visible()
    expect(page.locator("[data-enrichment-also]")).to_contain_text("1958")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"the review scrolls sideways by {overflow}px at {width}"
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    found = violations_on(page, AXE.read_text(encoding="utf-8"))
    assert not found, describe(f"the Find details review at {width} ({scheme})", found)


def test_what_is_ticked_is_kept_by_keyboard(page: Page, live_server, applicant, details, web):
    company = a_company(applicant)
    everything(web)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")
    page.locator("[data-find-details]").click()
    page.locator('#details-dialog button[type="submit"]').click()

    page.get_by_label("jobs@blackmesa.test").check()
    page.get_by_role("button", name="Keep what is ticked").press("Enter")

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/{company.pk}/")
    company.refresh_from_db()
    assert company.email == "jobs@blackmesa.test"
    assert company.careers_url == "" and company.legal_state == "active"
