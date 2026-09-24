"""Putting a reminder off, and narrowing the calendar, in a real browser (#238).

`tests/test_deadlines.py` reads the markup and the services. Two things it cannot say:

- that the menu on a reminder's row actually opens, and that pressing *Put off until next
  week* rewrites the date where it stands, without a page load;
- that the key along the top of the calendar really is the filter, since it being one
  control rather than two is the whole argument for drawing it that way.

Both are plain links and forms, so both work with no script at all — the menu is a
`<details>` for the same reason the column filters are.
"""

from __future__ import annotations

import datetime as dt

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


@pytest.fixture
def dated(applicant):
    """An application with a deadline, a reminder and a listing that closes."""
    from django.utils import timezone

    from postulo.applications.models import Application, Reminder, Status
    from postulo.jobs.models import Company, JobPosting

    today = timezone.localdate()
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test Engineer")
    application = Application.objects.create(
        owner=applicant, posting=posting, status=Status.DRAFT, deadline=today + dt.timedelta(2)
    )
    JobPosting.objects.create(
        owner=applicant,
        company=company,
        title="Portal Researcher",
        closes_at=today + dt.timedelta(days=3),
    )
    reminder = Reminder.objects.create(
        owner=applicant,
        application=application,
        summary="Chase them about the test chamber",
        due_at=timezone.now(),
    )
    return {"application": application, "reminder": reminder}


def test_putting_a_reminder_off_moves_it_where_it_stands(page: Page, live_server, dated):
    """The row stays and its date changes. A reminder put off until next week is still
    outstanding and still this application's; what was wrong with it was the day."""
    from django.utils import formats, timezone

    reminder = dated["reminder"]
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{dated['application'].get_absolute_url()}")
    block = page.locator("#reminders")
    expect(block).to_contain_text("Chase them")

    block.locator("summary").click()
    block.get_by_role("menuitem", name="Put off until next week").click()

    next_week = timezone.localtime(timezone.now() + dt.timedelta(days=7))
    expect(block).to_contain_text("Chase them")
    # The row writes the day the language writes it: a named format, never one written
    # out (the rule in CONTRIBUTING). A hard-coded "%d" zero-pads the day the app's "j"
    # does not, so this was green only on the days of the month that carry two digits.
    expect(block).to_contain_text(formats.date_format(next_week, "DATE_FORMAT"))
    reminder.refresh_from_db()
    assert reminder.due_at > timezone.now() + dt.timedelta(days=6)


def test_the_menu_leads_to_editing_and_to_deleting(page: Page, live_server, dated):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/reminders/")

    page.locator("#main summary").first.click()
    page.get_by_role("menuitem", name="Edit, or choose another time").click()

    expect(page).to_have_url(
        f"{live_server.url}/applications/reminders/{dated['reminder'].pk}/edit/"
    )
    expect(page.locator("input[name=summary]")).to_have_value("Chase them about the test chamber")


def test_the_calendar_key_is_the_filter(page: Page, live_server, dated):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/calendar/?view=agenda")

    expect(page.locator('[data-event="deadline"]')).to_have_count(1)
    expect(page.locator('[data-event="reminder"]')).to_have_count(1)
    expect(page.locator('[data-event="closing"]')).to_have_count(1)

    page.locator('[data-legend] [data-kind="reminder"]').click()

    expect(page.locator('[data-event="reminder"]')).to_have_count(0)
    expect(page.locator('[data-event="deadline"]')).to_have_count(1)
    expect(page.locator('[data-legend] [data-kind="reminder"]')).not_to_have_attribute(
        "data-showing", "true"
    )

    page.get_by_role("link", name="Show everything").click()

    expect(page.locator('[data-event="reminder"]')).to_have_count(1)


def test_a_whole_day_draws_no_hour(page: Page, live_server, dated):
    """A deadline is a date. A midnight in front of it would be a fact Postulo invented."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/calendar/?view=agenda&kinds=deadline")

    entry = page.locator('[data-event="deadline"]')
    expect(entry).to_contain_text("Test Engineer")
    expect(entry).not_to_contain_text("00:00")
