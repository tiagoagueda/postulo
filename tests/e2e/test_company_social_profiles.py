"""A company's social profiles in a browser (#906).

What the server saves is in `tests/test_company_social_profiles.py`. Here: the block on the
company form behaves as a contact's does -- a pasted address chooses its service and draws
its mark -- and the company page shows the saved row with that mark.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def test_a_profile_pasted_on_the_company_form_is_saved_and_shown(
    page: Page, live_server, applicant
):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    block = page.locator('[data-web-links="social"]')
    expect(block).to_be_visible()
    expect(page.locator('[data-web-links="repository"]')).to_have_count(0)
    address = block.locator('input[name$="-url"]').first
    address.fill("https://mastodon.social/@aperture")
    address.blur()
    expect(block.locator("select").first).to_have_value("mastodon")

    page.get_by_role("button", name="Save").click()
    page.wait_for_url(f"**/jobs/companies/{company.pk}/")

    links = page.locator("[data-company-links]")
    expect(links.get_by_role("link", name="@aperture")).to_be_visible()
    expect(links.locator('svg[data-brand="mastodon"]')).to_have_count(1)
