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
from postulo.documents.models import UploadedDocument

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
