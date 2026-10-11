"""*Delete* asks in a dialog on the object's own page (#854).

The button is a `popovertarget` one, so with a script the dialog is modal and without one it
is a popover whose form posts to the same view the page at that address answers. These walk
the company's page: what the dialog says, that *Cancel* changes nothing, and that confirming
deletes and lands on the list.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from postulo.applications.models import Application, Status
from postulo.jobs.models import Company, JobPosting
from tests.e2e import test_accessibility as accessibility
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


@pytest.fixture
def company(applicant):
    acme = Company.objects.create(owner=applicant, name="Black Mesa")
    posting = JobPosting.objects.create(owner=applicant, company=acme, title="Research Engineer")
    Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)
    return acme


@pytest.fixture
def axe() -> str:
    if not accessibility.AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return accessibility.AXE.read_text(encoding="utf-8")


def open_company(page: Page, live_server, company) -> None:
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/")


def test_delete_asks_in_a_dialog_that_counts_what_goes_and_cancel_changes_nothing(
    page: Page, live_server, company
):
    open_company(page, live_server, company)
    dialog = page.locator("#delete-company")
    drawn = page.locator("#delete-company > form")
    expect(drawn).to_be_hidden()

    page.get_by_role("button", name="Delete", exact=True).first.click()

    expect(drawn).to_be_visible()
    assert dialog.evaluate("(dialog) => dialog.matches(':modal')"), "modal where a script runs"
    expect(dialog.get_by_role("heading")).to_contain_text("Black Mesa")
    expect(dialog.locator("[data-consequences]")).to_contain_text("1 posting")
    expect(dialog.locator("[data-consequences]")).to_contain_text("1 application")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()

    page.keyboard.press("Escape")
    expect(drawn).to_be_hidden()
    assert Company.objects.filter(pk=company.pk).exists()


def test_confirming_in_the_dialog_deletes_the_company_and_lands_on_the_list(
    page: Page, live_server, company
):
    open_company(page, live_server, company)

    page.get_by_role("button", name="Delete", exact=True).first.click()
    page.locator("#delete-company").get_by_role("button", name="Delete", exact=True).click()

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/")
    assert not Company.objects.filter(pk=company.pk).exists()


def test_with_scripts_off_the_dialog_is_a_popover_that_posts(
    browser: Browser, live_server, company
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        open_company(page, live_server, company)
        page.get_by_role("button", name="Delete", exact=True).first.click()
        dialog = page.locator("#delete-company")
        expect(page.locator("#delete-company > form")).to_be_visible()
        assert dialog.evaluate("(dialog) => dialog.matches(':popover-open')")

        dialog.get_by_role("button", name="Delete", exact=True).click()

        expect(page).to_have_url(f"{live_server.url}/jobs/companies/")
        assert not Company.objects.filter(pk=company.pk).exists()
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_open_dialog_passes_axe(page: Page, live_server, company, axe, scheme):
    page.emulate_media(color_scheme=scheme)
    open_company(page, live_server, company)
    page.get_by_role("button", name="Delete", exact=True).first.click()
    expect(page.locator("#delete-company > form")).to_be_visible()

    found = accessibility.violations_on(page, axe)

    assert not found, accessibility.describe(f"the open delete dialog ({scheme})", found)
