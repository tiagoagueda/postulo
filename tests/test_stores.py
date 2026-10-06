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
from postulo.core import importer, languages
from postulo.core.export import write_archive
from postulo.documents import archiving, kinds
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
    RenderedDocument,
    UploadedDocument,
)
from postulo.documents.rendering import snapshot_cv
from postulo.documents.stores import metadata_for
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import registry
from postulo.plugins.api import DocumentMetadata, ExternalRef, StorePlugin
from postulo.plugins.localstore import LocalStore
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


def copies_of(document):
    """Every copy of one document, whatever kind it is.

    A copy points at its document with a generic link since #130, so this asks the content
    type rather than one of two columns — which is the change that means a third kind of
    document needs no new column and no new branch.
    """
    from django.contrib.contenttypes.models import ContentType

    return DocumentCopy.objects.filter(
        document_type=ContentType.objects.get_for_model(document), document_id=document.pk
    )


class ShelfStore:
    """A store as a package would ship it: it records what it was given."""

    name = "shelf"
    version = "0.1"
    kind = "store"
    label = "Shelf"
    received: ClassVar[list[tuple[str, bytes, DocumentMetadata, dict]]] = []
    fail_with: ClassVar[str | None] = None
    finished_with: ClassVar[str | None] = None
    decline_kinds: ClassVar[set[str]] = set()

    def config_fields(self):
        from postulo.plugins.base import FieldSpec

        return [FieldSpec("path", "Shelf path", type="text")]

    def test(self, config):
        from postulo.plugins.base import TestResult

        return TestResult(True, "shelved")

    def put(self, document, file, metadata, config, user):
        if ShelfStore.finished_with:
            from postulo.plugins.api import ConnectionUnusable

            raise ConnectionUnusable(ShelfStore.finished_with)
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
    ShelfStore.finished_with = None
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


def test_the_day_a_store_files_a_document_under_is_the_owners_not_utc(user):
    user.profile.time_zone = "Europe/Paris"
    user.profile.save()
    render = a_render(user, application=an_application(user))
    late = dt.datetime(2026, 10, 2, 22, 30, tzinfo=dt.UTC)
    type(render).objects.filter(pk=render.pk).update(rendered_at=late)
    render.refresh_from_db()

    metadata = metadata_for(render)

    assert metadata.sent_on == dt.date(2026, 10, 3), "00:30 in Paris is the next day"
    assert metadata.created_on == dt.date(2026, 10, 3)
    upload = an_upload(user)
    type(upload).objects.filter(pk=upload.pk).update(created_at=late)
    upload.refresh_from_db()
    assert metadata_for(upload).created_on == dt.date(2026, 10, 3)


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
    assert metadata.sent_on == timezone.localdate(render.rendered_at)
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
    copies = list(copies_of(upload))
    assert [copy.connection_id for copy in copies] == [wants_all.pk]
    assert copies[0].status == CopyStatus.PENDING and copies[0].label == "All"
    assert copies[0].owner == user and copies[0].store == "shelf"
    assert ShelfStore.received == [], "nothing is sent inside the request"

    # With an application: a CV exported on its own is kept here and queued for nobody
    # since #236, which `test_a_pdf_exported_on_its_own_is_queued_for_no_store` holds.
    render = a_render(user, application=an_application(user))
    assert copies_of(render).count() == 2, "both stores take a CV"

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


def test_a_store_that_says_the_connection_is_finished_is_not_dialled_again(user):
    """What the notifier has done since #216, and the store did not until #243."""
    connection = a_store(user)
    upload = an_upload(user)
    ShelfStore.finished_with = "The share is gone."
    copy = upload.copies.get()

    assert send_pending() == (0, 1)

    connection.refresh_from_db()
    assert not connection.enabled, "switched off rather than retried for ever"
    assert connection.last_error == "The share is gone.", "the plugin's words, not a class name"
    copy.refresh_from_db()
    assert copy.status == CopyStatus.FAILED and copy.last_error == "The share is gone."

    # Nothing is dialled while it is off: a new document is not even queued for it, and the
    # copy that found out waits instead of spending the rest of its attempts.
    another = an_upload(user, "Reference")
    assert not another.copies.exists(), "a switched-off connection is not queued for"
    DocumentCopy.objects.filter(pk=copy.pk).update(next_attempt_at=timezone.now())
    assert send_pending() == (0, 0), "nothing is dialled while it is switched off"
    copy.refresh_from_db()
    assert copy.attempts == 1, "waiting, not spending its attempts"

    # Switching it back on is one press, and the copy resumes where it left off.
    ShelfStore.finished_with = None
    connection.enabled = True
    connection.save(update_fields=["enabled"])

    assert send_pending() == (1, 0), "the waiting copy resumes"
    copy.refresh_from_db()
    assert copy.status == CopyStatus.SENT and copy.last_error == ""


