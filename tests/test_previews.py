"""One previewer for every file (#942): what is answered inline, to whom, and the thumbnails.

The viewer itself is drawn by pdf.js in the browser and is tested in
`tests/e2e/test_document_viewer.py`;
these are the addresses it reads, and the small pictures a list shows.
"""

import io
import sys

import pytest
from django.core.files.storage import default_storage
from django.urls import reverse
from PIL import Image

from postulo.documents import filestore, previews
from postulo.documents.models import RenderedDocument, UploadedDocument
from tests.pdfs import make_pdf

pytestmark = pytest.mark.django_db

pytest.importorskip("pypdfium2")


def fetched(client, address, **extra):
    """``client.get``, with a file that is streamed read to its end and closed."""
    response = client.get(address, **extra)
    if getattr(response, "streaming", False):
        response.body = b"".join(response.streaming_content)
        response.close()
    return response


def a_pdf(pages: int = 2) -> bytes:
    return make_pdf(pages)


def a_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 600), "teal").save(out, format="PNG")
    return out.getvalue()


def upload(user, name: str, content: bytes, title: str = "Mine") -> UploadedDocument:
    document = UploadedDocument.objects.create(owner=user, title=title)
    document.file.save(name, io.BytesIO(content), save=True)
    return document


def test_a_pdf_is_answered_inline_with_its_own_type_and_the_usual_headers(client, user):
    """The viewer reads a PDF as bytes: inline, yet `nosniff`, `no-store` and sandboxed (#942)."""
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    response = fetched(client, reverse("documents:upload_preview", args=[document.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("scan.png", "image/png"),
        ("scan.jpg", "image/jpeg"),
        ("notes.txt", "text/plain; charset=utf-8"),
    ],
)
def test_pictures_and_text_are_the_other_things_answered_inline(client, user, name, expected):
    document = upload(user, name, b"hello")
    client.force_login(user)
    response = fetched(client, reverse("documents:upload_preview", args=[document.pk]))
    assert response["Content-Type"] == expected
    assert response["Content-Disposition"].startswith("inline")


@pytest.mark.parametrize("name", ["letter.docx", "letter.odt", "letter.rtf", "letter.doc"])
def test_a_file_with_no_preview_is_never_answered_inline(client, user, name):
    """Only the kinds the viewer draws are on the list; the rest stay downloads (#771, #942)."""
    document = upload(user, name, b"PK\x03\x04")
    client.force_login(user)
    assert (
        fetched(client, reverse("documents:upload_preview", args=[document.pk])).status_code == 404
    )
    download = fetched(client, reverse("documents:upload_download", args=[document.pk]))
    assert download["Content-Disposition"].startswith("attachment")


def test_a_title_cannot_change_what_is_previewed(client, user):
    """A title that reads as a data URL or as `.pdf` decides nothing; the extension does (#771)."""
    document = upload(
        user, "report.docx", b"PK\x03\x04", title="data:application/pdf;base64,AAAA.pdf"
    )
    client.force_login(user)
    assert previews.preview_of(document.download_name).kind == "none"
    assert (
        fetched(client, reverse("documents:upload_preview", args=[document.pk])).status_code == 404
    )


def test_another_accounts_preview_and_thumbnail_are_a_404(client, user, other_user):
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(other_user)
    for name in ("upload_preview", "upload_thumb", "upload_detail"):
        assert (
            fetched(client, reverse(f"documents:{name}", args=[document.pk])).status_code == 404
        ), name


def test_the_preview_refuses_a_file_kept_outside_its_owners_folder(client, user):
    """The `within` guard of a download holds at the preview too (#663, #773)."""
    document = upload(user, "cv.pdf", a_pdf())
    UploadedDocument.objects.filter(pk=document.pk).update(file="documents/999/2026/01/cv.pdf")
    client.force_login(user)
    for name in ("upload_preview", "upload_thumb"):
        assert fetched(client, reverse(f"documents:{name}", args=[document.pk])).status_code == 404


def test_a_sent_document_is_previewed_and_has_a_page(client, user, other_user):
    sent = RenderedDocument.objects.create(owner=user, title="CV", source_type=None, source_id=None)
    sent.file.save("cv.pdf", io.BytesIO(a_pdf()), save=True)
    client.force_login(user)
    response = fetched(client, reverse("documents:rendered_preview", args=[sent.pk]))
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    page = client.get(reverse("documents:rendered_detail", args=[sent.pk]))
    assert page.status_code == 200 and b"data-document-viewer" in page.content
    client.force_login(other_user)
    assert fetched(client, reverse("documents:rendered_preview", args=[sent.pk])).status_code == 404
    assert client.get(reverse("documents:rendered_detail", args=[sent.pk])).status_code == 404


def test_a_file_page_loads_the_viewer_from_its_own_origin_and_names_its_alternatives(client, user):
    """No CDN and no script in the markup: the library is a static file; a link stands in."""
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    html = client.get(reverse("documents:upload_detail", args=[document.pk])).content.decode()
    assert 'data-kind="pdf"' in html
    assert (
        "/static/js/vendor/pdfjs/pdf.min" in html and "http" not in html.split('data-lib="')[1][:20]
    )
    assert reverse("documents:upload_download", args=[document.pk]) in html
    assert "<script" not in html.split("data-document-viewer")[1].split("</section>")[0]


# ---------------------------------------------------------------------------- thumbnails


def test_a_pdf_thumbnail_is_the_first_page_as_webp_and_is_made_once(client, user):
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    address = reverse("documents:upload_thumb", args=[document.pk])
    response = client.get(address)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    image = Image.open(io.BytesIO(response.content))
    assert (
        image.format == "WEBP" and image.width <= previews.WIDTH and image.height <= previews.HEIGHT
    )
    name = filestore.thumbnail_name(document.file.name)
    assert name.startswith(f"thumbnails/{user.pk}/") and default_storage.exists(name)
    with default_storage.open(name) as stored:
        kept = stored.read()
    assert client.get(address).content == kept, "the second asking reads the kept one"


def test_a_picture_has_a_thumbnail_too(client, user):
    document = upload(user, "scan.png", a_png())
    client.force_login(user)
    response = client.get(reverse("documents:upload_thumb", args=[document.pk]))
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).width == previews.WIDTH


