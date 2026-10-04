"""\"I'll get the server to write, or read, a file it should not.\" Paths stay inside media."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from postulo.core import export as export_module
from postulo.core import importer
from postulo.core.files import UnsafeMediaPath, resolve_media_path
from postulo.documents.forms import MAX_UPLOAD_BYTES
from postulo.documents.models import UploadedDocument
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def test_a_stored_name_cannot_escape_the_media_root():
    with pytest.raises(UnsafeMediaPath):
        resolve_media_path("../../etc/passwd")
    with pytest.raises(UnsafeMediaPath):
        resolve_media_path("documents/1/../../../secret")
    inside = resolve_media_path("documents/1/cv.pdf")
    assert Path(settings.MEDIA_ROOT).resolve() in inside.parents


def test_a_crafted_archive_writes_only_under_the_persons_own_directory(user, other_user):
    """An export whose file names try to climb out of media, or into somebody else's."""
    document = export_module.build_document(user)
    document["documents"]["uploads"] = [
        {
            "id": 1,
            "title": "Escape attempt",
            "kind": "cv",
            "notes": "",
            "version": 1,
            "replaces_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "file": "media/../../escaped.pdf",
        },
        {
            "id": 2,
            "title": "Into another account",
            "kind": "cv",
            "notes": "",
            "version": 1,
            "replaces_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "file": f"media/documents/{user.pk}/2026/01/theirs.pdf",
        },
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
        archive.writestr("media/../../escaped.pdf", b"%PDF-1.4 escaped")
        archive.writestr(f"media/documents/{user.pk}/2026/01/theirs.pdf", b"%PDF-1.4 theirs")

    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    root = Path(settings.MEDIA_ROOT).resolve()
    assert not (root.parent / "escaped.pdf").exists()
    for upload in UploadedDocument.objects.for_user(other_user):
        path = Path(upload.file.path).resolve()
        assert root / "documents" / str(other_user.pk) in path.parents, (
            "every imported file lands under the importing account's own directory"
        )


def test_private_files_are_never_served_by_path(client, user, other_user):
    upload = UploadedDocument.objects.create(owner=user, title="Mine")
    upload.file.save("cv.pdf", io.BytesIO(b"%PDF-1.4 private"), save=True)
    assert client.get(f"{settings.MEDIA_URL}{upload.file.name}").status_code == 404, (
        "MEDIA_URL is not routed; the web server never serves media"
    )
    client.force_login(other_user)
    assert client.get(reverse("documents:upload_download", args=[upload.pk])).status_code == 404
    client.force_login(user)
    response = client.get(reverse("documents:upload_download", args=[upload.pk]))
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, max-age=0, no-store"
    response.close()


def _archive_with(document: dict, files: dict[str, bytes]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
        for name, content in files.items():
            archive.writestr(name, content)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def test_an_upload_over_the_cap_is_restored_without_its_file_and_reported(user, other_user):
    document = export_module.build_document(user)
    document["documents"]["uploads"] = [
        {
            "id": 1,
            "title": "Huge",
            "kind": "cv",
            "notes": "",
            "version": 1,
            "replaces_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "file": "media/documents/1/huge.pdf",
        }
    ]
    # Packs to almost nothing, so the entry is small in the zip and large unpacked.
    huge = bytes(1) * (MAX_UPLOAD_BYTES + 1)
    report = importer.load(
        other_user, _archive_with(document, {"media/documents/1/huge.pdf": huge})
    )

    upload = UploadedDocument.objects.for_user(other_user).get(title="Huge")
    assert not upload.file
    assert any("Huge" in line for line in report.skipped)


def test_a_directory_that_understates_an_entry_does_not_defeat_the_cap():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as written:
        written.writestr("media/a.bin", bytes(1) * 5000)
    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    archive.getinfo("media/a.bin").file_size = 10  # what a lying directory says
    assert importer._extract_within(archive, "media/a.bin", 100) is None


def test_an_imported_svg_logo_is_stored_without_its_script(user, other_user):
    Company.objects.create(owner=user, name="Acme")
    document = export_module.build_document(user)
    document["companies"][0]["logo_file"] = "media/logos/1/acme.svg"
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b"<script>alert(1)</script><rect width='10' height='10'/></svg>"
    )
    importer.load(other_user, _archive_with(document, {"media/logos/1/acme.svg": svg}))

    company = Company.objects.for_user(other_user).get(name="Acme")
    if company.logo:
        with company.logo.open("rb") as handle:
            assert b"script" not in handle.read().lower()


def test_an_oversized_manifest_is_refused(monkeypatch):
    monkeypatch.setattr(importer, "MANIFEST_MAX_BYTES", 10)
    archive = _archive_with({"postulo": {"format": 1}}, {})
    with pytest.raises(importer.ArchiveError):
        importer.read_manifest(archive)
