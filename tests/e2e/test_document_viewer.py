"""The document viewer, in a real browser and under the real content security policy (#942).

What only a browser can say: that the vendored pdf.js loads from this origin, that its worker
starts (`worker-src 'self'`), that a page is drawn with its words as real text, and that nothing
the viewer does is a policy violation -- the autouse fixture in `conftest.py` fails a test on any
console report of one. The addresses it reads are held by `tests/test_previews.py`.
"""

from __future__ import annotations

import io

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on
from tests.pdfs import make_pdf

pytestmark = pytest.mark.e2e


def two_pages() -> bytes:
    return make_pdf(2)


@pytest.fixture
def pdf_file(applicant):
    from postulo.documents.models import UploadedDocument

    document = UploadedDocument.objects.create(owner=applicant, title="Two pages")
    document.file.save("two.pdf", io.BytesIO(two_pages()), save=True)
    return document


def open_viewer(page: Page, live_server, document):
    sign_in(page, live_server.url, f"/documents/files/{document.pk}/")
    viewer = page.locator("[data-document-viewer]")
    expect(viewer.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)
    return viewer


def test_a_pdf_is_drawn_with_its_words_and_its_pages_turn(page: Page, live_server, pdf_file):
    viewer = open_viewer(page, live_server, pdf_file)

    expect(viewer.locator("[data-viewer-fallback]")).to_be_hidden()
    expect(viewer.locator("[data-viewer-stage] canvas")).to_have_count(1)
    expect(viewer.locator(".textLayer")).to_contain_text("Words of page 1")
    expect(viewer.locator("[data-viewer-status]")).to_have_text("Page 1 of 2")

    viewer.get_by_role("button", name="Next page").click()
    expect(viewer.locator(".textLayer")).to_contain_text("Words of page 2")
    expect(viewer.locator("[data-viewer-page]")).to_have_value("2")
    expect(viewer.locator("[data-viewer-status]")).to_have_text("Page 2 of 2")
    expect(viewer.get_by_role("button", name="Next page")).to_be_disabled()


def test_zoom_changes_the_scale_of_the_page(page: Page, live_server, pdf_file):
    viewer = open_viewer(page, live_server, pdf_file)
    sheet = viewer.locator("[data-scale]")
    before = float(sheet.get_attribute("data-scale"))

    viewer.get_by_role("button", name="Zoom in").click()
    expect(viewer.locator("[data-scale]")).not_to_have_attribute("data-scale", str(before))
    assert float(viewer.locator("[data-scale]").get_attribute("data-scale")) > before

    viewer.locator("[data-viewer-zoom]").select_option("0.5")
    expect(viewer.locator("[data-scale]")).to_have_attribute("data-scale", "0.5")


def test_the_keyboard_works_while_the_viewer_has_focus_and_only_then(
    page: Page, live_server, pdf_file
):
    viewer = open_viewer(page, live_server, pdf_file)

    page.keyboard.press("PageDown")
    (
        expect(viewer.locator("[data-viewer-page]")).to_have_value("1"),
        "not the page's keys until it has focus",
    )

    viewer.locator("[data-viewer-stage]").focus()
    page.keyboard.press("PageDown")
    expect(viewer.locator(".textLayer")).to_contain_text("Words of page 2")
    page.keyboard.press("Home")
    expect(viewer.locator(".textLayer")).to_contain_text("Words of page 1")
    page.keyboard.press("End")
    expect(viewer.locator("[data-viewer-page]")).to_have_value("2")
    page.keyboard.press("+")
    expect(viewer.locator("[data-viewer-zoom]")).not_to_have_value("width")


def test_every_control_has_a_name_and_the_page_passes_axe(page: Page, live_server, pdf_file):
    viewer = open_viewer(page, live_server, pdf_file)
    for button in viewer.locator("button").all():
        assert button.inner_text().strip() or button.get_attribute("title"), "an unnamed control"
    expect(viewer).to_have_attribute("aria-label", "Two pages")
    found = violations_on(page, AXE.read_text(encoding="utf-8"))
    assert not found, describe(page.url, found)


def test_with_no_script_the_viewer_is_a_link(browser, live_server, applicant, pdf_file):
    """Everything the script adds is hidden until it runs; what is left is the way to the file."""
    from tests.e2e.conftest import EMAIL

    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        sign_in(page, live_server.url, f"/documents/files/{pdf_file.pk}/", email=EMAIL)
        viewer = page.locator("[data-document-viewer]")
        expect(viewer.locator("[data-viewer-bar]")).to_be_hidden()
        expect(
            viewer.locator("[data-viewer-fallback]").get_by_role("link", name="Download")
        ).to_be_visible()
    finally:
        context.close()


def test_a_file_that_is_not_a_pdf_says_so_and_offers_the_download(
    page: Page, live_server, applicant
):
    from postulo.documents.models import UploadedDocument

    broken = UploadedDocument.objects.create(owner=applicant, title="Broken")
    broken.file.save("broken.pdf", io.BytesIO(b"this is not a pdf"), save=True)
    sign_in(page, live_server.url, f"/documents/files/{broken.pk}/")
    message = page.locator("[data-viewer-message]")
    expect(message).to_contain_text("could not be shown here", timeout=30000)
    expect(
        page.locator("[data-viewer-fallback]").get_by_role("link", name="Download")
    ).to_be_visible()


