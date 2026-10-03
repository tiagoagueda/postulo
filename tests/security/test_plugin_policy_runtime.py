"""The policy is the gate where a plugin runs, not only where it is listed (#362).

`policy.plugins_for` hid a plugin an administrator had switched off, and the code that runs
a plugin never asked it: notifiers kept delivering, stores kept receiving documents and syncs
kept running. Each kind is checked in each of the ways a plugin stops being on for somebody:
forced off for them, unavailable to them, forced off for everybody, and -- for a plugin that
was installed rather than shipped -- their own switch. Nothing is deleted and nothing waiting
spends its attempts, so reversing the decision resumes it.
"""

from __future__ import annotations

import json
from typing import ClassVar
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.documents import archiving
from postulo.documents.models import CopyStatus, DocumentCopy, DocumentKind, UploadedDocument
from postulo.notifications import webhooks
from postulo.notifications.base import Notification
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.notifications.service import notify
from postulo.plugins import policy, registry, syncing, webhook
from postulo.plugins.api import ExternalRef
from postulo.plugins.base import FieldSpec, SyncReport
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection, PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()

#: The ways a plugin stops acting for somebody.
MODES = ("forced-off", "unavailable", "forced-off-for-everybody", "their-own-switch")


def switch_off(mode, name, person, monkeypatch):
    """Put the plugin off for ``person`` in this way. Returns what undoes it."""
    State = PluginPolicy.State
    if mode == "their-own-switch":
        # A plugin shipped inside Postulo is never the person's to switch (#200); the one
        # that is has been installed, which these stand-ins are not.
        monkeypatch.setattr(policy, "shipped_inside", lambda _name: False)
        person.profile.plugins_off = [name]
        person.profile.save()
        policy.forget_decisions()

        def undo():
            person.profile.plugins_off = []
            person.profile.save()
            policy.forget_decisions()

        return undo
    row = PluginPolicy.objects.create(
        plugin=name,
        person=None if mode == "forced-off-for-everybody" else person,
        state=State.UNAVAILABLE if mode == "unavailable" else State.FORCED_OFF,
    )
    return row.delete


# ------------------------------------------------------------------ the stand-ins


class Postbox:
    name = "postbox"
    version = "1"
    kind = "notifier"
    label = "Postbox"
    sent: ClassVar[list] = []

    def config_fields(self):
        return []

    def test(self, config):
        Postbox.sent.append("test")
        return Outcome(True, "sent")

    def send(self, notification, config, user):
        Postbox.sent.append(notification.title)


class Shelf:
    name = "shelf"
    version = "1"
    kind = "store"
    label = "Shelf"
    received: ClassVar[list] = []

    def config_fields(self):
        return [FieldSpec("path", "Path", type="text")]

    def test(self, config):
        return Outcome(True, "shelved")

    def put(self, document, file, metadata, config, user):
        Shelf.received.append(file.read())
        return ExternalRef(store="shelf", id="1", url="https://shelf.example/1")


class Mirror:
    name = "mirror"
    version = "1"
    kind = "sync"
    label = "Mirror"
    runs: ClassVar[list] = []

    def config_fields(self):
        return []

    def test(self, config):
        return Outcome(True, "mirrored")

    def sync(self, connection, config):
        Mirror.runs.append(connection.pk)
        return SyncReport()


@pytest.fixture(autouse=True)
def stand_ins():
    Postbox.sent, Shelf.received, Mirror.runs = [], [], []
    for plugin in (Postbox, Shelf, Mirror):
        registry.register_builtin(plugin.kind, plugin)
    yield
    for plugin in (Postbox, Shelf, Mirror):
        registry.unregister_builtin(plugin.kind, plugin)


def a_connection(user, kind, plugin, **config):
    return Connection.objects.create(
        owner=user, kind=kind, plugin=plugin, label=plugin, config=config
    )


# ----------------------------------------------------------------------- notifiers


@pytest.mark.parametrize("mode", MODES)
def test_a_notifier_that_is_off_delivers_nothing_and_resumes(mode, user, monkeypatch):
    a_connection(user, "notifier", "postbox", event_reminder_due=True)
    undo = switch_off(mode, "postbox", user, monkeypatch)

    assert notify(user, Notification(event="reminder_due", title="Chase them")) == 0
    assert Postbox.sent == []
    assert Connection.objects.filter(owner=user).count() == 1, "nothing was deleted"

    undo()
    assert notify(user, Notification(event="reminder_due", title="Chase them")) == 1
    assert Postbox.sent == ["Chase them"]


@pytest.mark.parametrize("mode", MODES)
def test_a_queued_webhook_waits_without_spending_an_attempt(mode, user, monkeypatch):
    connection = a_connection(user, "notifier", "webhook", url="https://hooks.example.org/postulo")
    connection.secrets = {"secret": "a-secret-of-sixteen-characters-or-more"}
    connection.save()
    row = WebhookDelivery.objects.create(
        owner=user,
        connection=connection,
        event="reminder_due",
        key="k",
        body=json.dumps({}).encode(),
        next_attempt_at=webhooks.timezone.now(),
    )
    undo = switch_off(mode, "webhook", user, monkeypatch)
    posted = mock.Mock(return_value=mock.Mock(status_code=200))

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", posted),
    ):
        assert webhooks.send_pending() == (0, 0)
        assert webhooks.deliver(row) is False, "even when handed the row directly"
        posted.assert_not_called()
        row.refresh_from_db()
        assert row.attempts == 0 and row.status == DeliveryStatus.PENDING

        undo()
        assert webhooks.send_pending() == (1, 0)
    row.refresh_from_db()
    assert row.status == DeliveryStatus.SENT


# ------------------------------------------------------------------------- stores


