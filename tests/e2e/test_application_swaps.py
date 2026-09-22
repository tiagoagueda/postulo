"""The application page records what happened without moving (#257).

`tests/test_application_swaps.py` says the server sends the right fragments. What it cannot
say is whether the page stays where it was, whether the timeline actually grew, and where
focus went when a control removed itself — three questions only a browser answers, and the
three the issue is actually about.
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


@pytest.fixture
def application(applicant):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(
        owner=applicant, company=company, title="Test Engineer", description="x" * 4000
    )
    return Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)


@pytest.fixture
def reminders(applicant, application):
    from django.utils import timezone

    from postulo.applications.models import Reminder

    return [
        Reminder.objects.create(
            owner=applicant,
            application=application,
            summary=f"Chase them {number}",
            due_at=timezone.now(),
        )
        for number in range(2)
    ]


def test_recording_a_status_does_not_take_the_reader_back_to_the_top(
    page: Page, live_server, application
):
    """The failure the issue names: returned to the top of a page you were reading the
    middle of, because the answer was a fresh render of the whole thing.

    Measured from the moment the request goes out rather than from before the click, because
    Playwright scrolls an element into view before clicking it -- so a scroll set up by the
    test is undone by the test's own click, and comparing across it proves nothing. What is
    actually claimed is that *the swap* does not move the page, and that is what is read:
    the position when the request left, against the position once the swap has settled.
    """
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{application.get_absolute_url()}")
    page.evaluate(
        "document.addEventListener('htmx:beforeRequest',"
        " () => { window.__scrollAtRequest = window.scrollY; })"
    )
    page.locator("#status-card select").select_option("interviewing")

    page.locator("#status-card").get_by_role("button", name="Record").click()
    expect(page.locator("#timeline")).to_contain_text("Interviewing")

    sent = page.evaluate("window.__scrollAtRequest")
    assert sent is not None, "no htmx request went out, so the form did a page load"
    assert page.evaluate("window.scrollY") == sent, "the swap moved the page"


def test_a_page_load_would_have_moved_it(page: Page, live_server, application):
    """The check on the check above: without the script the position is not kept.

    Two tests that each pass alone are how a test comes to assert something that was never
    at risk. This one fails if a swap and a page load are indistinguishable here.
    """
    page.context.add_init_script("window.htmx = undefined;")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{application.get_absolute_url()}")
    page.locator("#status-card select").select_option("interviewing")
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(100)
    scrolled = page.evaluate("window.scrollY")

    page.locator("#status-card").get_by_role("button", name="Record").click()
    expect(page.locator("#timeline")).to_contain_text("Interviewing")

    assert scrolled > 0 and page.evaluate("window.scrollY") == 0, "a reload starts at the top"


def test_an_entry_appears_in_the_timeline_and_the_form_empties(
    page: Page, live_server, application
):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{application.get_absolute_url()}")
    page.locator("#event-form input[name=summary]").fill("Spoke to the recruiter")

    page.locator("#event-form").get_by_role("button", name="Add entry").click()

    expect(page.locator("#timeline")).to_contain_text("Spoke to the recruiter")
    expect(page.locator("#event-form input[name=summary]")).to_have_value("")


def test_ticking_a_reminder_hands_focus_to_the_next_one(page: Page, live_server, reminders):
    """#227's rule, and the reminder tick is exactly its shape: the control removes itself.

    Without this the next Tab starts again at the skip link, which for somebody working
    through a list by keyboard is the whole list over again.
    """
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{reminders[0].application.get_absolute_url()}")
    # The ticks, not every button in the block: since #238 each row also carries a menu
    # with *Put off until tomorrow* and *next week* in it, and those are not what swaps.
    ticks = page.locator("#reminders button[data-focus-after]")
    expect(ticks).to_have_count(2)

    ticks.first.click()

    expect(ticks).to_have_count(1)
    # `to_be_focused` retries, which is what this needs: focus is handed over when the swap
    # *settles*, a beat after the markup arrives, so reading `activeElement` straight after
    # the text appears reads it too early and calls it lost when it was not yet given.
    # It is also the only way to wait for it here -- the content security policy forbids
    # `unsafe-eval`, and `wait_for_function` evaluates its predicate as a string.
    expect(ticks.first).to_be_focused()


def test_ticking_the_last_reminder_lands_on_the_region_rather_than_nowhere(
    page: Page, live_server, application, applicant
):
    from django.utils import timezone

    from postulo.applications.models import Reminder

    Reminder.objects.create(
        owner=applicant, application=application, summary="The only one", due_at=timezone.now()
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{application.get_absolute_url()}")

    page.locator("#reminders button").first.click()

    expect(page.locator("#reminders")).to_contain_text("None outstanding")
    expect(page.locator("#reminders")).to_be_focused()


def test_with_no_script_the_page_still_works(page: Page, live_server, application, context):
    """The standing rule (#134, #136). An `hx-post` on a real form is progressive by
    construction, and this is the test that keeps it so."""
    context.add_init_script("window.htmx = undefined;")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{application.get_absolute_url()}")
    page.locator("#status-card select").select_option("offer")

    page.locator("#status-card").get_by_role("button", name="Record").click()

    expect(page.locator("#timeline")).to_contain_text("Offer")
    application.refresh_from_db()
    assert application.status == "offer"
