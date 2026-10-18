"""A photograph uploaded as a document loses its location, time and camera, and nothing else (#964).

The file is cut by its structure, not re-encoded: the compressed picture is the bytes it was,
so the pixels are identical, and a checkbox, ticked unless the person unticks it, decides.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image, PngImagePlugin

from postulo.documents import integrity
from postulo.documents.forms import UploadedDocumentForm
from postulo.documents.models import UploadedDocument

pytestmark = pytest.mark.django_db

GPS = 0x8825
ORIENTATION = 0x0112


def pixels(data: bytes) -> bytes:
    return Image.open(io.BytesIO(data)).convert("RGB").tobytes()


def exif_with_a_place(*, turned: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "Phone Maker"  # Make
    exif[0x0110] = "Phone 12"  # Model
    exif[0x9003] = "2026:10:01 09:30:00"  # DateTimeOriginal (in the Exif IFD)
    place = exif.get_ifd(GPS)
    place[1], place[2] = "N", (38.0, 42.0, 0.0)
    place[3], place[4] = "W", (9.0, 8.0, 0.0)
    if turned is not None:
        exif[ORIENTATION] = turned
    return exif


def a_picture() -> Image.Image:
    image = Image.new("RGB", (48, 32))
    image.putdata([(x * 5 % 256, y * 7 % 256, (x * y) % 256) for y in range(32) for x in range(48)])
    return image


def a_jpeg(*, turned=None, **options) -> bytes:
    out = io.BytesIO()
    a_picture().save(out, "JPEG", exif=exif_with_a_place(turned=turned), **options)
    return out.getvalue()


def a_png() -> bytes:
    out = io.BytesIO()
    text = PngImagePlugin.PngInfo()
    text.add_text("Author", "Somebody Private")
    text.add_itxt("Comment", "Taken at home")
    a_picture().save(out, "PNG", exif=exif_with_a_place(), pnginfo=text)
    return out.getvalue()


def chunks(png: bytes) -> list[bytes]:
    names, position = [], 8
    while position < len(png):
        length = int.from_bytes(png[position : position + 4], "big")
        names.append(png[position + 4 : position + 8])
        position += 12 + length
    return names


def submitted(user, name: str, body: bytes, *, strip=True):
    data = {"title": "A photo", "kind": "cv", "notes": ""}
    if strip:
        data["strip_metadata"] = "on"
    form = UploadedDocumentForm(
        data=data, files={"file": SimpleUploadedFile(name, body)}, user=user
    )
    assert form.is_valid(), form.errors
    return form


def kept_bytes(form) -> bytes:
    file = form.cleaned_data["file"]
    file.seek(0)
    return file.read()


# ------------------------------------------------------------------------ what is removed


def test_the_fixtures_really_carry_a_place():
    assert Image.open(io.BytesIO(a_jpeg())).getexif().get_ifd(GPS)
    assert Image.open(io.BytesIO(a_png())).getexif().get_ifd(GPS)


def test_a_jpeg_loses_its_gps_time_and_camera_and_keeps_every_pixel(user):
    original = a_jpeg()
    kept = kept_bytes(submitted(user, "diploma.jpg", original))

    assert kept != original and len(kept) < len(original)
    assert pixels(kept) == pixels(original), "not re-encoded: the same picture"
    exif = Image.open(io.BytesIO(kept)).getexif()
    assert not exif.get_ifd(GPS) and not dict(exif), dict(exif)
    assert b"Phone Maker" not in kept and b"2026:10:01" not in kept


def test_a_photo_taken_sideways_is_still_shown_the_right_way_up(user):
    """Only the orientation survives: how to turn the pixels, not who took them where."""
    kept = kept_bytes(submitted(user, "diploma.jpg", a_jpeg(turned=6)))

    exif = Image.open(io.BytesIO(kept)).getexif()
    assert dict(exif) == {ORIENTATION: 6}
    assert not exif.get_ifd(GPS)


def test_a_progressive_jpeg_with_a_trailer_is_cut_by_its_structure(user):
    original = a_jpeg(progressive=True) + b"\xff\xe1\x00\x08tail" + a_jpeg()
    kept = kept_bytes(submitted(user, "diploma.jpeg", original))

    assert pixels(kept) == pixels(original)
    assert not Image.open(io.BytesIO(kept)).getexif().get_ifd(GPS)
    assert kept.endswith(b"\xff\xd9") and kept.count(b"\xff\xd8") == 1, "nothing after the image"


def test_a_png_loses_its_exif_and_text_chunks_and_keeps_every_pixel(user):
    original = a_png()
    assert {b"eXIf", b"tEXt", b"iTXt"} <= set(chunks(original))

    kept = kept_bytes(submitted(user, "diploma.png", original))

    assert pixels(kept) == pixels(original)
    assert not (set(chunks(kept)) & {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"})
    assert {b"IHDR", b"IDAT", b"IEND"} <= set(chunks(kept))
    assert not Image.open(io.BytesIO(kept)).getexif().get_ifd(GPS)
    assert b"Somebody Private" not in kept and b"Taken at home" not in kept


def test_the_compressed_picture_is_byte_for_byte_what_it_was():
    png = a_png()
    cut = integrity.without_metadata("png", png)
    before = b"".join(
        png[position : position + 12 + int.from_bytes(png[position : position + 4], "big")]
        for position in _positions(png)
        if png[position + 4 : position + 8] == b"IDAT"
    )
    assert before and before in cut


def _positions(png: bytes) -> list[int]:
    found, position = [], 8
    while position < len(png):
        found.append(position)
        position += 12 + int.from_bytes(png[position : position + 4], "big")
    return found


# ------------------------------------------------------------------ what is left alone


def test_unticking_the_box_keeps_the_file_exactly_as_it_is(user):
    original = a_jpeg()
    assert kept_bytes(submitted(user, "diploma.jpg", original, strip=False)) == original


@pytest.mark.parametrize(
    ("extension", "body"),
    [
        ("jpg", b"not a jpeg at all"),
        ("jpeg", b"\xff\xd8\xff\xe1\x00"),
        ("png", b"\x89PNG\r\n\x1a\n"),
    ],
)
def test_bytes_without_the_structure_are_returned_as_they_came(extension, body):
    assert integrity.without_metadata(extension, body) == body


def test_a_file_that_is_not_a_picture_is_never_touched():
    assert integrity.without_metadata("pdf", b"%PDF-1.7 Author") == b"%PDF-1.7 Author"


def test_a_picture_that_carries_nothing_is_kept_as_it_is(user):
    out = io.BytesIO()
    a_picture().save(out, "PNG")
    assert kept_bytes(submitted(user, "plain.png", out.getvalue())) == out.getvalue()


# --------------------------------------------------------------------- through the page


def test_the_page_offers_the_box_ticked_and_the_stored_file_has_no_place(
    client, user, tmp_path, settings
):
    settings.MEDIA_ROOT = str(tmp_path)
    client.force_login(user)
    page = client.get(reverse("documents:upload_create")).content.decode()
    assert 'name="strip_metadata"' in page
    box = page[
        page.index('name="strip_metadata"') - 200 : page.index('name="strip_metadata"') + 200
    ]
    assert "checked" in box

    original = a_jpeg()
    client.post(
        reverse("documents:upload_create"),
        {
            "title": "Diploma",
            "kind": "cv",
            "strip_metadata": "on",
            "file": SimpleUploadedFile("diploma.jpg", original, content_type="image/jpeg"),
        },
    )
    stored = UploadedDocument.objects.for_user(user).get(title="Diploma")
    with stored.file.open("rb") as handle:
        kept = handle.read()
    assert not Image.open(io.BytesIO(kept)).getexif().get_ifd(GPS)
    assert pixels(kept) == pixels(original)
    import hashlib

    assert stored.checksum == hashlib.sha256(kept).hexdigest(), "the checksum is of what is kept"
    assert stored.size == len(kept)


def test_unticked_on_the_page_the_file_is_stored_exactly_as_sent(client, user, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    client.force_login(user)
    original = a_jpeg()
    client.post(
        reverse("documents:upload_create"),
        {
            "title": "Diploma",
            "kind": "cv",
            "file": SimpleUploadedFile("diploma.jpg", original, content_type="image/jpeg"),
        },
    )
    stored = UploadedDocument.objects.for_user(user).get(title="Diploma")
    with stored.file.open("rb") as handle:
        assert handle.read() == original


def test_an_existing_file_cannot_be_changed_so_its_form_has_no_box(user):
    document = UploadedDocument.objects.create(
        owner=user,
        title="Old",
        kind="cv",
        file=SimpleUploadedFile("old.jpg", a_jpeg(), content_type="image/jpeg"),
    )
    form = UploadedDocumentForm(instance=document, user=user)
    assert "strip_metadata" not in form.fields and "file" not in form.fields