def test_a_copy_whose_connection_row_is_gone_is_never_dialled(user):
    """A leftover with no connection has nothing to send to, so it is not retried (#511)."""
    connection = a_store(user)
    upload = an_upload(user)
    copy = upload.copies.get()
    DocumentCopy.objects.filter(pk=copy.pk).update(connection=None)
    # Switched off, so `send_now` has no store to schedule a new copy for.
    connection.enabled = False
    connection.save(update_fields=["enabled"])

    assert send_pending() == (0, 0)
    assert send_now(upload) == (0, 0)
    copy.refresh_from_db()
    assert copy.status == CopyStatus.PENDING and copy.attempts == 0


def test_removing_a_connection_drops_its_unsent_copies_and_keeps_the_sent_ones(user):
    connection = a_store(user)
    sent = an_upload(user, "Sent")
    failed = an_upload(user, "Failed")
    DocumentCopy.objects.filter(pk=sent.copies.get().pk).update(status=CopyStatus.SENT)
    DocumentCopy.objects.filter(pk=failed.copies.get().pk).update(status=CopyStatus.FAILED)

    connection.delete()

    assert not failed.copies.exists()
    kept = sent.copies.get()
    assert kept.connection is None and kept.status == CopyStatus.SENT
    assert send_now(failed) == (0, 0)


def test_one_copy_is_sent_once_however_many_passes_are_running(user):
    """Two schedulers, or a pass overlapping *Send now*, used to both `put` the same copy."""
    a_store(user)
    upload = an_upload(user)
    copy = upload.copies.get()

    assert archiving.claim(copy) is True
    assert archiving.claim(copy) is False, "the second pass finds nothing to claim"

    # And the claim takes it out of the due set until the lease runs out.
    assert list(archiving.pending_copies()) == []
    assert send_pending() == (0, 0)
    assert ShelfStore.received == [], "not sent twice"

    # A process killed mid-send leaves it to come back by itself, rather than stuck.
    DocumentCopy.objects.filter(pk=copy.pk).update(
        claimed_until=timezone.now() - dt.timedelta(minutes=1)
    )
    assert send_pending() == (1, 0)
    assert len(ShelfStore.received) == 1


def test_send_now_does_not_take_a_copy_the_scheduler_is_sending(user):
    """A lease used to be written as a retry time, which *Send now* read as a wait (#509)."""
    a_store(user)
    upload = an_upload(user)
    copy = upload.copies.get()

    assert archiving.claim(upload.copies.get()) is True, "the scheduler has it"

    assert archiving.send_now(upload) == (0, 0)
    assert ShelfStore.received == [], "the store was not given it a second time"
    copy.refresh_from_db()
    assert copy.status == CopyStatus.PENDING


def test_a_pass_holding_a_copy_another_pass_already_failed_does_not_send_it_again(user):
    """The lease is cleared when a send fails, so a stale row must not find it free (#509)."""
    a_store(user)
    ShelfStore.fail_with = "the shelf is full"
    upload = an_upload(user)
    stale = upload.copies.get()

    assert archiving.claim(upload.copies.get()) is True
    assert send_pending() == (0, 0), "the first pass has it"
    first = upload.copies.get()
    assert archiving.send_copy(first) is False, "and it fails, clearing the lease"
    first.refresh_from_db()
    assert first.claimed_until is None and first.attempts == 1

    assert archiving.claim(stale) is False, "a pass that read it before the failure is too late"


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

    DocumentCopy.objects.filter(pk=copy.pk).update(connection=None)
    copy.refresh_from_db()
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
    assert "My shelf: Waiting to be sent" in html, "the label as the catalogue wrote it (#168)"
    assert reverse("documents:upload_archive", args=[upload.pk]) in html

    ShelfStore.fail_with = "shelf is full"
    send_pending()
    html = client.get(reverse("documents:rendered_list")).content.decode()
    assert "My shelf: Failed — RuntimeError: shelf is full" in html

    ShelfStore.fail_with = None
    DocumentCopy.objects.update(next_attempt_at=timezone.now())
    send_pending()
    html = client.get(reverse("documents:application_documents", args=[render.application.pk]))
    html = html.content.decode()
    assert 'href="https://shelf.example/1"' in html and "Archived" in html


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
    assert "1 copy sent." in response.content.decode()
    assert len(ShelfStore.received) == 1
    response = client.post(url, follow=True)
    assert "already has this document" in response.content.decode()


