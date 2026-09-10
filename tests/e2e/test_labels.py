"""Choosing several labels, in a real browser (#139).

The markup either side of this is covered by `tests/test_labels.py`. The chips are not, and
they are the part that needs a browser to be tested honestly: a control built on the fly
over one that already worked, a keyboard vocabulary, and a live region that has to actually
contain a sentence rather than be an empty element with a role on it.

The one thing the unit tests cannot say is the one that matters most here — that the form
still posts what it posted before. A chip is a picture of a checkbox; if the checkbox stops
being ticked, the picture is a lie.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def a_company_with_industries(applicant):
    from postulo.jobs.models import Company, Industry

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    company.industries.set(Industry.named(applicant, ["Research", "Software"]))
    Industry.named(applicant, ["Gaming"])
    return company


def test_the_chosen_industries_arrive_as_labels(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    chips = page.locator("[data-labels-chips] .chip")

    expect(chips).to_have_count(2)
    expect(chips.first).to_contain_text("Research")
    # And the control it was layered over is out of the way but still in the form.
    expect(page.locator("[data-labels-existing]")).to_be_hidden()
    expect(page.locator('input[name="industries"]')).to_have_count(3)


def test_removing_a_label_unticks_the_box_it_was_a_picture_of(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    page.get_by_role("button", name="Remove Research").click()

    expect(page.locator("[data-labels-chips] .chip")).to_have_count(1)
    ticked = page.locator('input[name="industries"]:checked')
    expect(ticked).to_have_count(1)


def test_a_removal_is_announced(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    page.get_by_role("button", name="Remove Research").click()

    expect(page.locator("[data-labels-live]")).to_have_text("Research removed")


def test_typing_an_existing_name_ticks_it(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    field = page.locator("[data-labels-input]")
    field.fill("Gaming")
    field.press("Enter")

    expect(page.locator("[data-labels-chips] .chip")).to_have_count(3)
    expect(page.locator("[data-labels-live]")).to_have_text("Gaming added")
    # Ticked, not typed: it already existed, so nothing goes into the new-names field.
    expect(page.locator('input[name="new_industries"]')).to_have_value("")


def test_a_name_that_does_not_exist_yet_is_visibly_new(page: Page, live_server, applicant):
    """Otherwise people create Fintech, FinTech and fintech and find out afterwards that
    the slug collapsed them.
    """
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    field = page.locator("[data-labels-input]")
    field.fill("Fintech")
    field.press("Enter")

    fresh = page.locator("[data-labels-chips] .chip-new")
    expect(fresh).to_have_count(1)
    expect(fresh).to_contain_text("Fintech")
    expect(page.locator('input[name="new_industries"]')).to_have_value("Fintech")


def test_enter_adds_rather_than_submitting_the_form(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    address = f"{live_server.url}/jobs/companies/{company.pk}/edit/"
    page.goto(address)

    field = page.locator("[data-labels-input]")
    field.fill("Fintech")
    field.press("Enter")

    expect(page).to_have_url(address)


def test_escape_abandons_what_was_typed(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    field = page.locator("[data-labels-input]")
    field.fill("Fintech")
    field.press("Escape")

    expect(field).to_have_value("")
    expect(page.locator("[data-labels-chips] .chip-new")).to_have_count(0)


def test_backspace_does_not_delete_the_last_label(page: Page, live_server, applicant):
    """The convention, deliberately not followed. It is a way to delete something by
    pressing the key you press to correct a typo, and these are somebody's own words.
    """
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    field = page.locator("[data-labels-input]")
    field.click()
    field.press("Backspace")

    expect(page.locator("[data-labels-chips] .chip")).to_have_count(2)


def test_the_arrows_walk_between_labels(page: Page, live_server, applicant):
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    first = page.get_by_role("button", name="Remove Research")
    first.focus()
    first.press("ArrowRight")

    expect(page.get_by_role("button", name="Remove Software")).to_be_focused()


def test_what_the_labels_say_is_what_gets_saved(page: Page, live_server, applicant):
    """The whole point. A chip is a picture of a checkbox, and a picture that disagrees
    with what posts is worse than no picture.
    """
    company = a_company_with_industries(applicant)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{company.pk}/edit/")

    page.get_by_role("button", name="Remove Research").click()
    field = page.locator("[data-labels-input]")
    field.fill("Fintech")
    field.press("Enter")
    page.get_by_role("button", name="Save").click()

    expect(page).to_have_url(f"{live_server.url}/jobs/companies/{company.pk}/")
    company.refresh_from_db()
    assert sorted(industry.name for industry in company.industries.all()) == [
        "Fintech",
        "Software",
    ]


def test_tags_get_the_same_control(page: Page, live_server, applicant):
    """Written once, used twice — which is what the issue asked for."""
    from postulo.applications.models import Application, Status
    from postulo.core.models import Tag
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Black Mesa")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test Engineer")
    application = Application.objects.create(owner=applicant, posting=posting, status=Status.DRAFT)
    application.tags.set(Tag.named(applicant, ["Remote"]))

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/{application.pk}/edit/")

    expect(page.locator("[data-labels-chips] .chip")).to_have_count(1)
    expect(page.get_by_role("button", name="Remove Remote")).to_be_visible()
