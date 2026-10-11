"""An appointment with an adviser, typed by hand and found again, in a real browser (#888).

`tests/test_appointments.py` holds the model, the pages' markup, the file, the report and the
archive. What needs a browser is the loop a person goes round: *New appointment* on the
calendar opens a form with the day in it, saving brings back the calendar with the meeting
drawn in its own colour on its own day, its key can switch it off, and the service's own page
lists it and offers another. The pages are in the accessibility walk, and the calendar is
reflowed at 320 pixels with the new kind showing.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

from .test_reflow import SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e


@pytest.fixture
def office(applicant):
    from postulo.jobs.models import Company, CompanyKind, Contact

    company = Company.objects.create(
        owner=applicant, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    Contact.objects.create(owner=applicant, company=company, name="Mme Durand")
    return company


def test_an_appointment_is_made_from_the_calendar_and_drawn_on_its_day(
    live_server, page: Page, applicant, office
):
    from postulo.applications.models import Appointment

    day = dt.date.today() + dt.timedelta(days=1)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/calendar/?view=agenda")

    page.get_by_role("link", name="New appointment").first.click()
    expect(page).to_have_url(re.compile(r"/applications/appointments/new/\?next="))
    page.get_by_label("Starts").fill(f"{day:%Y-%m-%d}T10:30")
    page.locator("select#id_contact").select_option(label="Mme Durand · France Travail")
    page.get_by_label("Where").fill("12 rue de la Paix")
    page.get_by_role("button", name="Save", exact=True).click()

    expect(page).to_have_url(re.compile(r"/applications/calendar/"))
    event = page.locator('a[data-event="appointment"]')
    expect(event).to_have_count(1)
    expect(event).to_contain_text("France Travail")
    expect(event.locator(".sr-only")).to_have_text("Appointment:")
    assert Appointment.objects.get().place == "12 rue de la Paix"

    # The key is the filter: taking the kind out takes the meeting off the page.
    page.get_by_role("link", name="Hide Appointments").click()
    expect(page.locator('a[data-event="appointment"]')).to_have_count(0)


def test_the_services_page_lists_it_and_adds_another_that_returns_there(
    live_server, page: Page, applicant, office
):
    from postulo.applications.models import Appointment

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/{office.pk}/")
    expect(page.locator("#appointments")).to_contain_text("No appointments yet.")

    page.locator("[data-new-appointment]").click()
    page.get_by_label("Starts").fill("2031-05-04T09:00")
    page.get_by_role("button", name="Save", exact=True).click()

    expect(page).to_have_url(re.compile(rf"/jobs/companies/{office.pk}/"))
    expect(page.locator("#appointments li")).to_have_count(1)
    assert Appointment.objects.filter(owner=applicant, company=office).count() == 1


@pytest.mark.parametrize("width", [320, 1280])
def test_the_calendar_with_an_appointment_on_it_reflows(
    live_server, page: Page, applicant, office, width
):
    from django.utils import timezone

    from postulo.applications.models import Appointment

    Appointment.objects.create(
        owner=applicant,
        company=office,
        starts_at=timezone.now() + dt.timedelta(hours=3),
        place="12 rue de la Paix",
    )
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/applications/calendar/?view=agenda")

    expect(page.locator('a[data-event="appointment"]')).to_have_count(1)
    assert page.evaluate(SPILLS) == []
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], (
        f"the calendar scrolls sideways at {width}"
    )
