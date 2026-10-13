"""The sector above the industries, in a real browser (#897).

What the server draws is in `tests/test_company_sectors.py`. What needs a browser is the
script between the two controls: the sector chooser narrowing what the industries box
offers, the chips offering the classification's names as well as the person's own, and an
industry chosen setting the chooser back to its sector, so the two never disagree.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.selects import button_of, choose
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def offered(page: Page, selector: str) -> list[str]:
    return page.locator(selector).evaluate(
        "(list) => [...list.options].map((option) => option.value)"
    )


def a_company(applicant):
    from postulo.jobs.models import Company, Industry

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    company.industries.set(Industry.named(applicant, ["Research"]))
    return company


def test_the_chips_offer_the_classification_narrowed_to_the_sector(
    page: Page, live_server, applicant
):
    company = a_company(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")
    sector = page.locator("select[data-industry-sector]")

    # Research is in N, so the form starts on it and offers N's names.
    expect(sector).to_have_value("N")
    names = offered(page, "[data-labels-options]")
    assert "Legal" in names and "Banking" not in names

    choose(page, sector, "Any sector")
    names = offered(page, "[data-labels-options]")
    assert "Banking" in names and "Software" in names, "every sector while none is chosen"

    choose(page, sector, "Financial and insurance activities")
    expect(sector).to_have_value("L")
    names = offered(page, "[data-labels-options]")
    assert "Banking" in names and "Insurance" in names
    assert "Software" not in names, "another sector's names are not offered"


def test_an_industry_chosen_sets_the_sector_to_its_own(page: Page, live_server, applicant):
    company = a_company(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")
    sector = page.locator("select[data-industry-sector]")

    field = page.locator("[data-labels-input]")
    field.fill("Software")
    field.press("Enter")

    expect(sector).to_have_value("K")
    expect(button_of(sector)).to_contain_text("Telecommunication")
    names = offered(page, "[data-labels-options]")
    assert "Telecommunications" in names and "Banking" not in names


def test_a_word_of_ones_own_typed_under_a_sector_is_filed_under_it(
    page: Page, live_server, applicant
):
    from postulo.jobs.models import Industry

    company = a_company(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    choose(page, page.locator("select[data-industry-sector]"), "Education")
    field = page.locator("[data-labels-input]")
    field.fill("Portal training")
    field.press("Enter")
    page.get_by_role("button", name="Save").click()
    page.wait_for_url(f"**/jobs/companies/{company.pk}/")

    assert Industry.objects.get(owner=applicant, name="Portal training").section == "Q"


def test_with_no_industries_yet_the_box_for_new_ones_is_narrowed(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/new/")

    choose(page, page.locator("select[data-industry-sector]"), "Education")
    names = offered(page, "#industry-suggestions")
    assert "Education" in names and "Banking" not in names