def an_upload(user):
    upload = UploadedDocument(owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE)
    upload.file.save("diploma.pdf", ContentFile(b"%PDF-1.7 diploma"), save=False)
    upload.save()
    return upload


@pytest.mark.parametrize("mode", MODES)
def test_a_store_that_is_off_receives_nothing_and_resumes(mode, user, monkeypatch):
    store = a_connection(user, "store", "shelf", path="/shelf")
    upload = an_upload(user)
    assert DocumentCopy.objects.filter(connection=store).count() == 1, "queued while on"
    undo = switch_off(mode, "shelf", user, monkeypatch)

    assert archiving.send_pending() == (0, 0)
    assert archiving.send_now(upload) == (0, 0)
    copy = DocumentCopy.objects.get()
    assert archiving.send_copy(copy) is False
    copy.refresh_from_db()
    assert copy.attempts == 0 and copy.status == CopyStatus.PENDING
    assert Shelf.received == []
    assert not archiving.store_connections(user).exists(), "nothing new is queued for it"
    assert archiving.schedule_copies(upload) == []

    undo()
    assert archiving.send_pending() == (1, 0)
    assert Shelf.received == [b"%PDF-1.7 diploma"]


# -------------------------------------------------------------------------- syncs


@pytest.mark.parametrize("mode", MODES)
def test_a_sync_that_is_off_does_not_run_and_resumes(mode, user, monkeypatch):
    connection = a_connection(user, "sync", "mirror")
    undo = switch_off(mode, "mirror", user, monkeypatch)

    assert syncing.run_syncs() == (0, 0)
    assert syncing.sync_connection(connection).error
    connection.refresh_from_db()
    assert connection.synced_at is None, "nothing was recorded either"
    assert Mirror.runs == []

    undo()
    assert syncing.run_syncs()[0] == 1
    assert Mirror.runs == [connection.pk]


# ---------------------------------------------------------------- sources, importers


PAGE = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(
        {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "Senior Backend Engineer",
            "description": "Work on things.",
            "hiringOrganization": {"@type": "Organization", "name": "Aperture Science"},
        }
    )
    + "</script></head><body><p>Body</p></body></html>"
)


@pytest.mark.parametrize("mode", MODES)
def test_a_source_that_is_off_does_not_read_the_persons_captures(mode, user, monkeypatch):
    from postulo.jobs.remembered import read_page

    url = "https://jobs.example.org/1"
    _data, source, _handed = read_page(user, url, PAGE)
    assert source.name == "schema.org"
    undo = switch_off(mode, "schema.org", user, monkeypatch)

    found = read_page(user, url, PAGE)
    assert found is None or found[1].name != "schema.org"

    undo()
    assert read_page(user, url, PAGE)[1].name == "schema.org"


@pytest.mark.parametrize("mode", MODES)
def test_an_importer_that_is_off_does_not_read_the_persons_file(mode, client, user, monkeypatch):
    from tests.test_europass import upload

    client.force_login(user)
    url = reverse("resume:europass_import")
    undo = switch_off(mode, "europass", user, monkeypatch)

    client.post(url, {"file": upload()})
    assert client.session.get("europass_import") is None

    undo()
    client.post(url, {"file": upload()})
    assert client.session.get("europass_import")


# ----------------------------------------------------------------------- outboxes


@pytest.mark.parametrize("mode", ("forced-off", "unavailable", "forced-off-for-everybody"))
def test_an_outbox_that_is_off_cannot_send_as_the_person(mode, user, monkeypatch):
    from postulo.core import correspondence

    a_connection(user, "outbox", "own-mail", from_address="alex@example.org")
    assert correspondence.connection_for(user) is not None
    undo = switch_off(mode, "own-mail", user, monkeypatch)

    assert correspondence.outbox_for(user) is None
    assert correspondence.connection_for(user) is None

    undo()
    assert correspondence.connection_for(user) is not None


# -------------------------------------------------------------------- the pages


def test_the_create_address_of_an_unavailable_plugin_is_not_found(client, user):
    client.force_login(user)
    PluginPolicy.objects.create(plugin="postbox", person=user, state=PluginPolicy.State.UNAVAILABLE)
    url = reverse("connections:create", args=["notifier", "postbox"])

    assert client.get(url).status_code == 404
    assert client.post(url, {"label": "Mine"}).status_code == 404
    assert not Connection.objects.exists()


def test_a_post_for_a_plugin_forced_off_saves_nothing(client, user):
    client.force_login(user)
    PluginPolicy.objects.create(plugin="postbox", person=user, state=PluginPolicy.State.FORCED_OFF)
    url = reverse("connections:create", args=["notifier", "postbox"])

    response = client.post(url, {"label": "Mine"})

    assert response.status_code == 302
    assert not Connection.objects.exists()


def test_test_sync_now_send_everything_and_consent_call_nothing_that_is_off(client, user):
    client.force_login(user)
    notifier = a_connection(user, "notifier", "postbox")
    sync = a_connection(user, "sync", "mirror")
    store = a_connection(user, "store", "shelf", path="/shelf")
    an_upload(user)
    DocumentCopy.objects.all().delete()
    for name in ("postbox", "mirror", "shelf"):
        PluginPolicy.objects.create(plugin=name, person=user, state=PluginPolicy.State.FORCED_OFF)

    client.post(reverse("connections:test", args=[notifier.pk]))
    client.post(reverse("connections:sync_now", args=[sync.pk]))
    client.post(reverse("connections:backfill", args=[store.pk]))
    client.post(reverse("connections:consent", args=[notifier.pk]))

    assert Postbox.sent == [] and Mirror.runs == [] and Shelf.received == []
    assert not DocumentCopy.objects.exists(), "Send everything queued nothing"
    notifier.refresh_from_db()
    assert notifier.last_ok_at is None
