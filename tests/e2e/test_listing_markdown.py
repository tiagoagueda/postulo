"""A listing in Markdown under the real policy: both themes, scripts on and off (#665)."""

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e

SOURCE = (
    "# Duties\n\nWe want **careful** people and `portals`.\n\n"
    "- Calibrate portals\n- [Apply here](https://example.com/apply)\n\n"
    "> Quoted advice\n\n1. First\n2. Second\n\n"
    "<script>window.hacked = true</script>\n\n![x](https://tracker.example/p.png)"
)


@pytest.fixture(scope="session")
def axe() -> str:
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return AXE.read_text(encoding="utf-8")


@pytest.fixture
def markdown_listing(applicant):
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    return JobPosting.objects.create(
        owner=applicant,
        company=company,
        title="Portal Technician",
        description=SOURCE,
        description_format="markdown",
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_markdown_listing_has_no_violations_and_draws_its_markup(
    live_server, page: Page, axe, markdown_listing, scheme
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(live_server.url + markdown_listing.get_absolute_url())

    body = page.locator(".markdown-body")
    expect(body.get_by_role("heading", name="Duties")).to_be_visible()
    expect(body.get_by_role("listitem")).to_have_count(4)
    link = body.get_by_role("link", name="Apply here")
    expect(link).to_have_attribute("rel", "noopener noreferrer external")
    expect(link).to_have_attribute("target", "_blank")
    expect(body.locator("script, img")).to_have_count(0)
    assert page.evaluate("window.hacked") is None
    found = violations_on(page, axe)
    assert not found, describe(f"listing ({scheme})", found)


def test_a_markdown_listing_reads_the_same_with_scripts_off(
    browser: Browser, live_server, markdown_listing
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(live_server.url + markdown_listing.get_absolute_url())
        body = page.locator(".markdown-body")
        expect(body.get_by_role("heading", name="Duties")).to_be_visible()
        expect(body.get_by_role("link", name="Apply here")).to_be_visible()
        expect(body.get_by_role("listitem")).to_have_count(4)
    finally:
        context.close()
