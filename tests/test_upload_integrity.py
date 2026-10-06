"""Uploaded documents: no orphans, no corrupted files, one boundary for the bytes (#663)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from postulo.core import export as export_module
from postulo.core import importer, media
from postulo.documents import filestore, integrity, scrub
from postulo.documents.forms import UploadedDocumentForm
from postulo.documents.models import UploadedDocument

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def picture(kind: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(out, kind)
    return out.getvalue()


def zipped(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return out.getvalue()


ODT = zipped(
    [("mimetype", b"application/vnd.oasis.opendocument.text"), ("content.xml", b"<office/>")]
)
DOCX = zipped([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w/>")])
REAL = {
    "cv.pdf": PDF,
    "cv.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
    "cv.docx": DOCX,
    "cv.odt": ODT,
    "cv.rtf": b"{\\rtf1\\ansi hello}",
    "cv.txt": b"Plain words, no NUL.\n",
    "cv.png": picture("PNG"),
    "cv.jpg": picture("JPEG"),
    "cv.jpeg": picture("JPEG"),
}


def submitted(user, name: str, body: bytes):
    form = UploadedDocumentForm(
        data={"title": "A file", "kind": "cv", "notes": ""},
        files={"file": SimpleUploadedFile(name, body)},
        user=user,
    )
    form.is_valid()
    return form


def an_upload(user, *, name="cv.pdf", body=PDF, title="My CV"):
    return UploadedDocument.objects.create(
        owner=user, title=title, kind="cv", file=ContentFile(body, name=name)
    )


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def age(path: Path, hours: int) -> None:
    then = (timezone.now() - dt.timedelta(hours=hours)).timestamp()
    os.utime(path, (then, then))


# ------------------------------------------------------------- what a file is


@pytest.mark.parametrize("name", sorted(REAL))
def test_a_real_file_of_each_of_the_nine_types_is_accepted(user, name):
    form = submitted(user, name, REAL[name])
    assert "file" not in form.errors, form.errors


def test_a_file_called_pdf_that_is_not_a_pdf_is_refused_with_the_reason(user):
    form = submitted(user, "cv.pdf", b"<html>not a pdf</html>")
    assert "does not look like a PDF" in " ".join(form.errors["file"])


def test_a_pdf_cut_short_is_refused(user):
    form = submitted(user, "cv.pdf", PDF[:-8])
    assert "file" in form.errors


def test_an_odt_whose_first_entry_is_not_the_mimetype_is_refused(user):
    body = zipped([("content.xml", b"<office/>"), ("mimetype", b"x")])
    form = submitted(user, "cv.odt", body)
    assert "OpenDocument" in " ".join(form.errors["file"])


def test_a_docx_with_no_content_types_entry_is_refused(user):
    form = submitted(user, "cv.docx", zipped([("word/document.xml", b"<w/>")]))
    assert "Word document" in " ".join(form.errors["file"])


def test_a_picture_that_is_not_one_is_refused(user):
    assert "file" in submitted(user, "cv.png", b"GIF89a not a png").errors
    assert "file" in submitted(user, "cv.jpg", picture("PNG")).errors, "a PNG called .jpg"


def test_text_with_a_nul_byte_is_not_text(user):
    assert "file" in submitted(user, "notes.txt", b"hello\x00world").errors


def test_looks_like_reads_the_two_ends_only():
    assert integrity.looks_like("pdf", b"%PDF-1.4", b"...%%EOF\n")
    assert not integrity.looks_like("pdf", b"%PDF-1.4", b"no end")
    assert integrity.looks_like("rtf", b"{\\rtf1")
    assert not integrity.looks_like("doc", b"PK\x03\x04")


# ----------------------------------------------------- the write that fails


def test_a_row_the_database_refuses_leaves_no_file_behind(media_root):
    upload = UploadedDocument(title="No owner", file=ContentFile(PDF, name="cv.pdf"))
    with pytest.raises(IntegrityError), transaction.atomic():
        upload.save()
    assert [p for p in media_root.rglob("*") if p.is_file()] == []


def test_a_failure_half_way_through_an_import_leaves_no_file(
    user, other_user, media_root, monkeypatch
):
    an_upload(user)
    archive = zipfile.ZipFile(export_module.write_archive(user))

    class Boom(Exception):
        pass

    def explode(*args, **kwargs):
        raise Boom

    # After the first file is written and its row saved: the rows roll back, the bytes
    # must not stay.
    monkeypatch.setattr(importer, "_restore_copies", explode)
    with pytest.raises(Boom):
        importer.load(other_user, archive)

    mine = media_root / "documents" / str(other_user.pk)
    assert not mine.exists() or [p for p in mine.rglob("*") if p.is_file()] == []
    assert not UploadedDocument.objects.for_user(other_user).exists()


# --------------------------------------------------------------- the archive


def test_the_archive_carries_the_checksum_and_size_of_an_upload(user):
    upload = an_upload(user)
    archive = zipfile.ZipFile(export_module.write_archive(user))
    entry = json.loads(archive.read(export_module.MANIFEST_NAME))["documents"]["uploads"][0]
    assert entry["checksum"] == upload.checksum
    assert entry["size"] == len(PDF) == upload.size


def rebuilt_with(user, change) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(export_module.write_archive(user))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as rebuilt:
        for member in archive.namelist():
            rebuilt.writestr(member, change(member, archive.read(member)))
    return zipfile.ZipFile(io.BytesIO(out.getvalue()))


def test_an_upload_changed_in_the_archive_is_reported_and_restored_without_its_file(
    user, other_user
):
    an_upload(user)

    def change(member, body):
        return body.replace(b"obj<<>>", b"obj<<X>>") if member.endswith(".pdf") else body

    report = importer.load(other_user, rebuilt_with(user, change))

    restored = UploadedDocument.objects.for_user(other_user).get()
    assert not restored.file
    assert any("checksum" in line for line in report.skipped)


def test_an_upload_truncated_in_the_archive_is_reported(user, other_user):
    an_upload(user)

    def change(member, body):
        return body[:-9] if member.endswith(".pdf") else body

    report = importer.load(other_user, rebuilt_with(user, change))
    assert not UploadedDocument.objects.for_user(other_user).get().file
    assert report.skipped


def test_an_upload_that_is_not_what_its_name_says_is_not_restored(user, other_user):
    an_upload(user)

    def change(member, body):
        if member == export_module.MANIFEST_NAME:
            document = json.loads(body)
            for entry in document["documents"]["uploads"]:
                entry.pop("checksum")
                entry.pop("size")
            return json.dumps(document).encode()
        return b"<html>" if member.endswith(".pdf") else body

    report = importer.load(other_user, rebuilt_with(user, change))
    assert not UploadedDocument.objects.for_user(other_user).get().file
    assert any("not what its name says" in line for line in report.skipped)


def test_an_archive_without_the_figures_still_restores(user, other_user):
    an_upload(user)

    def change(member, body):
        if member == export_module.MANIFEST_NAME:
            document = json.loads(body)
            for entry in document["documents"]["uploads"]:
                entry.pop("checksum")
                entry.pop("size")
            return json.dumps(document).encode()
        return body

    importer.load(other_user, rebuilt_with(user, change))
    restored = UploadedDocument.objects.for_user(other_user).get()
    with restored.file.open("rb") as handle:
        assert handle.read() == PDF
    assert restored.size == len(PDF)


def test_a_sent_document_whose_checksum_does_not_match_its_bytes_is_reported(user, other_user):
    from postulo.documents.models import RenderedDocument

    sent = RenderedDocument.objects.create(
        owner=user,
        title="CV as sent",
        kind="cv",
        file=ContentFile(PDF, name="sent.pdf"),
        checksum=hashlib.sha256(PDF).hexdigest(),
    )
    assert sent.checksum

    def change(member, body):
        if member == export_module.MANIFEST_NAME:
            document = json.loads(body)
            document["documents"]["sent"][0]["checksum"] = "0" * 64
            return json.dumps(document).encode()
        return body

    report = importer.load(other_user, rebuilt_with(user, change))
    assert any("checksum" in line for line in report.skipped)
    assert not RenderedDocument.objects.for_user(other_user).get().file


# ------------------------------------------------------------------ serving


def test_a_stored_name_outside_the_owners_folder_is_not_served(client, user, media_root):
    upload = an_upload(user)
    other = media_root / "documents" / "999999" / "theirs.pdf"
    other.parent.mkdir(parents=True)
    other.write_bytes(PDF)
    UploadedDocument.objects.filter(pk=upload.pk).update(file="documents/999999/theirs.pdf")

    client.force_login(user)
    assert client.get(reverse("documents:upload_download", args=[upload.pk])).status_code == 404


def test_an_own_file_is_still_served(client, user):
    upload = an_upload(user)
    client.force_login(user)
    response = client.get(reverse("documents:upload_download", args=[upload.pk]))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF


# --------------------------------------------------------------- the scrub


def test_verify_media_reports_a_missing_file_a_changed_byte_and_a_stray_file(
    user, media_root, capsys
):
    missing = an_upload(user, name="gone.pdf", title="Gone")
    changed = an_upload(user, name="changed.pdf", title="Changed")
    fine = an_upload(user, name="fine.pdf", title="Fine")
    Path(missing.file.path).unlink()
    Path(changed.file.path).write_bytes(PDF.replace(b"1 0", b"2 0"))
    stray = media_root / "documents" / str(user.pk) / "stray.pdf"
    stray.write_bytes(PDF)

    call_command("verify_media")

    out = capsys.readouterr().out
    assert "missing" in out and "changed" in out and "stray" in out
    assert "Nothing was deleted" in out
    assert stray.is_file() and Path(changed.file.path).is_file() and Path(fine.file.path).is_file()
    assert UploadedDocument.objects.get(pk=missing.pk).damage == integrity.Damage.MISSING
    assert UploadedDocument.objects.get(pk=changed.pk).damage == integrity.Damage.CHANGED
    assert UploadedDocument.objects.get(pk=fine.pk).damage == ""
    assert UploadedDocument.objects.get(pk=fine.pk).verified_at is not None


def test_the_files_page_says_a_file_is_damaged_and_how_to_mend_it(client, user, media_root):
    upload = an_upload(user)
    Path(upload.file.path).unlink()
    scrub.verify_uploads()

    client.force_login(user)
    html = client.get(reverse("documents:upload_list")).content.decode()
    assert 'data-damage="missing"' in html


def test_the_scrub_reads_the_least_recently_checked_first(user, media_root):
    first = an_upload(user, name="a.pdf", title="A")
    second = an_upload(user, name="b.pdf", title="B")
    scrub.verify_uploads(limit=1)
    assert UploadedDocument.objects.get(pk=first.pk).verified_at is not None
    assert UploadedDocument.objects.get(pk=second.pk).verified_at is None
    scrub.verify_uploads(limit=1)
    assert UploadedDocument.objects.get(pk=second.pk).verified_at is not None


# ---------------------------------------------------------------- the sweep


def test_the_sweep_removes_an_old_file_with_no_row_and_nothing_else(user, media_root):
    from postulo.core.models import ExportArchive

    named = an_upload(user)
    old = media_root / "documents" / str(user.pk) / "old.pdf"
    recent = media_root / "documents" / str(user.pk) / "recent.pdf"
    for path in (old, recent):
        path.write_bytes(PDF)
    age(old, 48)
    age(Path(named.file.path), 48)
    archive = ExportArchive.objects.create(
        owner=user,
        file=ContentFile(b"PK the whole job search", name="export.zip"),
        filename="export.zip",
        size=23,
        expires_at=timezone.now() + dt.timedelta(hours=24),
    )
    age(Path(archive.file.path), 48)

    removed = scrub.sweep()

    assert removed == [f"documents/{user.pk}/old.pdf"]
    assert not old.exists()
    assert recent.is_file(), "inside the grace period: a request may be about to attach it"
    assert Path(named.file.path).is_file(), "a row names it"
    assert Path(archive.file.path).is_file(), "a live export is referenced"


def test_the_sweep_can_be_switched_off(user, media_root, settings):
    settings.POSTULO_MEDIA_SWEEP = False
    old = media_root / "documents" / "stray.pdf"
    old.parent.mkdir(parents=True)
    old.write_bytes(PDF)
    age(old, 100)
    assert scrub.sweep() == []
    assert old.is_file()


def test_the_scheduled_pass_runs_the_slice_and_the_sweep(user, media_root, capsys):
    old = media_root / "documents" / "stray.pdf"
    old.parent.mkdir(parents=True)
    old.write_bytes(PDF)
    age(old, 100)
    an_upload(user)

    call_command("send_due_reminders")

    assert not old.exists()
    assert UploadedDocument.objects.get().verified_at is not None


# ---------------------------------------------------------- the boundary


def test_the_store_lists_what_it_holds(user, media_root):
    upload = an_upload(user)
    listed = {stored.name: stored for stored in filestore.listing()}
    assert listed[upload.file.name].size == len(PDF)
    assert filestore.totals() == (1, len(PDF))
    assert media.referenced_names() == {upload.file.name}


FIVE = (
    "core/files.py",
    "documents/management/commands/prune_media.py",
    "accounts/deletion.py",
    "core/backup.py",
    "core/server_views.py",
)


@pytest.mark.parametrize("module", FIVE)
def test_the_five_places_that_touched_the_directory_ask_the_store(module):
    """Where the bytes live is `documents.filestore`'s to say, and nobody else's (#663)."""
    import postulo

    source = (Path(postulo.__file__).parent / module).read_text(encoding="utf8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""'))
    )
    assert not re.search(r"settings\.MEDIA_ROOT", code), module
