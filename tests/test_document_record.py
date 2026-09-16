"""The record of what was sent stays true, and a deleted file is really deleted (#217).

Three ways it was not:

- deleting an application — or a listing, or a company, each of which cascades into
  applications — took the frozen PDFs an employer had received with it;
- editing an upload replaced the file in place, so an application said it had sent something
  it never sent;
- nothing removed a file from disk except deleting a whole account, so "deleted" meant
  "hidden" for files that hold a home address and a career.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.documents.models import RenderedDocument, UploadedDocument
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def a_render(user, application=None, *, name="sent.pdf", sent_to="Research Engineer at Black Mesa"):
    return RenderedDocument.objects.create(
        owner=user,
        title="CV as sent",
        kind="cv",
        application=application,
        sent_to=sent_to if application is not None else "",
        file=ContentFile(b"%PDF-1.7 frozen", name=name),
        checksum="abc",
    )


def an_upload(user, *, name="cv.pdf", body=b"%PDF-1.7 mine"):
    return UploadedDocument.objects.create(
        owner=user, title="My CV", kind="cv", file=ContentFile(body, name=name)
    )


# ------------------------------------------------- what an employer received survives


def test_deleting_an_application_keeps_what_was_sent(user, application):
    render = a_render(user, application)

    application.delete()

    render.refresh_from_db()
    assert render.application is None, "the link goes"
    assert render.sent_to == "Research Engineer at Black Mesa", "it still says where it went"
    assert Path(render.file.path).is_file(), "and the PDF is still there"


def test_deleting_a_company_keeps_what_was_sent(user, application):
    render = a_render(user, application)

    application.posting.company.delete()

    assert not Application.objects.filter(pk=application.pk).exists(), "the application goes"
    render.refresh_from_db()
    assert render.application is None and render.sent_to


def test_a_snapshot_records_where_it_went_when_it_is_taken(user, application):
    from postulo.documents.rendering import sent_to

    assert sent_to(application) == "Research Engineer at Black Mesa"
    assert sent_to(None) == ""


# --------------------------------------------------------- saying so before it happens


def test_deleting_an_application_says_what_goes_and_what_is_kept(client, user, application):
    from postulo.applications.services import change_status

    change_status(application, Status.INTERVIEWING)
    a_render(user, application)
    client.force_login(user)

    page = client.get(reverse("applications:delete", args=[application.pk])).content.decode()

    assert "data-consequences" in page
    assert "entries on its timeline" in page or "entry on its timeline" in page
    assert "document you sent is kept" in page


def test_deleting_a_company_counts_the_applications_it_takes(client, user, application):
    client.force_login(user)

    page = client.get(
        reverse("jobs:company_delete", args=[application.posting.company.pk])
    ).content.decode()

    assert "1 posting" in page and "1 application" in page


def test_deleting_an_upload_warns_that_applications_say_it_was_sent(client, user, application):
    upload = an_upload(user)
    application.sent_uploads.add(upload)
    client.force_login(user)

    page = client.get(reverse("documents:upload_delete", args=[upload.pk])).content.decode()

    assert "recorded as sent with 1 application" in page


# ------------------------------------------------------------- the file cannot be swapped


def test_an_upload_keeps_the_file_it_arrived_with(client, user):
    upload = an_upload(user)
    before = upload.file.name
    client.force_login(user)

    response = client.post(
        reverse("documents:upload_update", args=[upload.pk]),
        {
            "title": "Renamed",
            "kind": "cv",
            "notes": "",
            "file": SimpleUploadedFile("other.pdf", b"%PDF-1.7 someone else", "application/pdf"),
        },
    )

    assert response.status_code in (302, 200)
    upload.refresh_from_db()
    assert upload.title == "Renamed", "the rest of the form still works"
    assert upload.file.name == before, "the bytes an application says it sent cannot change"


def test_an_upload_is_checksummed_when_it_arrives(user):
    import hashlib

    upload = an_upload(user, body=b"exactly these bytes")

    assert upload.checksum == hashlib.sha256(b"exactly these bytes").hexdigest()

    from postulo.documents.stores import metadata_for

    assert metadata_for(upload).checksum == upload.checksum, "a store can deduplicate it now"


# --------------------------------------------------------------- deleting means deleting


def test_deleting_a_document_removes_its_file(user, django_capture_on_commit_callbacks):
    """After the commit, deliberately: a rolled-back delete must not take the file."""
    upload = an_upload(user)
    path = Path(upload.file.path)
    assert path.is_file()

    with django_capture_on_commit_callbacks(execute=True):
        upload.delete()

    assert not path.exists(), "deleted means deleted, for a file holding a whole career"


def test_a_rolled_back_delete_keeps_the_file(user):
    """The transaction never commits, so the callback never runs and the bytes stay."""
    from django.db import transaction

    upload = an_upload(user)
    path = Path(upload.file.path)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), transaction.atomic():
        upload.delete()
        raise Rollback

    assert path.is_file()


def test_deleting_a_render_removes_its_file(user, application, django_capture_on_commit_callbacks):
    render = a_render(user, application)
    path = Path(render.file.path)

    with django_capture_on_commit_callbacks(execute=True):
        render.delete()

    assert not path.exists()


def test_a_file_another_row_still_points_at_is_left_alone(user, django_capture_on_commit_callbacks):
    first = an_upload(user, name="shared.pdf")
    second = UploadedDocument.objects.create(
        owner=user, title="Same file", kind="cv", file=first.file.name
    )
    path = Path(first.file.path)

    with django_capture_on_commit_callbacks(execute=True):
        first.delete()

    assert path.is_file(), "the row that still points at it keeps it"
    with django_capture_on_commit_callbacks(execute=True):
        second.delete()
    assert not path.exists()


def test_prune_media_lists_orphans_and_removes_them_when_told(user, tmp_path, settings, capsys):
    settings.MEDIA_ROOT = str(tmp_path)
    upload = an_upload(user)
    kept = Path(upload.file.path)
    orphan = tmp_path / "documents" / "stray.pdf"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"%PDF-1.7 nobody's")

    call_command("prune_media")
    listed = capsys.readouterr().out
    assert "stray.pdf" in listed and "Nothing was deleted" in listed
    assert orphan.is_file(), "listing alone never deletes"

    call_command("prune_media", "--remove")
    assert not orphan.exists()
    assert kept.is_file(), "a file a record points at is never touched"
