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
