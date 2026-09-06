"""Document stores: local media built in, copies to external stores through the scheduler."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from typing import ClassVar

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.core import importer
from postulo.core.export import write_archive
from postulo.documents import archiving
from postulo.documents.archiving import (
    backfill,
    schedule_copies,
    send_copy,
    send_now,
    send_pending,
)
from postulo.documents.models import (
    CV,
    CopyStatus,
    DocumentCopy,
    DocumentKind,
    UploadedDocument,
)
from postulo.documents.rendering import snapshot_cv
from postulo.documents.stores import (
    DocumentMetadata,
    ExternalRef,
    LocalStore,
    StorePlugin,
    metadata_for,
)
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import registry
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


class ShelfStore:
    """A store as a package would ship it: it records what it was given."""

    name = "shelf"
    version = "0.1"
    kind = "store"
    label = "Shelf"
    received: ClassVar[list[tuple[str, bytes, DocumentMetadata, dict]]] = []
    fail_with: ClassVar[str | None] = None
    decline_kinds: ClassVar[set[str]] = set()

    def config_fields(self):
        from postulo.plugins.base import FieldSpec

        return [FieldSpec("path", "Shelf path", type="text")]

    def test(self, config):
        from postulo.plugins.base import TestResult

        return TestResult(True, "shelved")

    def put(self, document, file, metadata, config, user):
        if ShelfStore.fail_with:
            raise RuntimeError(ShelfStore.fail_with)
        if metadata.kind in ShelfStore.decline_kinds:
            return None
        content = file.read()
        ShelfStore.received.append((config["path"], content, metadata, config))
        return ExternalRef(
            store="shelf", id=f"doc-{len(ShelfStore.received)}", url="https://shelf.example/1"
        )


@pytest.fixture(autouse=True)
def shelf():
    ShelfStore.received = []
    ShelfStore.fail_with = None
    ShelfStore.decline_kinds = set()
    registry.register_builtin("store", ShelfStore)
    yield ShelfStore
    registry.unregister_builtin("store", ShelfStore)


def a_store(user, label="My shelf", *, enabled=True, **config):
    connection = Connection(
        owner=user,
        kind="store",
        plugin="shelf",
        label=label,
        enabled=enabled,
        config={"path": "/shelf", **config},
    )
    connection.save()
    return connection


def an_upload(user, title="Diploma", kind=DocumentKind.CERTIFICATE):
    upload = UploadedDocument(owner=user, title=title, kind=kind)
    upload.file.save("diploma.pdf", ContentFile(b"%PDF-1.7 diploma"), save=False)
    upload.save()
    return upload


def an_application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


class FakeBackend:
    name = "fake"

    def is_available(self):
        return True

    def render(self, html):
        return b"%PDF-1.7 fake"


def a_render(user, application=None):
    cv = CV.objects.create(owner=user, name="Backend EN")
    return snapshot_cv(cv, application=application, backend=FakeBackend())


# ----------------------------------------------------------------- the contract


def test_the_local_store_is_a_store_and_needs_no_connection(client, user):
    assert isinstance(LocalStore(), StorePlugin)
    assert registry.find_plugin("store", "local") is not None
    client.force_login(user)
    html = client.get(reverse("connections:pick")).content.decode()
    assert "Shelf" in html and "This instance" not in html


def test_a_render_is_written_through_the_local_store(user, settings):
    render = a_render(user, application=an_application(user))
    assert render.file.name.startswith(f"documents/{user.pk}/")
    with render.file.open("rb") as handle:
        assert handle.read() == b"%PDF-1.7 fake"

    metadata = metadata_for(render)
    assert metadata.origin == "render" and metadata.kind == "cv"
    assert metadata.kind_label == "CV" and metadata.content_type == "application/pdf"
    assert metadata.company == "Black Mesa" and metadata.role == "Research Engineer"
    assert metadata.application_url.endswith(render.application.get_absolute_url())
    assert metadata.sent_on == render.rendered_at.date()
    assert metadata.checksum == render.checksum and metadata.size == len(b"%PDF-1.7 fake")
    assert metadata.tags == ("postulo", "cv")

    upload = an_upload(user)
    metadata = metadata_for(upload)
    assert metadata.origin == "upload" and metadata.kind == "certificate"
    assert metadata.company == "" and metadata.sent_on is None
    assert metadata.filename.startswith("diploma")


def test_a_store_connection_carries_a_switch_per_kind(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:create", args=["store", "shelf"])).content.decode()
    for kind in DocumentKind:
        assert f'name="plugin_kind_{kind.value}"' in html, kind
    response = client.post(
        reverse("connections:create", args=["store", "shelf"]),
        {
            "label": "Shelf at home",
            "enabled": "on",
            "plugin_path": "/shelf",
            "plugin_kind_cv": "on",
            "plugin_kind_cover_letter": "on",
        },
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert connection.config["kind_cv"] is True and connection.config["kind_certificate"] is False


# ------------------------------------------------------------------ scheduling


def test_a_new_document_is_queued_for_every_store_that_wants_its_kind(user):
    wants_all = a_store(user, "All")
    a_store(user, "No certificates", kind_certificate=False)
    a_store(user, "Off", enabled=False)

    upload = an_upload(user)
    copies = list(DocumentCopy.objects.filter(upload=upload))
    assert [copy.connection_id for copy in copies] == [wants_all.pk]
    assert copies[0].status == CopyStatus.PENDING and copies[0].label == "All"
    assert copies[0].owner == user and copies[0].store == "shelf"
    assert ShelfStore.received == [], "nothing is sent inside the request"

    render = a_render(user)
    assert DocumentCopy.objects.filter(rendered=render).count() == 2, "both stores take a CV"

    # Scheduling again changes nothing.
    assert schedule_copies(upload) == [] and schedule_copies(render) == []


def test_editing_a_document_does_not_queue_it_again(user):
    a_store(user)
    upload = an_upload(user)
    DocumentCopy.objects.all().delete()
    upload.title = "Renamed"
    upload.save()
    assert DocumentCopy.objects.count() == 0


# --------------------------------------------------------------------- sending


def test_the_scheduler_sends_what_is_pending_and_keeps_the_reference(user):
    connection = a_store(user)
    upload = an_upload(user)
    render = a_render(user, application=an_application(user))

    assert send_pending() == (2, 0)
    assert len(ShelfStore.received) == 2
    path, _content, _metadata, config = ShelfStore.received[0]
    assert path == "/shelf" and config == {"path": "/shelf"}
    assert {m.origin for _p, _c, m, _cfg in ShelfStore.received} == {"upload", "render"}
    assert any(c == b"%PDF-1.7 diploma" for _p, c, _m, _cfg in ShelfStore.received)

    for document in (upload, render):
        copy = document.copies.get()
        assert copy.status == CopyStatus.SENT
        assert copy.external_id.startswith("doc-") and copy.external_url.startswith("https://")
        assert copy.sent_at is not None and copy.attempts == 1
    connection.refresh_from_db()
    assert connection.last_ok_at is not None

    assert send_pending() == (0, 0), "sent once, and never again"


def test_a_failure_is_retried_with_a_growing_wait_and_then_left_to_the_person(user):
    a_store(user)
    upload = an_upload(user)
    ShelfStore.fail_with = "shelf is full"
    copy = upload.copies.get()

    assert send_pending() == (0, 1)
    copy.refresh_from_db()
    assert copy.status == CopyStatus.FAILED and "shelf is full" in copy.last_error
    assert copy.attempts == 1
    wait = copy.next_attempt_at - copy.last_attempt_at
    assert wait == dt.timedelta(minutes=5)

    assert send_pending() == (0, 0), "not due yet"

    for attempt in range(2, archiving.MAX_ATTEMPTS + 1):
        DocumentCopy.objects.filter(pk=copy.pk).update(next_attempt_at=timezone.now())
        assert send_pending() == (0, 1)
        copy.refresh_from_db()
        assert copy.attempts == attempt
        assert copy.next_attempt_at - copy.last_attempt_at == dt.timedelta(
            minutes=5 * 2 ** (attempt - 1)
        )

    DocumentCopy.objects.filter(pk=copy.pk).update(next_attempt_at=timezone.now())
    assert send_pending() == (0, 0), "given up until someone asks"

    # Asking again gives the attempts back, and the store has recovered.
    ShelfStore.fail_with = None
    assert send_now(upload) == (1, 0)
    copy.refresh_from_db()
    assert copy.status == CopyStatus.SENT and copy.last_error == ""


def test_a_store_may_decline_a_kind(user):
    a_store(user)
    ShelfStore.decline_kinds = {"certificate"}
    upload = an_upload(user)
    assert send_pending() == (0, 1)
    copy = upload.copies.get()
    assert copy.status == CopyStatus.DECLINED and copy.next_attempt_at is None
    assert send_pending() == (0, 0), "a decline is final until someone asks"


def test_a_missing_plugin_or_connection_is_a_failure_in_words(user):
    connection = a_store(user)
    upload = an_upload(user)
    copy = upload.copies.get()
    connection.plugin = "gone"
    connection.save()
    assert send_copy(copy) is False
    assert "gone plugin is not installed" in copy.last_error

    connection.delete()
    copy.refresh_from_db()
    assert copy.connection is None, "the copy outlives the connection"
    assert send_copy(copy) is False and "gone or switched off" in copy.last_error


def test_the_scheduler_command_reports_copies(user):
    a_store(user)
    an_upload(user)
    out = io.StringIO()
    call_command("send_due_reminders", stdout=out)
    assert "1 document copies sent, 0 failed" in out.getvalue()
    out = io.StringIO()
    call_command("send_due_reminders", stdout=out)
    assert "Nothing due." in out.getvalue()


# --------------------------------------------------------------- the interface


def test_the_pages_say_how_each_copy_is_getting_on(client, user):
    a_store(user)
    upload = an_upload(user)
    render = a_render(user, application=an_application(user))
    client.force_login(user)

    html = client.get(reverse("documents:upload_list")).content.decode()
    assert "My shelf: waiting to be sent" in html
    assert reverse("documents:upload_archive", args=[upload.pk]) in html

    ShelfStore.fail_with = "shelf is full"
    send_pending()
    html = client.get(reverse("documents:rendered_list")).content.decode()
    assert "My shelf: failed — RuntimeError: shelf is full" in html

    ShelfStore.fail_with = None
    DocumentCopy.objects.update(next_attempt_at=timezone.now())
    send_pending()
    html = client.get(reverse("documents:application_documents", args=[render.application.pk]))
    html = html.content.decode()
    assert 'href="https://shelf.example/1"' in html and "archived" in html


def test_send_now_tries_at_once_and_is_private(client, user, other_user):
    a_store(user)
    upload = an_upload(user)
    client.force_login(other_user)
    url = reverse("documents:upload_archive", args=[upload.pk])
    assert client.post(url).status_code == 404
    assert ShelfStore.received == []

    client.force_login(user)
    response = client.post(url, {"next": reverse("documents:upload_list")}, follow=True)
    assert response.redirect_chain[-1][0] == reverse("documents:upload_list")
    assert "1 copies sent" in response.content.decode()
    assert len(ShelfStore.received) == 1
    response = client.post(url, follow=True)
    assert "already has this document" in response.content.decode()


def test_send_now_without_a_store_explains(client, user):
    upload = an_upload(user)
    client.force_login(user)
    response = client.post(reverse("documents:upload_archive", args=[upload.pk]), follow=True)
    assert "No document store is connected" in response.content.decode()


def test_send_everything_queues_what_existed_before_the_store(client, user, other_user):
    an_upload(user)
    a_render(user)
    an_upload(other_user)
    connection = a_store(user, kind_certificate=False)
    assert DocumentCopy.objects.count() == 0

    client.force_login(user)
    response = client.post(reverse("connections:backfill", args=[connection.pk]), follow=True)
    assert "1 documents are queued for My shelf" in response.content.decode()
    assert DocumentCopy.objects.filter(owner=user).count() == 1, "the certificate was not wanted"
    assert DocumentCopy.objects.filter(owner=other_user).count() == 0

    response = client.post(reverse("connections:backfill", args=[connection.pk]), follow=True)
    assert "Nothing to queue" in response.content.decode()
    assert backfill(connection) == 0

    client.force_login(other_user)
    assert client.post(reverse("connections:backfill", args=[connection.pk])).status_code == 404


def test_copies_are_private_to_their_owner(client, user, other_user):
    a_store(user)
    an_upload(user)
    send_pending()
    client.force_login(other_user)
    html = client.get(reverse("documents:upload_list")).content.decode()
    assert "My shelf" not in html and "shelf.example" not in html


# ----------------------------------------------------------- export and import


def test_references_travel_in_the_export_and_survive_an_import(user, other_user):
    a_store(user)
    upload = an_upload(user)
    render = a_render(user)
    an_upload(user, title="Pending one", kind=DocumentKind.OTHER)
    ShelfStore.decline_kinds = {"other"}
    send_pending()

    archive = write_archive(user)
    with zipfile.ZipFile(archive) as bundle:
        import json

        manifest = json.loads(bundle.read("postulo.json"))
    uploads = {entry["title"]: entry for entry in manifest["documents"]["uploads"]}
    assert uploads["Diploma"]["copies"][0]["store"] == "shelf"
    assert uploads["Diploma"]["copies"][0]["label"] == "My shelf"
    assert uploads["Diploma"]["copies"][0]["external_url"] == "https://shelf.example/1"
    assert uploads["Pending one"]["copies"] == [], "only copies that arrived are facts"
    sent = manifest["documents"]["sent"][0]
    assert sent["copies"][0]["external_id"].startswith("doc-")

    archive.seek(0)
    with zipfile.ZipFile(archive) as bundle:
        report = importer.load(other_user, bundle)
    assert report.uploads == 2 and report.sent_documents == 1
    restored = DocumentCopy.objects.filter(owner=other_user, status=CopyStatus.SENT)
    assert restored.count() == 2
    copy = restored.get(upload__title="Diploma")
    assert copy.connection is None and copy.store == "shelf" and copy.label == "My shelf"
    assert copy.external_url == "https://shelf.example/1" and copy.sent_at is not None
    assert copy.next_attempt_at is None, "nothing to retry: it is a record, not a job"
    assert (
        upload.pk != copy.upload_id
        and render.pk != restored.get(rendered__isnull=False).rendered_id
    )
