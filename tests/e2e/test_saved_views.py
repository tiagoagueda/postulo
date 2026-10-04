"""Keeping a view and choosing it again, in a real browser (#259).

The mechanics are in `tests/test_saved_views.py`. What only a browser can say is that the
*Views* menu opens, the form in it posts, and the link it then lists is the filtered table
back again -- all through a `<details>` and two forms, with no script doing any of it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


def test_a_narrowed_table_can_be_kept_and_chosen_again(page: Page, live_server, applicant):
    from postulo.jobs.models import Company

    Company.objects.create(owner=applicant, name="Aperture Science", location="Cambridge")
    Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/?location=mexico")
    rows = page.locator("#companies-table tbody tr")
    expect(rows).to_have_count(1)

    page.locator("[data-views] summary").click()
    page.get_by_label("Keep this view as").fill("In Mexico")
    page.get_by_role("button", name="Keep").click()

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/?location=mexico&saved=in-mexico")
    expect(rows).to_have_count(1)

    # Away, and back through the menu: the view is a link and it restores the question.
    page.goto(f"{live_server.url}/jobs/companies/")
    expect(page.locator("#companies-table tbody tr")).to_have_count(2)
    page.locator("[data-views] summary").click()
    page.get_by_role("link", name="In Mexico").click()

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/?location=mexico&saved=in-mexico")
    expect(page.locator("#companies-table tbody tr")).to_have_count(1)


def test_a_view_kept_after_filtering_live_is_the_filtered_table(page: Page, live_server, applicant):
    """The menu's `next` was written when the page loaded, so a filter typed into a header
    afterwards was not what was kept (#623)."""
    from postulo.jobs.models import Company

    Company.objects.create(owner=applicant, name="Aperture Science", location="Cambridge")
    Company.objects.create(owner=applicant, name="Black Mesa", location="New Mexico")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")

    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("mexico")
    expect(page.locator("#companies-table tbody tr")).to_have_count(1)

    page.locator("[data-views] summary").click()
    page.get_by_label("Keep this view as").fill("In Mexico")
    page.get_by_role("button", name="Keep").click()
    expect(page).to_have_url(re.compile(r"[?&]location=mexico(&|$).*[?&]saved=in-mexico"))

    page.goto(f"{live_server.url}/jobs/companies/")
    expect(page.locator("#companies-table tbody tr")).to_have_count(2)
    page.locator("[data-views] summary").click()
    page.get_by_role("link", name="In Mexico").click()
    expect(page.locator("#companies-table tbody tr")).to_have_count(1)
