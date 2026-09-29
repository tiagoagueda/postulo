"""A draft, the words of a CV, and a Word file, in a real browser (#236).

`tests/test_document_drafts.py` and `tests/test_document_formats.py` hold what is handed
over and what is filed. What only a browser can say: that pressing the button hands the
browser a *file* and leaves the page where it was; that a letter's draft goes out of the
frame its chooser otherwise points at; that *Copy as plain text* is whole with the scripts
off; and that the script's button, where there is one, copies exactly what is in the box.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest
from playwright.sync_api import Browser, Page, expect

from postulo.documents.pdf import PDFBackendUnavailable, get_pdf_backend

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def a_renderer_is_installed() -> bool:
    try:
        get_pdf_backend()
    except PDFBackendUnavailable:
        return False
    return True


needs_a_renderer = pytest.mark.skipif(
    not a_renderer_is_installed(), reason="no PDF backend is installed here"
)


@pytest.fixture
def cv(applicant):
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem
    from postulo.resume.models import Experience

    experience = Experience.objects.create(
        owner=applicant,
        organisation="Aperture Science",
        role="Test engineer",
        start_date=dt.date(2019, 1, 1),
        highlights="Kept the turrets calm.",
    )
    document = CV.objects.create(owner=applicant, name="Main", headline="Tester")
    CVItem.objects.create(
        owner=applicant,
        cv=document,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
    )
    return document


@pytest.fixture
def letter(applicant):
    from postulo.applications.models import Application, Status
    from postulo.documents.models import CoverLetter
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test engineer")
    Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)
    return CoverLetter.objects.create(
        owner=applicant, name="To Aperture", body="Dear {{ company }}, about the {{ role }}."
    )


# ------------------------------------------------------------------------ a draft


@needs_a_renderer
def test_a_draft_is_a_file_and_the_page_stays_where_it_was(page: Page, live_server, cv):
    from postulo.documents.models import RenderedDocument

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")

    with page.expect_download() as taken:
        page.get_by_role("link", name="Download draft PDF").click()

    download = taken.value
    assert download.suggested_filename.endswith("(draft).pdf"), download.suggested_filename
    with open(download.path(), "rb") as handle:
        assert handle.read(5) == b"%PDF-"
    expect(page).to_have_url(f"{live_server.url}/documents/cvs/{cv.pk}/")
    assert not RenderedDocument.objects.exists(), "a look at a page break is not a record"


@needs_a_renderer
def test_a_letters_draft_leaves_the_frame_and_the_page_alone(page: Page, live_server, letter):
    """The chooser's form points at the frame; the draft's button takes its answer out of
    it, so the browser is handed a file rather than the frame a page it cannot draw."""
    from postulo.documents.models import RenderedDocument

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/letters/{letter.pk}/")
    frame = page.frame_locator("iframe[data-document-preview]")
    expect(frame.locator("body")).to_contain_text("Dear")

    page.locator("#preview-application").select_option(index=1)
    with page.expect_download() as taken:
        page.get_by_role("button", name="Download draft PDF").click()

    assert taken.value.suggested_filename.endswith("(draft).pdf")
    expect(page).to_have_url(f"{live_server.url}/documents/letters/{letter.pk}/")
    # The frame still holds the letter: the answer went to the browser, not into it.
    expect(frame.locator("body")).to_contain_text("Dear")
    assert not RenderedDocument.objects.exists()

    # And the button above it still does what it did.
    page.get_by_role("button", name="Read it below").click()
    expect(page.frame_locator("iframe[data-document-preview]").locator("body")).to_contain_text(
        "Aperture Science"
    )


# ----------------------------------------------------------------- the other formats


def test_the_word_file_downloads_and_is_one(page: Page, live_server, cv):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")

    with page.expect_download() as taken:
        page.get_by_role("link", name="Download .docx").click()

    download = taken.value
    assert download.suggested_filename.endswith(".docx")
    with open(download.path(), "rb") as handle:
        content = handle.read()
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        assert "Test engineer" in package.read("word/document.xml").decode("utf-8")
    expect(page).to_have_url(f"{live_server.url}/documents/cvs/{cv.pk}/")


def test_copy_as_plain_text_is_whole_with_the_scripts_off(browser: Browser, live_server, cv):
    """The page is the feature. With no script there is no button, and nothing is missing:
    the words are in a box that can be reached from the keyboard, holds all of them, and
    cannot be typed into."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/")
        page.get_by_role("link", name="Copy as plain text").click()
        expect(page).to_have_url(f"{live_server.url}/documents/cvs/{cv.pk}/text/")

        box = page.get_by_label("The text of this document")
        expect(box).to_be_visible()
        expect(box).not_to_be_editable()
        held = box.input_value()
        assert "Test engineer" in held and "- Kept the turrets calm." in held
        expect(page.get_by_role("button", name="Copy")).to_have_count(0)

        # A control, so it is a stop in the tab order: *Select all* from inside it selects
        # the CV and not the page around it, which is how it is copied by hand.
        box.focus()
        expect(box).to_be_focused()
        expect(page.get_by_role("link", name="Download .txt")).to_be_visible()
    finally:
        context.close()


def test_the_scripts_button_copies_what_is_in_the_box(browser: Browser, live_server, cv):
    """A convenience beside the box, never in place of it: the box is still there, still
    holds the words, and the button copies exactly them."""
    context = browser.new_context()
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server.url)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/documents/cvs/{cv.pk}/text/")

        box = page.get_by_label("The text of this document")
        expect(box).to_be_visible()
        button = page.get_by_role("button", name="Copy", exact=True)
        expect(button).to_be_visible()

        button.click()

        expect(page.get_by_role("button", name="Copied")).to_be_visible()
        # A clipboard is the operating system's, and Windows hands text back with its own
        # line endings; what was copied is the same lines either way.
        copied = page.evaluate("() => navigator.clipboard.readText()").replace("\r\n", "\n")
        assert copied == box.input_value().strip()
        assert "Test engineer" in copied
    finally:
        context.close()
