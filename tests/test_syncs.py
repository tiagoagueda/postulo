"""Sync plugins: an interval per connection, the scheduler, Sync now, and the link table."""

from __future__ import annotations

import datetime as dt
import io
from typing import ClassVar

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.jobs.models import Company, Contact
from postulo.plugins import registry, syncing
from postulo.plugins.base import FieldSpec, SyncPlugin, SyncReport
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import AlreadyPaired, Connection, SyncLink

pytestmark = pytest.mark.django_db


class MirrorSync:
    """A sync as a package would ship it: it links every contact to a made-up twin."""

    name = "mirror"
    version = "0.1"
    kind = "sync"
    label = "Mirror"
    runs: ClassVar[list[dict]] = []
    fail_with: ClassVar[str | None] = None
    during: ClassVar[object] = None

    def config_fields(self):
        return [FieldSpec("url", "Server", type="url")]

    def test(self, config):
        return Outcome(True, "mirrored")

    def sync(self, connection, config):
        if MirrorSync.during:
            MirrorSync.during()
            return SyncReport()
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
    MirrorSync.during = None
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


def contacts(user, *names):
    """Several contacts at one company, for the tests where two records want one item."""
    company = Company.objects.create(owner=user, name="Aperture Science")
    return [Contact.objects.create(owner=user, company=company, name=name) for name in names]


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


def test_a_run_leaves_a_connection_another_run_is_syncing_alone(user, monkeypatch):
    """Overlapping passes both find a connection due; only the one that claims it runs it (#576)."""
    a_sync(user)
    nested = []
    original = MirrorSync.sync

    def sync_while_another_pass_comes_round(self, connection, config):
        if not nested:
            nested.append(syncing.run_syncs())
        return original(self, connection, config)

    monkeypatch.setattr(MirrorSync, "sync", sync_while_another_pass_comes_round)

    assert syncing.run_syncs() == (1, 0)
    assert nested == [(0, 0)], "the second run found the claim and left it"
    assert len(MirrorSync.runs) == 1


def test_a_stale_list_of_due_connections_does_not_run_one_twice(user):
    connection = a_sync(user)
    stale = syncing.due_connections()
    assert syncing.claim_connection(connection) is True
    assert syncing.claim_connection(stale[0]) is False, "the row changed under the second run"


def test_a_slow_sync_does_not_hold_up_everything_behind_it(user, monkeypatch):
    """The budget decides whether to *begin* another, which is the only safe place to stop."""
    for label in ("One", "Two", "Three"):
        a_sync(user, label)
    clock = iter([0.0, 0.0, 99.0, 99.0])
    monkeypatch.setattr("postulo.plugins.syncing.time.monotonic", lambda: next(clock))

    assert syncing.run_syncs(budget=60) == (1, 0), "one ran, the rest wait for the next pass"
    assert len(MirrorSync.runs) == 1


def test_with_no_budget_every_due_sync_runs(user):
    for label in ("One", "Two", "Three"):
        a_sync(user, label)

    assert syncing.run_syncs() == (3, 0)


def test_a_sync_that_raises_outright_is_one_sync_and_not_the_whole_pass(user, monkeypatch):
    a_sync(user, "Breaks")
    a_sync(user, "Fine")
    calls = {"n": 0}
    real = syncing.sync_connection

    def sometimes(connection):
        calls["n"] += 1
        if connection.label == "Breaks":
            raise RuntimeError("not even a report")
        return real(connection)

    monkeypatch.setattr("postulo.plugins.syncing.sync_connection", sometimes)

    assert syncing.run_syncs() == (2, 1)
    assert calls["n"] == 2, "the second one still had its turn"


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


def test_a_connection_runs_once_at_a_time(user, monkeypatch):
    connection = a_sync(user)
    inner = []
    MirrorSync.during = lambda: inner.append(syncing.sync_connection(connection))
    outer = syncing.sync_connection(connection)

    assert not outer.already_running
    assert len(inner) == 1 and inner[0].already_running
    assert "already running" in inner[0].notes[0]
    assert MirrorSync.runs == [], "the inner call never reached the plugin"
    assert cache.add(syncing.lease_key(connection), "x", 5), "the lease is gone afterwards"
    cache.delete(syncing.lease_key(connection))


def test_the_lease_is_let_go_when_the_plugin_raises(user):
    connection = a_sync(user)
    MirrorSync.fail_with = "boom"
    assert syncing.sync_connection(connection).error == "RuntimeError: boom"
    assert cache.add(syncing.lease_key(connection), "x", 5)
    cache.delete(syncing.lease_key(connection))