@pytest.mark.parametrize(
    "content", [b"not a pdf at all", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF", b""]
)
def test_a_broken_pdf_has_no_thumbnail_and_no_traceback(client, user, content):
    """A file PDFium cannot read is a 404, the kind's icon, and is tried once."""
    document = upload(user, "broken.pdf", content)
    client.force_login(user)
    address = reverse("documents:upload_thumb", args=[document.pk])
    assert client.get(address).status_code == 404
    assert default_storage.size(filestore.thumbnail_name(document.file.name)) == 0
    assert client.get(address).status_code == 404


def test_the_process_that_draws_a_pdf_is_stopped_when_it_takes_too_long(monkeypatch):
    """The limit holds: a hung renderer is a missing thumbnail, never a hung worker."""
    import subprocess

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(previews.subprocess, "run", hang)
    assert previews.make(a_pdf(), "cv.pdf") is None


def test_the_thumbnailer_is_a_separate_process_that_reads_stdin():
    """The module the list starts: a PDF in, WebP out; a bad one exits non-zero, writing nothing."""
    import subprocess

    command = [sys.executable, "-I", "-m", "postulo.documents.thumbnailer", "200", "300"]
    # our own interpreter and module, fixed arguments
    good = subprocess.run(  # noqa: S603
        command, input=a_pdf(1), capture_output=True, timeout=60, check=False
    )
    assert good.returncode == 0 and good.stdout.startswith(b"RIFF")
    bad = subprocess.run(  # noqa: S603
        command, input=b"nope", capture_output=True, timeout=60, check=False
    )
    assert bad.returncode != 0 and bad.stdout == b""


def test_a_kind_with_no_picture_has_none(client, user):
    document = upload(user, "notes.txt", b"hello")
    client.force_login(user)
    assert client.get(reverse("documents:upload_thumb", args=[document.pk])).status_code == 404
    assert not default_storage.exists(filestore.thumbnail_name(document.file.name))


def test_a_thumbnail_goes_with_its_file_and_is_never_a_stray(
    client, user, django_capture_on_commit_callbacks
):
    from postulo.core import media

    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    client.get(reverse("documents:upload_thumb", args=[document.pk]))
    name = filestore.thumbnail_name(document.file.name)
    assert default_storage.exists(name)
    assert not any(stored.name == name for stored in media.unreferenced())
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        document.delete()
    assert not default_storage.exists(name), "deleting the file deletes its thumbnail"


def test_deleting_an_account_removes_its_thumbnails(
    client, user, django_capture_on_commit_callbacks
):
    from postulo.accounts.deletion import delete_account

    type(user).objects.create_user(
        email="keeper@example.com", password="x", is_superuser=True, is_staff=True
    )
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    client.get(reverse("documents:upload_thumb", args=[document.pk]))
    name = filestore.thumbnail_name(document.file.name)
    assert default_storage.exists(name)
    with django_capture_on_commit_callbacks(execute=True):
        delete_account(user)
    assert not default_storage.exists(name)


def test_a_thumbnail_name_belongs_to_one_person_and_cannot_climb():
    assert filestore.thumbnail_name("documents/7/2026/01/a.pdf").startswith("thumbnails/7/")
    assert filestore.thumbnail_name("documents/7/../8/a.pdf") == ""
    assert filestore.thumbnail_name("avatars/7/a.png") == ""
    assert filestore.thumbnail_name("documents/x/a.pdf") == ""


def test_without_the_library_a_pdf_has_no_thumbnail_and_the_list_still_draws(
    client, user, monkeypatch
):
    monkeypatch.setattr(previews, "can_rasterise", lambda: False)
    document = upload(user, "cv.pdf", a_pdf())
    client.force_login(user)
    assert client.get(reverse("documents:upload_thumb", args=[document.pk])).status_code == 404
    page = client.get(reverse("documents:upload_list"))
    assert page.status_code == 200 and b"document-thumb" in page.content
    assert reverse("documents:upload_thumb", args=[document.pk]).encode() not in page.content


# ------------------------------------------------------------------------------ the draft


def test_a_draft_can_be_asked_for_inline_and_a_refusal_is_a_sentence(client, user, monkeypatch):
    """The viewer reads the CV's draft; where no renderer exists the answer is a 503 sentence
    and not a message queued for the next page and a redirect into HTML (#942)."""
    from postulo.documents import pdf as renderers
    from postulo.documents.models import CV

    cv = CV.objects.create(owner=user, name="Main")
    client.force_login(user)
    monkeypatch.setattr(renderers, "draft_pdf", lambda html: b"%PDF-1.4 drawn")
    inline = client.get(reverse("documents:cv_draft", args=[cv.pk]), {"inline": "1"})
    assert inline["Content-Type"] == "application/pdf"
    assert inline["Content-Disposition"].startswith("inline")
    attachment = client.get(reverse("documents:cv_draft", args=[cv.pk]))
    assert attachment["Content-Disposition"].startswith("attachment"), "a link still downloads"

    def unavailable(html):
        raise renderers.PDFBackendUnavailable("No PDF renderer is installed.")

    monkeypatch.setattr(renderers, "draft_pdf", unavailable)
    refused = client.get(reverse("documents:cv_draft", args=[cv.pk]), {"inline": "1"})
    assert refused.status_code == 503
    assert refused["Content-Type"].startswith("text/plain")
    assert b"No PDF renderer" in refused.content
