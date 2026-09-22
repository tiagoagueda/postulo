"""A CV's preview beside its entries, in a real browser (#293).

The markup and the two policy headers are held by `tests/security/test_requests.py`. What
only a browser can say: that the frame actually draws the document under the content
security policy (a refused frame is a blank box and a console message, and the autouse
fixture in `conftest.py` fails on the message); that moving an entry changes what the frame
shows, with no script doing the work; and that a frame holding a second document does not
swallow the keyboard on its way through the page.
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
def cv(applicant):
    """A CV holding two experiences, so that moving one is visible."""
    import datetime as dt

    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem
    from postulo.resume.models import Experience

    first = Experience.objects.create(
        owner=applicant,
        organisation="Aperture Science",
        role="Test engineer",
        start_date=dt.date(2019, 1, 1),
    )
    second = Experience.objects.create(
        owner=applicant,
        organisation="Black Mesa",
        role="Research associate",
        start_date=dt.date(2021, 1, 1),
    )
    document = CV.objects.create(owner=applicant, name="Main")
    kind = ContentType.objects.get_for_model(Experience)
    CVItem.objects.create(owner=applicant, cv=document, content_type=kind, object_id=first.pk)
    CVItem.objects.create(owner=applicant, cv=document, content_type=kind, object_id=second.pk)
    return document


def test_the_preview_is_on_the_page_and_follows_an_edit(page: Page, live_server, cv):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")

    frame = page.frame_locator("iframe[data-document-preview]")
    body = frame.locator("body")
    expect(body).to_contain_text("Aperture Science")
    expect(body).to_contain_text("Black Mesa")
    before = body.inner_text()
    assert before.index("Aperture Science") < before.index("Black Mesa")

    page.get_by_role("button", name="Move down").first.click()

    after = page.frame_locator("iframe[data-document-preview]").locator("body")
    expect(after).to_contain_text("Aperture Science")
    text = after.inner_text()
    assert text.index("Black Mesa") < text.index("Aperture Science"), "the frame redrew"


def test_the_frame_is_named_and_is_not_a_keyboard_trap(page: Page, live_server, cv):
    """A frame is a second document and a reading context of its own (#275); it may be a
    stop in the tab order, but a stop somebody can leave again."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")

    frame = page.locator("iframe[data-document-preview]")
    expect(frame).to_have_attribute("title", "Preview of Main")

    frame.focus()
    assert page.evaluate("() => document.activeElement.tagName") == "IFRAME"
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.tagName") != "IFRAME":
            break
    assert page.evaluate("() => document.activeElement.tagName") != "IFRAME", "trapped"


def test_a_letters_chooser_redraws_the_frame_for_that_application(page: Page, live_server, cv):
    """The application picker targets the frame rather than a new tab, so choosing one is
    reading the letter the way that employer will -- plain HTML, no script."""
    from postulo.applications.models import Application, Status
    from postulo.documents.models import CoverLetter
    from postulo.jobs.models import Company, JobPosting

    applicant = cv.owner
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test engineer")
    Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)
    letter = CoverLetter.objects.create(
        owner=applicant, name="To Aperture", body="Dear {{ company }}, about the {{ role }}."
    )
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/")

    frame = page.frame_locator("iframe[data-document-preview]")
    expect(frame.locator("body")).to_contain_text("Dear")

    page.locator("#preview-application").select_option(index=1)
    page.get_by_role("button", name="Read it below").click()

    expect(page.frame_locator("iframe[data-document-preview]").locator("body")).to_contain_text(
        "Aperture Science"
    )
    # The page itself did not navigate: only the frame did.
    expect(page).to_have_url(f"{live_server.url}/documents/letters/{letter.pk}/")
