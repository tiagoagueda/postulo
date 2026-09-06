"""Sync plugins: an interval per connection, the scheduler, Sync now, and the link table."""

from __future__ import annotations

import datetime as dt
import io
from typing import ClassVar

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.jobs.models import Company, Contact
from postulo.plugins import registry, syncing
from postulo.plugins.base import FieldSpec, SyncPlugin, SyncReport
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection, SyncLink

pytestmark = pytest.mark.django_db


class MirrorSync:
    """A sync as a package would ship it: it links every contact to a made-up twin."""

    name = "mirror"
    version = "0.1"
    kind = "sync"
    label = "Mirror"
    runs: ClassVar[list[dict]] = []
    fail_with: ClassVar[str | None] = None

    def config_fields(self):
        return [FieldSpec("url", "Server", type="url")]

    def test(self, config):
        return Outcome(True, "mirrored")

    def sync(self, connection, config):
        MirrorSync.runs.append(config)
        if MirrorSync.fail_with:
            raise RuntimeError(MirrorSync.fail_with)
        report = SyncReport()
        for contact in Contact.objects.for_user(connection.owner):
            link = SyncLink.for_record(connection, contact)
            if link is None:
                SyncLink.bind(
                    connection,
                    contact,
                    remote_href=f"/book/{contact.pk}.vcf",
                    uid=f"uid-{contact.pk}",
                    etag='"1"',
                    local_hash="h1",
                    last_synced_at=timezone.now(),
                )
                report.pushed += 1
        report.notes.append("all quiet")
        return report


@pytest.fixture(autouse=True)
def mirror():
    MirrorSync.runs = []
    MirrorSync.fail_with = None
    registry.register_builtin("sync", MirrorSync)
    yield MirrorSync
    registry.unregister_builtin("sync", MirrorSync)


def a_sync(user, label="Phone", *, enabled=True, **config):
    connection = Connection(
        owner=user,
        kind="sync",
        plugin="mirror",
        label=label,
        enabled=enabled,
        config={"url": "https://dav.example.org", **config},
    )
    connection.save()
    return connection


def a_contact(user, name="Cave Johnson"):
    company = Company.objects.create(owner=user, name="Aperture Science")
    return Contact.objects.create(owner=user, company=company, name=name)


# ---------------------------------------------------------------- the pieces


def test_the_mirror_is_a_sync_and_a_report_reads_as_a_sentence():
    assert isinstance(MirrorSync(), SyncPlugin)
    assert SyncReport().summary() == "nothing to do"
    report = SyncReport(pushed=2, pulled=1, notes=["one event is not ours"])
    assert report.summary() == "2 pushed, 1 pulled · one event is not ours"


def test_a_sync_connection_carries_an_interval(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:create", args=["sync", "mirror"])).content.decode()
    assert 'name="plugin_interval"' in html and "Every hour" in html
    response = client.post(
        reverse("connections:create", args=["sync", "mirror"]),
        {
            "label": "Phone",
            "enabled": "on",
            "plugin_url": "https://dav.example.org",
            "plugin_interval": "15",
        },
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert syncing.interval_minutes(connection) == 15


def test_a_connection_is_due_at_first_and_then_on_its_interval(user):
    connection = a_sync(user, interval="60")
    now = timezone.now()
    assert syncing.is_due(connection, now)
    connection.synced_at = now - dt.timedelta(minutes=30)
    assert not syncing.is_due(connection, now)
    connection.synced_at = now - dt.timedelta(minutes=61)
    assert syncing.is_due(connection, now)
    connection.config["interval"] = "nonsense"
    assert syncing.interval_minutes(connection) == 60


# ------------------------------------------------------------------ running


def test_the_scheduler_runs_what_is_due_and_records_the_report(user):
    connection = a_sync(user)
    a_sync(user, "Off", enabled=False)
    a_contact(user)

    assert syncing.run_syncs() == (1, 0)
    assert len(MirrorSync.runs) == 1 and MirrorSync.runs[0]["url"] == "https://dav.example.org"
    connection.refresh_from_db()
    assert connection.synced_at is not None and connection.last_ok_at is not None
    assert connection.last_summary == "1 pushed · all quiet"
    link = SyncLink.objects.get()
    assert link.owner == user and link.remote_href.endswith(".vcf")
    assert link.target.name == "Cave Johnson"

    assert syncing.run_syncs() == (0, 0), "not due again for an hour"
    Connection.objects.filter(pk=connection.pk).update(
        synced_at=timezone.now() - dt.timedelta(hours=2)
    )
    assert syncing.run_syncs() == (1, 0)
    connection.refresh_from_db()
    assert connection.last_summary == "nothing to do · all quiet"


def test_a_failing_sync_is_recorded_and_tried_again_on_the_next_interval(user):
    connection = a_sync(user)
    MirrorSync.fail_with = "the phone is off"
    assert syncing.run_syncs() == (1, 1)
    connection.refresh_from_db()
    assert connection.last_error == "RuntimeError: the phone is off"
    assert connection.last_ok_at is None and connection.synced_at is not None

    connection.plugin = "gone"
    connection.save()
    report = syncing.sync_connection(connection)
    assert "gone plugin is not installed" in report.error


def test_the_scheduler_command_reports_syncs(user):
    a_sync(user)
    out = io.StringIO()
    call_command("send_due_reminders", stdout=out)
    assert "1 syncs ran, 0 failed" in out.getvalue()


def test_sync_now_runs_at_once_and_is_private(client, user, other_user):
    connection = a_sync(user)
    a_contact(user)
    client.force_login(other_user)
    assert client.post(reverse("connections:sync_now", args=[connection.pk])).status_code == 404

    client.force_login(user)
    response = client.post(reverse("connections:sync_now", args=[connection.pk]), follow=True)
    html = response.content.decode()
    assert "Synced: 1 pushed · all quiet." in html
    assert "Last run" in html and "1 pushed · all quiet" in html

    MirrorSync.fail_with = "no answer"
    response = client.post(reverse("connections:sync_now", args=[connection.pk]), follow=True)
    assert "Sync failed: RuntimeError: no answer" in response.content.decode()


# ------------------------------------------------------------------- links


def test_links_are_one_per_record_per_connection_and_die_with_it(user, other_user):
    connection = a_sync(user)
    other = a_sync(user, "Tablet")
    contact = a_contact(user)
    SyncLink.bind(connection, contact, remote_href="/a.vcf", etag='"1"')
    SyncLink.bind(connection, contact, remote_href="/a.vcf", etag='"2"')
    SyncLink.bind(other, contact, remote_href="/b.vcf")
    assert SyncLink.objects.count() == 2
    assert SyncLink.for_record(connection, contact).etag == '"2"'
    assert list(SyncLink.of_model(other, Contact)) == [SyncLink.for_record(other, contact)]
    assert SyncLink.for_record(a_sync(other_user), contact) is None

    connection.delete()
    assert SyncLink.objects.count() == 1, "the links go with their connection"
    contact.delete()
    link = SyncLink.objects.get()
    assert link.target is None, (
        "a record deleted here leaves a dangling link for the plugin to clean"
    )
