"""A company's names in other languages, read from a popover beside its name (#917).

The button is a `popovertarget` one, so it opens with no script; with one, the browser closes
it on Escape and gives the focus back to the button.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Browser, Page, expect

from postulo.jobs.models import Company, CompanyName
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


@pytest.fixture
def company(applicant):
    company = Company.objects.create(owner=applicant, name="Aveiro Uni")
    CompanyName.objects.create(
        owner=applicant, company=company, language="pt", name="Universidade de Aveiro"
    )
    CompanyName.objects.create(
        owner=applicant, company=company, language="fr", name="Université d'Aveiro"
    )
    return company


def detail(company) -> str:
    return reverse("jobs:company_detail", args=[company.pk])


def test_the_keyboard_opens_it_and_escape_closes_it_and_returns_the_focus(
    page: Page, live_server, applicant, company
):
    sign_in(page, live_server.url, detail(company))
    button = page.get_by_role("button", name="Names in other languages")
    popover = page.locator("#company-names")
    expect(popover).to_be_hidden()

    button.focus()
    page.keyboard.press("Enter")

    expect(popover).to_be_visible()
    expect(popover.locator("bdi[lang='pt']")).to_have_text("Universidade de Aveiro")
    expect(popover.locator("bdi[lang='fr']")).to_have_text("Université d'Aveiro")
    expect(popover.get_by_role("button")).to_have_count(0)

    page.keyboard.press("Escape")

    expect(popover).to_be_hidden()
    expect(button).to_be_focused()


def test_it_opens_with_scripts_off(browser: Browser, live_server, applicant, company):
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        sign_in(page, live_server.url, detail(company))
        button = page.locator("[data-company-names-button]")
        assert button.get_attribute("popovertarget") == "company-names"
        button.click()
        expect(page.locator("#company-names")).to_be_visible()
    finally:
        context.close()


def test_no_icon_without_names(page: Page, live_server, applicant):
    bare = Company.objects.create(owner=applicant, name="Nobody Inc")
    sign_in(page, live_server.url, detail(bare))

    expect(page.get_by_role("button", name="Names in other languages")).to_have_count(0)
