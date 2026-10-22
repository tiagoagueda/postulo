"""A company's status, drawn in a real browser (#941).

What the server draws is in `tests/test_company_status.py`. What needs a browser is how it
sits: the badge by the company's name and the hiring history on a phone and on a desk, in
light and dark, with nothing wider than the screen and nothing axe objects to -- and the
entries added, changed and taken off from the company's form.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e

WIDTHS = [(390, 844), (1280, 900)]


def a_company_with_a_status(applicant):
    from django.utils import timezone

    from postulo.jobs.models import Company, CompanyHiringSignal, JobPosting, LegalState

    company = Company.objects.create(
        owner=applicant,
        name="Aperture Science",
        legal_state=LegalState.ADMINISTRATION,
        legal_state_since=dt.date(2026, 2, 3),
        legal_state_source="https://register.example/aperture",
    )
    today = timezone.localdate()
    CompanyHiringSignal.objects.create(
        company=company,
        kind="laying_off",
        noted_on=today - dt.timedelta(days=200),
        author=applicant,
        source="https://news.example/layoffs",
    )
    CompanyHiringSignal.objects.create(
        company=company, kind="freeze", noted_on=today - dt.timedelta(days=4), author=applicant
    )
    JobPosting.objects.create(owner=applicant, company=company, title="Open role")
    return company


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width,height", WIDTHS)
def test_the_badge_and_the_history_fit_and_pass_axe(
    page: Page,
    live_server,
    applicant,
    scheme,
    width,
    height,
):
    company = a_company_with_a_status(applicant)
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")

    status = page.locator("[data-company-status]")
    expect(status).to_be_visible()
    expect(status.locator('[data-legal-state="administration"]')).to_contain_text(
        "In administration"
    )
    # The newest entry is the signal, fresh, so it has no "as of".
    freeze = status.locator('[data-hiring-signal="freeze"]')
    expect(freeze).to_contain_text("Hiring freeze")
    expect(freeze).not_to_contain_text("as of")
    expect(status.locator("[data-derived-hiring]")).to_have_count(0)

    history = page.locator("[data-hiring-history] [data-hiring-entry]")
    expect(history).to_have_count(2)
    expect(history.first).to_contain_text("Hiring freeze")
    expect(history.nth(1)).to_contain_text("Laying off")

    # The quiet warning on the company's listing is not the company's page; here nothing is
    # wider than the screen.
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"the page scrolls sideways by {overflow}px at {width}"
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    found = violations_on(page, AXE.read_text(encoding="utf-8"))
    assert not found, describe(f"the company page at {width} ({scheme})", found)


@pytest.mark.parametrize("width,height", WIDTHS)
def test_a_stale_signal_reads_as_of_its_date_and_the_count_is_derived(
    page: Page, live_server, applicant, width, height
):
    from django.utils import timezone

    from postulo.jobs.models import Company, CompanyHiringSignal, JobPosting

    company = Company.objects.create(owner=applicant, name="Old News")
    CompanyHiringSignal.objects.create(
        company=company, kind="hiring", noted_on=timezone.localdate() - dt.timedelta(days=200)
    )
    JobPosting.objects.create(owner=applicant, company=company, title="One")
    JobPosting.objects.create(owner=applicant, company=company, title="Two")
    page.set_viewport_size({"width": width, "height": height})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")

    signal = page.locator('[data-company-status] [data-hiring-signal="hiring"]')
    expect(signal).to_contain_text("as of")
    expect(page.locator("[data-company-status] [data-derived-hiring]")).to_contain_text(
        "2 open listings"
    )


def test_an_entry_is_added_changed_and_taken_off_from_the_form(page: Page, live_server, applicant):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    section = page.locator("[data-hiring-signals]")
    section.locator("select[name=kind]").last.select_option("hiring")
    section.get_by_label("Source").last.fill("https://news.example/hiring")
    section.get_by_role("button", name="Add an entry").click()
    entry = page.locator("[data-hiring-entry]")
    expect(entry).to_have_count(1)
    expect(entry).to_contain_text("Hiring")
    assert company.signals.get().author == applicant

    entry.get_by_text("Change", exact=True).click()
    entry.locator("select[name=kind]").select_option("freeze")
    entry.get_by_role("button", name="Save the change").click()
    page.wait_for_url("**/edit/**")
    expect(page.locator("[data-hiring-entry]")).to_contain_text("Hiring freeze")

    page.locator("[data-hiring-entry]").get_by_role("button", name="Remove this entry").click()
    page.wait_for_url("**/edit/**")
    expect(page.locator("[data-hiring-entry]")).to_have_count(0)
    assert not company.signals.exists()