def test_sync_now_on_a_held_connection_says_so_and_does_not_run(client, user):
    connection = a_sync(user)
    cache.add(syncing.lease_key(connection), "x", 60)
    client.force_login(user)
    response = client.post(reverse("connections:sync_now", args=[connection.pk]), follow=True)
    assert "A sync of this connection is already running." in response.content.decode()
    assert MirrorSync.runs == []
    connection.refresh_from_db()
    assert connection.synced_at is None, "a refused run leaves no trace on the connection"


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


# ------------------------------------------- collections, slots and unpairing (#326)


def test_one_record_holds_a_twin_per_slot(user):
    """An interview's event beside its task: two twins of one record, one per slot."""
    connection = a_sync(user)
    contact = a_contact(user)
    SyncLink.bind(connection, contact, collection="/cal/", uid="event-1")
    SyncLink.bind(connection, contact, slot="task", collection="/tasks/", uid="task-1")
    SyncLink.bind(connection, contact, slot="task", etag='"2"')

    assert SyncLink.objects.count() == 2, "binding a slot again updates its own row"
    assert SyncLink.for_record(connection, contact).uid == "event-1"
    task = SyncLink.for_record(connection, contact, "task")
    assert (task.uid, task.etag) == ("task-1", '"2"')
    assert SyncLink.for_record(connection, contact, "hear-back") is None
    assert set(SyncLink.of_model(connection, Contact).values_list("slot", flat=True)) == {
        "",
        "task",
    }


def test_one_uid_in_two_collections_is_two_items(user):
    """A UID is unique only within its collection (RFC 6352 §5.1, RFC 4791 §4.1)."""
    connection = a_sync(user)
    work, home = contacts(user, "Cave Johnson", "GLaDOS")
    SyncLink.bind(connection, work, collection="/books/work/", uid="same")
    SyncLink.bind(connection, home, collection="/books/home/", uid="same")

    assert SyncLink.for_remote(connection, "/books/work/", "same").target == work
    assert SyncLink.for_remote(connection, "/books/home/", "same").target == home
    assert SyncLink.for_remote(connection, "/books/other/", "same") is None
    assert SyncLink.for_remote(a_sync(user, "Tablet"), "/books/work/", "same") is None


def test_one_remote_item_is_paired_with_one_record(user):
    """A second record is refused the item, with the pairing that holds it named."""
    connection = a_sync(user)
    first, second = contacts(user, "Cave Johnson", "GLaDOS")
    held = SyncLink.bind(connection, first, collection="/book/", uid="card")

    with pytest.raises(AlreadyPaired) as refused:
        SyncLink.bind(connection, second, collection="/book/", uid="card")
    assert refused.value.link == held
    assert refused.value.link.target == first
    assert SyncLink.for_record(connection, second) is None, "nothing half-written"

    # Filling in the collection afterwards meets the same rule.
    SyncLink.bind(connection, second, uid="card")
    with pytest.raises(AlreadyPaired):
        SyncLink.bind(connection, second, collection="/book/")
    # Another connection, or a blank collection (every row before #326), is not held to it.
    SyncLink.bind(a_sync(user, "Tablet"), second, collection="/book/", uid="card")
    other, third = (
        Contact.objects.create(owner=user, company=first.company, name=name)
        for name in ("Wheatley", "Chell")
    )
    SyncLink.bind(connection, other, uid="loose")
    SyncLink.bind(connection, third, uid="loose")


def test_the_database_holds_the_rule_too(user):
    """A plugin writing past `bind` meets the constraint itself."""
    from django.db import IntegrityError, transaction

    connection = a_sync(user)
    first, second = contacts(user, "Cave Johnson", "GLaDOS")
    SyncLink.bind(connection, first, collection="/book/", uid="card")
    link = SyncLink.bind(connection, second, collection="/book/", uid="other")
    link.uid = "card"
    with pytest.raises(IntegrityError), transaction.atomic():
        link.save()


def test_unbind_keeps_the_row_unpaired_and_bind_pairs_it_again(user):
    connection = a_sync(user)
    contact = a_contact(user)
    SyncLink.bind(connection, contact, collection="/book/", uid="card", etag='"1"')

    link = SyncLink.unbind(connection, contact)
    assert link.paired is False
    assert SyncLink.objects.count() == 1, "the row stays, so the plugin leaves both alone"
    kept = SyncLink.for_record(connection, contact)
    assert (kept.collection, kept.uid, kept.etag) == ("/book/", "card", '"1"')
    assert kept.unpaired_at is not None
    assert SyncLink.for_remote(connection, "/book/", "card") == kept
    when = kept.unpaired_at
    SyncLink.unbind(connection, contact)
    assert SyncLink.for_record(connection, contact).unpaired_at == when, "the first time stays"
    contact.refresh_from_db()  # the record itself is untouched
    assert SyncLink.unbind(connection, contact, "task") is None

    relinked = SyncLink.bind(connection, contact, etag='"2"')
    assert relinked.pk == kept.pk
    assert relinked.paired
    assert SyncLink.for_record(connection, contact).unpaired_at is None


