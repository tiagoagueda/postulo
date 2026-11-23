"""The earnings statistics card in a real browser (#1257): both themes, scripts off, a phone.

What the server draws is in `tests/test_earnings_plugin.py`. Here the bundled plugin is
switched on and the card is drawn from the file that ships with Postulo, on a listing's page
and on its application's, under axe in both themes, with scripts off, and at 320 pixels right
to left without a sideways scroll.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def axe() -> str:
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return AXE.read_text(encoding="utf-8")


@pytest.fixture
def lisbon_listing(applicant):
    from decimal import Decimal

    from postulo.applications.models import Application, Status
    from postulo.core.memo import forget_decisions
    from postulo.jobs.models import Company, JobPosting
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.filter(plugin="earnings", person__isnull=True).delete()
    forget_decisions()
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    listing = JobPosting.objects.create(
        owner=applicant,
        company=company,
        title="Software developer",
        location="Lisboa, PT",
        salary_min=Decimal("40000"),
        salary_currency="EUR",
    )
    JobPosting.objects.filter(pk=listing.pk).update(isco_code="2512")
    application = Application.objects.create(
        owner=applicant, posting=listing, status=Status.APPLIED
    )
    return listing, application


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_card_has_no_violations_on_either_page(
    live_server, page: Page, axe, lisbon_listing, scheme
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    for record in lisbon_listing:
        page.goto(live_server.url + record.get_absolute_url())
        card = page.locator("[data-earnings-reference]")
        expect(card).to_contain_text("Professionals (ISCO-08 major group 2)")
        expect(card.get_by_role("link", name="Eurostat")).to_be_visible()
        found = violations_on(page, axe)
        assert not found, describe(f"{record} with earnings statistics ({scheme})", found)


def test_the_card_reads_with_scripts_off(live_server, browser, lisbon_listing):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        listing, _application = lisbon_listing
        page.goto(live_server.url + listing.get_absolute_url())
        card = page.locator("[data-earnings-reference]")
        expect(card).to_contain_text("36,066 EUR per year, gross (2022)")
        expect(card).to_contain_text("not a recommendation")
    finally:
        context.close()


def test_the_card_fits_a_phone_and_reads_right_to_left(
    live_server, page: Page, axe, lisbon_listing, applicant
):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    page.set_viewport_size({"width": 320, "height": 800})
    sign_in(page, live_server.url)
    page.goto(live_server.url + lisbon_listing[0].get_absolute_url())
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    expect(page.locator("[data-earnings-reference] [data-earnings-figure]").first).to_be_visible()
    sideways = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert sideways <= 0, f"the page scrolls {sideways}px sideways at 320px"
    found = violations_on(page, axe)
    assert not found, describe("listing with earnings statistics (rtl, 320px)", found)
