"""An identifier on a company's form goes by its bin, after a question (#857).

The same contract as a row on *Your details* (`test_row_removal.py`): the bin opens a dialog,
confirming takes the row off there and then, the form's other edits are not saved or lost, the
block says what went, and focus goes on to the next row.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from postulo.jobs.models import Company, CompanyIdentifier
from tests.e2e.signing_in import sign_in

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

LEI = "5493001KJTIIGC8Y1R12"


@pytest.fixture
def company(applicant):
    acme = Company.objects.create(owner=applicant, name="Acme")
    CompanyIdentifier.objects.create(owner=applicant, company=acme, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(owner=applicant, company=acme, scheme="lei", value=LEI)
    return acme


def open_form(page: Page, live_server, company) -> None:
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")


def test_the_bin_asks_and_confirming_takes_the_row_off_at_once(page: Page, live_server, company):
    open_form(page, live_server, company)
    block = page.locator("[data-identifiers]")
    expect(block.locator("[data-remove-trigger]")).to_have_count(2)
    # An edit nobody has saved: the removal must neither save it nor lose it.
    page.get_by_label("Name", exact=True).first.fill("Acme Ltd")

    block.locator("[data-remove-trigger]").first.click()
    dialog = page.locator("dialog[data-remove-row]:visible, dialog[data-remove-row][open]").first
    expect(dialog.get_by_role("heading")).to_contain_text("Remove")
    dialog.get_by_role("button", name="Remove", exact=True).click()

    expect(block.locator("[data-remove-trigger]")).to_have_count(1)
    expect(block.locator("[data-removed-said]")).to_contain_text("removed.")
    assert CompanyIdentifier.objects.filter(company=company).count() == 1, "gone from the table now"
    assert Company.objects.get(pk=company.pk).name == "Acme", "and the rest was not saved"
    expect(page.get_by_label("Name", exact=True).first).to_have_value("Acme Ltd")
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_cancel_changes_nothing(page: Page, live_server, company):
    open_form(page, live_server, company)
    block = page.locator("[data-identifiers]")

    block.locator("[data-remove-trigger]").first.click()
    page.get_by_role("button", name="Cancel").first.click()

    expect(block.locator("[data-remove-trigger]")).to_have_count(2)
    assert CompanyIdentifier.objects.filter(company=company).count() == 2


def test_saving_after_a_removal_does_not_bring_the_row_back(page: Page, live_server, company):
    open_form(page, live_server, company)
    block = page.locator("[data-identifiers]")
    block.locator("[data-remove-trigger]").first.click()
    page.locator("dialog[data-remove-row][open]").get_by_role("button", name="Remove").click()
    expect(block.locator("[data-remove-trigger]")).to_have_count(1)

    page.get_by_role("button", name="Save", exact=True).click()

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/{company.pk}/")
    assert CompanyIdentifier.objects.filter(company=company).count() == 1


def test_with_scripts_off_the_bin_opens_a_popover_that_removes_and_returns_to_the_form(
    browser: Browser, live_server, company
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        open_form(page, live_server, company)
        page.locator("[data-identifiers] [data-remove-trigger]").first.click()
        dialog = page.locator("dialog[data-remove-row]:popover-open")
        expect(dialog).to_have_count(1)

        dialog.get_by_role("button", name="Remove", exact=True).click()

        expect(page).to_have_url(
            f"{live_server.url}/jobs/companies/{company.pk}/edit/#section-identifiers"
        )
        assert CompanyIdentifier.objects.filter(company=company).count() == 1
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_form_and_the_open_dialog_pass_axe(
    page: Page,
    live_server,
    company,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    open_form(page, live_server, company)
    page.locator("[data-identifiers] [data-remove-trigger]").first.click()
    expect(page.locator("dialog[data-remove-row][open]")).to_have_count(1)

    found = violations_on(page, axe_source)

    assert not found, describe(f"the company form with its remove dialog ({scheme})", found)