def test_a_store_is_told_the_kind_in_the_owners_language_whichever_path_sends(user):
    user.profile.language = "fr-FR"
    user.profile.save()
    a_store(user)
    first = an_upload(user, title="One")
    second = an_upload(user, title="Two")
    DocumentCopy.objects.filter(document_id=second.pk).delete()

    with languages.override("pt-PT"):
        assert send_now(second) == (1, 0)
    with languages.override("en-GB"):
        assert send_pending() == (1, 0)

    labels = {m.kind_label for _p, _c, m, _cfg in ShelfStore.received}
    assert len(ShelfStore.received) == 2 and first.pk != second.pk
    assert len(labels) == 1, "one kind, one name"
    with languages.override("fr-FR"):
        assert labels == {kinds.label_for(DocumentKind.CERTIFICATE)}
    assert labels != {"Certificate"}


def test_send_now_without_a_store_explains(client, user):
    upload = an_upload(user)
    client.force_login(user)
    response = client.post(reverse("documents:upload_archive", args=[upload.pk]), follow=True)
    assert "No document store is connected" in response.content.decode()


def test_send_everything_queues_what_existed_before_the_store(client, user, other_user):
    an_upload(user)
    a_render(user, application=an_application(user))
    an_upload(other_user)
    connection = a_store(user, kind_certificate=False)
    assert DocumentCopy.objects.count() == 0

    client.force_login(user)
    response = client.post(reverse("connections:backfill", args=[connection.pk]), follow=True)
    assert "1 document is queued for My shelf" in response.content.decode()
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
    render = a_render(user, application=an_application(user))
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
    # Found through the generic link rather than a column, and its `document` is whatever
    # kind it points at — which is what stops a third kind needing a third branch (#130).
    copy = next(one for one in restored if getattr(one.document, "title", "") == "Diploma")
    assert copy.connection is None and copy.store == "shelf" and copy.label == "My shelf"
    assert copy.external_url == "https://shelf.example/1" and copy.sent_at is not None
    assert copy.next_attempt_at is None, "nothing to retry: it is a record, not a job"
    restored_render = next(one for one in restored if isinstance(one.document, RenderedDocument))
    assert upload.pk != copy.document_id and render.pk != restored_render.document_id


def test_a_deactivated_account_has_no_copies_sent(user):
    """The copies stay queued, so reactivating resumes them (#575)."""
    a_store(user)
    an_upload(user)
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert list(archiving.pending_copies()) == []
    assert send_pending() == (0, 0)
    assert ShelfStore.received == []

    user.is_active = True
    user.save(update_fields=["is_active"])
    assert send_pending() == (1, 0)


# ------------------------------------------------------- reference letters (#666)


def test_a_new_connection_has_the_reference_switch_off_and_every_other_on(client, user):
    """A reference letter is somebody else's words and name: not copied unless asked."""
    import re

    client.force_login(user)
    html = client.get(reverse("connections:create", args=["store", "shelf"])).content.decode()

    def switch(kind):
        tag = re.search(rf'<input[^>]*name="plugin_kind_{kind}"[^>]*>', html).group(0)
        return "checked" in tag

    assert not switch("reference")
    assert switch("cv") and switch("certificate")
    assert "somebody else" in html, "the reason is shown beside it"


def test_an_existing_connection_keeps_the_setting_it_has_for_reference_letters(user):
    from postulo.documents.stores import wants_kind

    old = a_store(user, "Before #666")
    assert "kind_reference" not in old.config
    assert wants_kind(old.config, "reference") is True, "unset still means yes"
    assert wants_kind(a_store(user, "Chose", kind_reference=False).config, "reference") is False


def test_a_copy_whose_bytes_do_not_match_the_recorded_checksum_is_not_sent(user, shelf):
    """What leaves is what was kept: a file that changed on disk is never handed over (#663)."""
    from pathlib import Path

    a_store(user)
    upload = an_upload(user)
    Path(upload.file.path).write_bytes(b"%PDF-1.7 something else")
    copy = copies_of(upload).get()

    assert send_copy(copy) is False

    copy.refresh_from_db()
    assert copy.status == CopyStatus.FAILED
    assert "no longer matches" in copy.last_error
    assert shelf.received == [], "nothing reached the store"


def test_a_copy_of_a_file_that_is_gone_says_so(user, shelf):
    from pathlib import Path

    a_store(user)
    upload = an_upload(user)
    Path(upload.file.path).unlink()
    copy = copies_of(upload).get()

    assert send_copy(copy) is False
    copy.refresh_from_db()
    assert "missing" in copy.last_error and shelf.received == []
