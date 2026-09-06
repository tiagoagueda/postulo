"""Dragging a card between board columns, in a real browser.

Drag and drop is one of the few things that cannot be tested honestly without one: the
whole feature is browser events. What the test proves is the thing that matters — that a
drop goes through the *existing* status form, so the server path, the event log and the
timeline entry are exactly what a person using the menu would have produced.
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
def application(applicant):
    from django.utils import timezone

    from postulo.applications.models import Application, Status
    from postulo.applications.services import change_status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Black Mesa")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Research Engineer")
    application = Application.objects.create(owner=applicant, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=3))
    application.refresh_from_db()
    return application


def drag(page: Page, card, column) -> None:
    """A real HTML5 drag, which Playwright's high-level helpers do not perform."""
    page.evaluate(
        """([card, column]) => {
            const transfer = new DataTransfer();
            const fire = (target, type) => target.dispatchEvent(
                new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: transfer})
            );
            fire(card, 'dragstart');
            fire(column, 'dragover');
            fire(column, 'drop');
            fire(card, 'dragend');
        }""",
        [card, column],
    )


def test_a_card_dropped_on_a_column_moves_and_is_recorded(live_server, page: Page, application):
    from postulo.applications.models import ApplicationEvent, EventKind, Status

    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/board/")

    card = page.locator(f"[data-card='{application.pk}']")
    expect(card).to_have_attribute("data-status", "applied")
    interviewing = page.locator("[data-board-column='interviewing']")
    assert interviewing.locator("[data-card]").count() == 0

    drag(page, card.element_handle(), interviewing.element_handle())
    page.wait_for_load_state("networkidle")

    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING

    # Through the ordinary service, so the timeline reads as it always does.
    event = ApplicationEvent.objects.filter(
        application=application, kind=EventKind.STATUS_CHANGE
    ).latest("pk")
    assert event.from_status == Status.APPLIED and event.to_status == Status.INTERVIEWING

    # And the board that comes back shows it where it now belongs.
    assert page.locator("[data-board-column='interviewing'] [data-card]").count() == 1
    assert page.locator("[data-board-column='applied'] [data-card]").count() == 0


def test_the_status_menu_still_does_the_same_thing(live_server, page: Page, application):
    """Drag and drop fires on neither a touch screen nor a keyboard; the menu must stay."""
    from postulo.applications.models import Status

    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/board/")

    card = page.locator(f"[data-card='{application.pk}']")
    assert card.locator("select[name='status']").count() == 1
    card.locator("select[name='status']").select_option("offer")
    page.wait_for_load_state("networkidle")

    application.refresh_from_db()
    assert application.status == Status.OFFER
    assert page.locator("#board-drag-help").count() == 1, "and the cards say so to a reader"
