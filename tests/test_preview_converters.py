"""A plugin may offer to turn a file Postulo cannot show into a PDF it can (#942, D3).

The core ships no converter; these hold the surface a plugin writes to and what Postulo does
with what it is handed: the converted copy is a derivative beside the thumbnails, the file
stays what is downloaded, ownership and the folder guard are the file's own, and nothing that
is not a PDF is ever kept or answered.
"""

import io

import pytest
from django.core.files.storage import default_storage
from django.urls import reverse

from postulo.documents import filestore, previews
from postulo.documents.models import UploadedDocument
from postulo.plugins import api
from tests.pdfs import make_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_converters():
    previews.forget_converter()
    yield
    previews.forget_converter()


def upload(user, name: str, content: bytes = b"PK\x03\x04") -> UploadedDocument:
    document = UploadedDocument.objects.create(owner=user, title="Letter")
    document.file.save(name, io.BytesIO(content), save=True)
    return document


def fetched(client, address):
    response = client.get(address)
    if getattr(response, "streaming", False):
        response.body = b"".join(response.streaming_content)
        response.close()
    return response


def offer(convert, key="words", extensions=(".docx", ".odt")):
    return previews.register_converter(
        previews.Converter(key=key, extensions=extensions, convert=convert)
    )


def test_the_core_has_no_converter_so_a_word_file_is_only_a_download(client, user):
    document = upload(user, "letter.docx")
    assert previews.preview_of("letter.docx").kind == "none"
    client.force_login(user)
    assert (
        fetched(client, reverse("documents:upload_preview", args=[document.pk])).status_code == 404
    )


def test_the_surface_names_the_offer_and_the_call():
    assert api.PreviewConverter is previews.Converter
    assert api.register_preview_converter is previews.register_converter
    assert "PreviewConverter" in api.__all__
    assert "register_preview_converter" in api.__all__


def test_a_converted_file_is_previewed_as_a_pdf_and_is_not_thumbnailed(client, user):
    seen = []

    def convert(data, extension):
        seen.append((data, extension))
        return make_pdf(1)

    assert offer(convert)
    document = upload(user, "Letter.DOCX")
    found = previews.preview_of("Letter.DOCX")
    assert (found.kind, found.content_type, found.converted) == ("pdf", "application/pdf", True)
    assert not previews.has_thumbnail("Letter.DOCX")

    client.force_login(user)
    response = fetched(client, reverse("documents:upload_preview", args=[document.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == "inline"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert response.content.startswith(b"%PDF-")
    assert seen == [(b"PK\x03\x04", ".docx")]

    # Made once: the second view reads the derivative.
    fetched(client, reverse("documents:upload_preview", args=[document.pk]))
    assert len(seen) == 1
    assert default_storage.exists(filestore.converted_name(document.file.name))

    # The file itself is still what is downloaded, and the page draws the viewer.
    download = fetched(client, reverse("documents:upload_download", args=[document.pk]))
    assert "attachment" in download["Content-Disposition"]
    page = client.get(reverse("documents:upload_detail", args=[document.pk]))
    assert b"data-document-viewer" in page.content


def test_what_is_not_a_pdf_is_never_kept_or_answered(client, user):
    offer(lambda data, extension: b"<script>alert(1)</script>")
    document = upload(user, "letter.docx")
    client.force_login(user)
    assert (
        fetched(client, reverse("documents:upload_preview", args=[document.pk])).status_code == 404
    )
    assert not default_storage.exists(filestore.converted_name(document.file.name))


def test_a_converter_that_fails_or_has_nothing_is_a_404_and_is_tried_again(client, user):
    calls = []

    def convert(data, extension):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the service is down")
        return make_pdf(1)

    offer(convert)
    document = upload(user, "letter.odt")
    client.force_login(user)
    address = reverse("documents:upload_preview", args=[document.pk])
    assert fetched(client, address).status_code == 404
    assert fetched(client, address).status_code == 200


def test_another_accounts_converted_preview_is_a_404(client, user, other_user):
    offer(lambda data, extension: make_pdf(1))
    document = upload(user, "letter.docx")
    client.force_login(other_user)
    assert (
        fetched(client, reverse("documents:upload_preview", args=[document.pk])).status_code == 404
    )


@pytest.mark.parametrize(
    "extensions", [(), ("docx",), (".pdf",), (".png",), (".docx", "txt"), (".txt",)]
)
def test_an_offer_with_no_extension_a_bare_one_or_a_kind_postulo_shows_is_refused(extensions):
    assert not offer(lambda data, extension: None, extensions=extensions)
    assert previews.CONVERTERS == {}


def test_a_key_taken_keeps_its_first_converter():
    assert offer(lambda data, extension: None, key="one")
    assert not offer(lambda data, extension: None, key="one", extensions=(".rtf",))
    assert previews.converter_for("a.rtf") is None


def test_the_converted_pdf_goes_with_the_file(user, django_capture_on_commit_callbacks):
    offer(lambda data, extension: make_pdf(1))
    document = upload(user, "letter.docx")
    previews.converted_bytes(document.file, "letter.docx")
    name = filestore.converted_name(document.file.name)
    assert default_storage.exists(name)
    assert name in filestore.thumbnail_names({document.file.name})
    with django_capture_on_commit_callbacks(execute=True):
        document.delete()
    assert not default_storage.exists(name)