def test_an_unpaired_item_may_be_paired_with_another_record(user):
    """An unpaired row does not count: the item is free, and `for_remote` finds the new pair."""
    connection = a_sync(user)
    first, second = contacts(user, "Cave Johnson", "GLaDOS")
    SyncLink.bind(connection, first, collection="/book/", uid="card")
    SyncLink.unbind(connection, first)

    moved = SyncLink.bind(connection, second, collection="/book/", uid="card")
    assert SyncLink.for_remote(connection, "/book/", "card") == moved
    with pytest.raises(AlreadyPaired) as refused:
        SyncLink.bind(connection, first)
    assert refused.value.link == moved
    assert not SyncLink.for_record(connection, first).paired


def test_bind_clears_remote_gone_unless_told(user):
    connection = a_sync(user)
    contact = a_contact(user)
    SyncLink.bind(connection, contact, uid="card", remote_gone=True)
    assert SyncLink.for_record(connection, contact).remote_gone is True

    SyncLink.bind(connection, contact, etag='"2"')
    assert SyncLink.for_record(connection, contact).remote_gone is False
    SyncLink.bind(connection, contact, remote_gone=True)
    assert SyncLink.for_record(connection, contact).remote_gone is True


@pytest.mark.django_db(transaction=True)
def test_the_migration_keeps_every_link(user):
    """Every existing link survives, blank in what #326 adds, even two holding one UID.

    A re-added DAV connection can hold two links with one UID (postulo/postulo-dav#7); the
    new rule does not reach them while their collection is blank.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection as db
    from django.db.migrations.executor import MigrationExecutor

    # The records are made with today's models: the migration does not touch them.
    cave, caroline = contacts(user, "Cave", "Caroline")
    kind = ContentType.objects.get_for_model(Contact).pk
    before = [("plugins", "0012_register_lookup_starts_off")]
    after = [("plugins", "0013_synclink_slots")]
    executor = MigrationExecutor(db)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    sync = old.get_model("plugins", "Connection").objects.create(
        owner_id=user.pk, kind="sync", plugin="mirror", label="Phone"
    )
    for record in (cave, caroline):
        old.get_model("plugins", "SyncLink").objects.create(
            owner_id=user.pk,
            connection=sync,
            content_type_id=kind,
            object_id=record.pk,
            remote_href=f"/book/{record.name}.vcf",
            uid="same",
        )

    executor = MigrationExecutor(db)
    executor.migrate(after)
    new = executor.loader.project_state(after).apps
    rows = new.get_model("plugins", "SyncLink").objects.order_by("remote_href")
    assert list(rows.values_list("remote_href", "uid", "collection", "slot", "unpaired_at")) == [
        ("/book/Caroline.vcf", "same", "", "", None),
        ("/book/Cave.vcf", "same", "", "", None),
    ]

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(db).migrate(MigrationExecutor(db).loader.graph.leaf_nodes())


def test_a_deactivated_account_is_not_synced(user):
    """Its connections wait with their rows, and reactivating picks them up again (#575)."""
    connection = a_sync(user)
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert syncing.due_connections() == []
    assert syncing.run_syncs() == (0, 0)
    assert MirrorSync.runs == []

    user.is_active = True
    user.save(update_fields=["is_active"])
    assert syncing.due_connections() == [connection]


# ------------------------------------------------------ worded for the reader (#384)


def _reading_german(user):
    """A blank profile language is the instance's, not the browser's (#398), so say it."""
    user.profile.language = "de"
    user.profile.save(update_fields=["language"])


def _marking_the_language(monkeypatch):
    """Stand in for a catalogue the suite does not compile: every counter says its language."""
    from django.utils import translation

    def ngettext(singular, plural, number):
        return f"[{translation.get_language()}] " + (singular if number == 1 else plural)

    def gettext(message):
        return f"[{translation.get_language()}] {message}"

    monkeypatch.setattr(translation, "ngettext", ngettext)
    monkeypatch.setattr(translation, "gettext", gettext)


def test_a_run_keeps_its_numbers_and_the_sentence_is_worded_when_read(user, monkeypatch):
    from postulo.core import languages

    connection = a_sync(user)
    a_contact(user)
    syncing.sync_connection(connection)
    connection.refresh_from_db()

    assert connection.last_report == {
        "pushed": 1,
        "pulled": 0,
        "removed": 0,
        "skipped": 0,
        "notes": ["all quiet"],
    }
    assert connection.last_summary == "1 pushed · all quiet"

    _marking_the_language(monkeypatch)
    with languages.override("de"):
        worded = connection.last_summary
    assert worded == "[de] 1 pushed · all quiet"


def test_the_counters_of_a_report_take_their_plural_form_from_the_count():
    assert SyncReport(pushed=1).summary() == "1 pushed"
    assert SyncReport(pushed=3, skipped=1).summary() == "3 pushed, 1 skipped"
    assert SyncReport().summary() == "nothing to do"


def test_the_connections_page_words_the_last_run_in_the_readers_language(client, user, monkeypatch):
    connection = a_sync(user)
    a_contact(user)
    syncing.sync_connection(connection)
    _reading_german(user)
    client.force_login(user)
    _marking_the_language(monkeypatch)

    html = client.get(reverse("connections:list"), headers={"Accept-Language": "de"}).content
    assert "[de]" in html.decode() and "[de] 1 pushed" in html.decode()


def test_the_sync_now_flash_is_worded_in_the_requests_language(client, user, monkeypatch):
    connection = a_sync(user)
    a_contact(user)
    _reading_german(user)
    client.force_login(user)
    _marking_the_language(monkeypatch)

    response = client.post(
        reverse("connections:sync_now", args=[connection.pk]),
        follow=True,
        headers={"Accept-Language": "de"},
    )
    assert "[de] 1 pushed" in response.content.decode()


# ------------------------------------------------- as the person it is for (#335)


class MovingSync(MirrorSync):
    """A calendar sync that moves an interview and words a note, as the DAV plugin does."""

    name = "mover"
    label = "Mover"
    interview_id: ClassVar[int] = 0
    moved_to: ClassVar[dt.datetime | None] = None

    def sync(self, connection, config):
        from django.utils.translation import gettext

        from postulo.applications.models import Interview
        from postulo.applications.services import reschedule_interview

        interview = Interview.objects.get(pk=MovingSync.interview_id)
        end = MovingSync.moved_to + dt.timedelta(hours=1)
        reschedule_interview(interview, starts_at=MovingSync.moved_to, ends_at=end)
        report = SyncReport()
        report.notes.append(gettext("Recorded what you sent."))
        return report


@pytest.fixture
def an_owner_far_from_the_server(user, settings):
    """A French reader in New York, on a server in Paris, with an interview to be moved."""
    from postulo.applications.models import Application, Status
    from postulo.applications.services import schedule_interview
    from postulo.jobs.models import JobPosting

    settings.TIME_ZONE = "Europe/Paris"
    user.profile.language = "fr-FR"
    user.profile.time_zone = "America/New_York"
    user.profile.save()
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    interview = schedule_interview(
        application, kind="video", starts_at=timezone.now() + dt.timedelta(days=30)
    )
    MovingSync.interview_id = interview.pk
    MovingSync.moved_to = dt.datetime(2027, 1, 12, 14, 0, tzinfo=dt.UTC)
    registry.register_builtin("sync", MovingSync)
    yield interview
    registry.unregister_builtin("sync", MovingSync)


def moving_connection(user):
    connection = a_sync(user)
    connection.plugin = "mover"
    connection.save()
    return connection


def what_the_run_wrote(interview, connection):
    from django.utils import translation

    interview.refresh_from_db()
    connection.refresh_from_db()
    with translation.override("fr-FR"):
        note = translation.gettext("Recorded what you sent.")
    # 14:00 UTC is 09:00 in New York and 15:00 in Paris.
    assert "09:00" in interview.reminder.summary and "15:00" not in interview.reminder.summary
    assert note != "Recorded what you sent.", "the catalogue has it"
    assert note in connection.last_summary


def test_a_scheduled_sync_runs_in_its_owners_language_and_zone(user, an_owner_far_from_the_server):
    from django.utils import translation

    connection = moving_connection(user)
    translation.activate("en-gb")
    timezone.deactivate()

    syncing.run_syncs()

    what_the_run_wrote(an_owner_far_from_the_server, connection)


def test_sync_now_writes_the_same_text_as_the_scheduler(client, user, an_owner_far_from_the_server):
    connection = moving_connection(user)
    client.force_login(user)

    client.post(reverse("connections:sync_now", args=[connection.pk]))

    what_the_run_wrote(an_owner_far_from_the_server, connection)


def test_a_run_that_outlived_its_lease_does_not_free_the_next_runs(user):
    """It deleted the key whatever it held, so a third run could start beside the second (#988)."""
    connection = a_sync(user)
    key = syncing.lease_key(connection)
    MirrorSync.during = lambda: cache.set(key, "the-next-runs-token", 60)

    syncing.sync_connection(connection)

    assert cache.get(key) == "the-next-runs-token"
    cache.delete(key)