def test_a_word_file_is_only_a_download(page: Page, live_server, applicant):
    from postulo.documents.models import UploadedDocument

    word = UploadedDocument.objects.create(owner=applicant, title="Letter")
    word.file.save("letter.docx", io.BytesIO(b"PK\x03\x04"), save=True)
    sign_in(page, live_server.url, f"/documents/files/{word.pk}/")
    expect(page.get_by_text("This kind of file cannot be shown here.")).to_be_visible()
    expect(page.get_by_role("link", name="Download").first).to_be_visible()
    assert page.locator("[data-document-viewer]").count() == 0


def test_a_picture_is_drawn_inline(page: Page, live_server, applicant):
    from PIL import Image

    from postulo.documents.models import UploadedDocument

    out = io.BytesIO()
    Image.new("RGB", (300, 200), "teal").save(out, format="PNG")
    picture = UploadedDocument.objects.create(owner=applicant, title="Scan")
    picture.file.save("scan.png", io.BytesIO(out.getvalue()), save=True)
    sign_in(page, live_server.url, f"/documents/files/{picture.pk}/")
    shown = page.locator(".document-viewer-picture")
    expect(shown).to_have_count(1)
    assert shown.evaluate("image => image.complete && image.naturalWidth === 300")


def test_the_list_shows_the_first_page_of_a_pdf(page: Page, live_server, pdf_file):
    sign_in(page, live_server.url, "/documents/files/")
    thumb = page.locator("[data-document-thumb] img").first
    expect(thumb).to_have_count(1)
    page.wait_for_function(
        "() => { const i = document.querySelector('[data-document-thumb] img');"
        " return i && i.complete && i.naturalWidth > 0; }"
    )


# ---------------------------------------------------------------- a CV and a letter


@pytest.fixture
def drawn_draft(monkeypatch):
    """The draft is any two-page PDF: what is under test is the page and the viewer, not
    WeasyPrint (held by the document tests)."""
    from postulo.documents import pdf

    monkeypatch.setattr(pdf, "draft_pdf", lambda html, **kwargs: two_pages())


@pytest.fixture
def cv(applicant):
    from postulo.documents.models import CV

    return CV.objects.create(owner=applicant, name="Main")


def test_a_cvs_pdf_is_the_tab_that_opens_and_the_quick_view_is_the_other(
    page: Page, live_server, cv, drawn_draft
):
    sign_in(page, live_server.url, f"/documents/cvs/{cv.pk}/")
    pdf_tab = page.get_by_role("tab", name="PDF")
    quick_tab = page.get_by_role("tab", name="Quick view")
    expect(pdf_tab).to_have_attribute("aria-selected", "true")
    viewer = page.locator("[data-document-viewer]")
    expect(viewer.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)
    expect(viewer.locator(".textLayer")).to_contain_text("Words of page 1")
    expect(page.locator("iframe[data-document-preview]")).to_be_hidden()
    expect(page.get_by_role("tabpanel", name="PDF")).to_be_visible()

    # Arrow keys move between the tabs, and only the chosen one is a stop of the Tab key.
    pdf_tab.focus()
    page.keyboard.press("ArrowRight")
    expect(quick_tab).to_have_attribute("aria-selected", "true")
    expect(quick_tab).to_be_focused()
    expect(pdf_tab).to_have_attribute("tabindex", "-1")
    expect(page.locator("iframe[data-document-preview]")).to_be_visible()
    expect(viewer).to_be_hidden()
    page.keyboard.press("Home")
    expect(pdf_tab).to_have_attribute("aria-selected", "true")
    expect(viewer).to_be_visible()


def test_a_cvs_page_with_the_pdf_tab_passes_axe(page: Page, live_server, cv, drawn_draft):
    sign_in(page, live_server.url, f"/documents/cvs/{cv.pk}/")
    expect(page.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)
    found = violations_on(page, AXE.read_text(encoding="utf-8"))
    assert not found, describe(page.url, found)


def test_a_letters_redraw_asks_for_the_application_chosen_beside_it(
    page: Page, live_server, cv, drawn_draft
):
    from postulo.applications.models import Application, Status
    from postulo.documents.models import CoverLetter
    from postulo.jobs.models import Company, JobPosting

    owner = cv.owner
    company = Company.objects.create(owner=owner, name="Aperture Science")
    posting = JobPosting.objects.create(owner=owner, company=company, title="Test engineer")
    application = Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)
    letter = CoverLetter.objects.create(owner=owner, name="To Aperture", body="Dear {{ company }}")
    sign_in(page, live_server.url, f"/documents/letters/{letter.pk}/")
    viewer = page.locator("[data-document-viewer]")
    expect(viewer.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)

    page.locator("#preview-application").select_option(str(application.pk))
    with page.expect_request(lambda r: f"application={application.pk}" in r.url):
        viewer.get_by_role("button", name="Redraw").click()
    expect(viewer.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)


def test_a_cvs_page_does_not_scroll_sideways_at_320_pixels(
    page: Page, live_server, cv, drawn_draft
):
    from tests.e2e.test_reflow import NARROW, SCROLLS_SIDEWAYS

    page.set_viewport_size({"width": NARROW, "height": 800})
    sign_in(page, live_server.url, f"/documents/cvs/{cv.pk}/")
    expect(page.locator("[data-viewer-count]")).to_have_text("of 2", timeout=30000)
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
