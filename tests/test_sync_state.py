"""What a sync keeps between two runs of one connection (#1165).

A sync had nowhere to note how far it had got, so one that could not mark the other side
(a mailbox left untouched, a folder that refuses keywords) read the same things again on
every pass. A run now reads a small state with `sync_state(connection)` and returns the next
one on its `SyncReport`. These tests hold the rules around it: kept only after a run that did
not fail, dropped when the connection is set up differently, bounded, JSON only, one per
connection, and never shown to anybody.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import ClassVar
from unittest import mock

import pytest
from django.db import connection as database
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from postulo.core.models import Lease
from postulo.plugins import consent, discovery, registry, syncing
from postulo.plugins.api import (
    MAX_SYNC_STATE_BYTES,
    ConnectionUnusable,
    SyncReport,
    sync_state,
)
from postulo.plugins.base import Consent, DiscoveredOption, FieldSpec
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


class Counting:
    """A sync as a package would ship it: it notes how far it read and goes on from there."""

    name = "counting"
    version = "0.1"
    kind = "sync"
    label = "Counting"
    #: What each run started from, as ``(the connection's label, its state)``.
    started_from: ClassVar[list[tuple[str, dict]]] = []
    #: What a run does in place of counting, given the connection and where it started.
    instead: ClassVar[object] = None

    def config_fields(self):
        return [
            FieldSpec("url", "Server", type="url"),
            FieldSpec("password", "Password", type="password", secret=True, required=False),
        ]

    def test(self, config):
        return Outcome(True, "counted")

    def sync(self, connection, config):
        before = sync_state(connection)
        Counting.started_from.append((connection.label, dict(before)))
        if Counting.instead:
            return Counting.instead(connection, before)
        return SyncReport(pulled=1, state={"last_uid": before.get("last_uid", 0) + 1})


class Stateless(Counting):
    """A sync written before there was a state: it reads none and returns none."""

    name = "stateless"
    label = "Stateless"

    def sync(self, connection, config):
        Counting.started_from.append((connection.label, {}))
        return SyncReport(pushed=2, notes=["all quiet"])


class Agreeing(Counting):
    """A sync that signs in by consent, whose tokens Postulo renews by itself."""

    name = "agreeing"
    label = "Agreeing"

    def config_fields(self):
        return [
            FieldSpec("client_id", "Client id"),
            FieldSpec("client_secret", "Client secret", type="password", secret=True),
        ]

    def needs_consent(self):
        return Consent(
            authorise_url="https://provider.example/authorise",
            token_url="https://provider.example/token",
            scopes=("mail",),
            provider="Provider",
        )


@pytest.fixture(autouse=True)
def counting():
    Counting.started_from = []
    Counting.instead = None
    for plugin in (Counting, Stateless, Agreeing):
        registry.register_builtin("sync", plugin)
    registry.plugins("sync", refresh=True)
    yield Counting
    for plugin in (Counting, Stateless, Agreeing):
        registry.unregister_builtin("sync", plugin)
    registry.plugins("sync", refresh=True)


def a_sync(user, label="Mailbox", *, plugin="counting", state=None, **config):
    row = Connection(
        owner=user,
        kind="sync",
        plugin=plugin,
        label=label,
        config={"url": "https://mail.example.org", "interval": "60", **config},
        sync_state=state or {},
    )
    row.secrets = {"password": "hunter2"}
    row.save()
    return row


def run(connection) -> SyncReport:
    """One run as *Sync now* makes it: the row read afresh, then synced."""
    return syncing.sync_connection(Connection.objects.get(pk=connection.pk))


def kept(connection) -> dict:
    return Connection.objects.get(pk=connection.pk).sync_state


def after_two_runs(user, **config):
    """A connection whose state says two runs have been counted."""
    connection = a_sync(user, **config)
    run(connection)
    run(connection)
    assert kept(connection) == {"last_uid": 2}
    Counting.started_from = []
    return Connection.objects.get(pk=connection.pk)


def the_form(connection, **changes) -> dict:
    """What the connection's form posts when nothing was touched, then ``changes``."""
    return {
        "label": connection.label,
        "enabled": "on",
        "plugin_url": connection.config["url"],
        "plugin_password": "",
        "plugin_interval": connection.config["interval"],
        **changes,
    }


# ------------------------------------------------------- kept from one run to the next


def test_a_sync_is_handed_at_its_next_run_the_state_it_returned(user):
    """A run had nowhere to say how far it got, so the next one began again from nothing (#1165)."""
    connection = a_sync(user)

    assert run(connection).error == ""
    assert run(connection).error == ""
    assert run(connection).error == ""

    assert [state for _label, state in Counting.started_from] == [
        {},
        {"last_uid": 1},
        {"last_uid": 2},
    ]
    assert kept(connection) == {"last_uid": 3}


def test_the_scheduler_keeps_the_state_as_sync_now_does(user):
    """The state is written wherever a report is, whoever started the run (#1165)."""
    connection = a_sync(user)

    assert syncing.run_syncs() == (1, 0)

    assert kept(connection) == {"last_uid": 1}


def test_two_connections_of_one_plugin_keep_a_state_each(user, other_user):
    """A state is one connection's: a second mailbox, or another person's, has its own (#1165)."""
    mine = a_sync(user, "Work")
    second = a_sync(user, "Home")
    theirs = a_sync(other_user, "Theirs")

    run(mine)
    run(mine)
    run(second)
    run(theirs)
    run(mine)

    assert Counting.started_from == [
        ("Work", {}),
        ("Work", {"last_uid": 1}),
        ("Home", {}),
        ("Theirs", {}),
        ("Work", {"last_uid": 2}),
    ]
    assert (kept(mine), kept(second), kept(theirs)) == (
        {"last_uid": 3},
        {"last_uid": 1},
        {"last_uid": 1},
    )


def test_returning_no_state_leaves_what_is_kept_and_an_empty_one_forgets_it(user):
    """``None`` is "nothing new to say", which is what a run that changed nothing returns;
    an empty dict is the plugin asking to start again (#1165)."""
    connection = after_two_runs(user)

    Counting.instead = lambda connection, before: SyncReport()
    assert run(connection).error == ""
    assert kept(connection) == {"last_uid": 2}

    Counting.instead = lambda connection, before: SyncReport(state={})
    assert run(connection).error == ""
    assert kept(connection) == {}


def test_what_a_run_is_handed_is_a_copy_and_what_json_makes_of_what_it_returned(user):
    """A plugin changing the dict it read changes nothing kept, and a tuple it returned comes
    back as the list the database holds, the same in the run after as in every later one (#1165)."""
    connection = a_sync(user)

    def scribble(connection, before):
        before["scribbled"] = True
        return SyncReport(state={"folders": ("INBOX", "Jobs"), "seen": {"INBOX": 7}})

    Counting.instead = scribble
    run(connection)
    run(connection)

    assert Counting.started_from[1][1] == {"folders": ["INBOX", "Jobs"], "seen": {"INBOX": 7}}
    assert sync_state(Connection.objects.get(pk=connection.pk)) == Counting.started_from[1][1]


# ----------------------------------------------------- only after a run that did not fail


def test_a_run_that_raises_keeps_the_state_of_the_last_good_one(user):
    """A pass that stopped half-way is done again, not skipped: it writes no state (#1165)."""
    connection = after_two_runs(user)

    def boom(connection, before):
        raise RuntimeError("no answer")

    Counting.instead = boom
    assert run(connection).error == "RuntimeError: no answer"

    assert kept(connection) == {"last_uid": 2}
    Counting.instead = None
    run(connection)
    assert Counting.started_from[-1] == ("Mailbox", {"last_uid": 2})


def test_a_run_that_reports_an_error_keeps_the_state_of_the_last_good_one(user):
    """A plugin that says its run failed is believed, whatever state came with it (#1165)."""
    connection = after_two_runs(user)

    Counting.instead = lambda connection, before: SyncReport(
        error="The server hung up.", state={"last_uid": 99}
    )
    assert run(connection).error == "The server hung up."

    assert kept(connection) == {"last_uid": 2}


def test_a_run_that_ends_its_connection_keeps_nothing_new(user):
    """`ConnectionUnusable` cuts the run short, and a run cut short writes no state (#1165)."""
    connection = after_two_runs(user)

    def finished(connection, before):
        raise ConnectionUnusable("That account was closed.", keep_secrets=True)

    Counting.instead = finished
    assert run(connection).error == "That account was closed."

    stored = Connection.objects.get(pk=connection.pk)
    assert stored.enabled is False
    assert stored.sync_state == {"last_uid": 2}, "the secrets were kept, and so was the place"


def test_a_run_that_outlived_its_turn_does_not_write_over_the_next_runs_state(user):
    """A run whose lease another run took no longer knows where things stand (#1165)."""
    connection = after_two_runs(user)
    key = syncing.lease_key(connection)

    def overtaken(connection, before):
        Lease.objects.filter(key=key).update(token="the-next-runs-token")
        return SyncReport(pulled=5, state={"last_uid": 50})

    Counting.instead = overtaken
    report = run(connection)

    assert report.error == "" and report.pulled == 5, "what it did is still reported"
    assert kept(connection) == {"last_uid": 2}


def test_a_run_whose_settings_changed_under_it_does_not_keep_its_state(user):
    """The person saved another folder while a run was reading the old one: what that run
    learnt is true of the old folder, and the next run must start with none (#1165)."""
    connection = after_two_runs(user)

    def edited_meanwhile(connection, before):
        other = Connection.objects.get(pk=connection.pk)
        other.config = {**other.config, "url": "https://elsewhere.example.org"}
        other.save()
        return SyncReport(state={"last_uid": 50})

    Counting.instead = edited_meanwhile
    assert run(connection).error == ""

    assert kept(connection) == {}


# -------------------------------------------- dropped when the connection is set up anew


@pytest.mark.parametrize(
    "changes",
    [
        {"plugin_url": "https://other.example.org"},
        {"plugin_password": "another-password"},
    ],
    ids=["a setting", "a secret"],
)
def test_saving_the_form_with_something_changed_drops_the_state(client, user, changes):
    """Another server, folder or account makes the kept place a place somewhere else (#1165)."""
    connection = after_two_runs(user)
    client.force_login(user)

    response = client.post(
        reverse("connections:edit", args=[connection.pk]), the_form(connection, **changes)
    )

    assert response.status_code == 302
    assert kept(connection) == {}
    run(connection)
    assert Counting.started_from == [("Mailbox", {})], "the run after the edit starts with none"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"label": "Renamed"},
        {"enabled": ""},
        {"plugin_password": "hunter2"},
        {"plugin_interval": "15"},
    ],
    ids=["nothing", "the name", "the switch", "the same password typed again", "the interval"],
)
def test_saving_the_form_with_no_setting_changed_keeps_the_state(client, user, changes):
    """A new name, the switch or running more often is not a new mailbox, and must not cost
    a whole reading (#1165)."""
    connection = after_two_runs(user)
    client.force_login(user)
    form = the_form(connection, **changes)
    if not form["enabled"]:
        del form["enabled"]

    response = client.post(reverse("connections:edit", args=[connection.pk]), form)

    assert response.status_code == 302
    stored = Connection.objects.get(pk=connection.pk)
    assert stored.label == changes.get("label", "Mailbox")
    assert stored.sync_state == {"last_uid": 2}


def test_any_save_that_changes_the_settings_or_the_secrets_drops_the_state(user):
    """The rule is on the row's own save, so a plugin's own code that saves its connection,
    or a path written later, cannot forget it (#1165)."""
    whole = after_two_runs(user)
    whole.config = {**whole.config, "folder": "Jobs"}
    whole.save()
    assert kept(whole) == {}

    named = after_two_runs(user, url="https://second.example.org")
    named.config["folder"] = "Jobs"
    named.save(update_fields=["config", "updated_at"])
    assert kept(named) == {}

    secret = after_two_runs(user, url="https://third.example.org")
    secret.secrets = {"password": "changed"}
    secret.save(update_fields=["secrets_encrypted", "updated_at"])
    assert kept(secret) == {}

    retired = after_two_runs(user, url="https://fourth.example.org")
    retired.retire("Revoked.")
    assert kept(retired) == {}, "its secrets went, and with them the account the place was in"


def test_what_is_not_a_setting_leaves_the_state_alone(user):
    """A report, a test, the switch, the scheduler's stamp, the wait after a failure, the
    same secrets encrypted again, and what *Look on the server* found: none is an edit (#1165)."""
    connection = after_two_runs(user)

    connection.record_test(False, "no answer")
    connection.record_test(True)
    connection.enabled = False
    connection.save(update_fields=["enabled", "updated_at"])
    connection.enabled = True
    connection.label = "Renamed"
    connection.save()
    assert syncing.claim_connection(connection)
    connection.refresh_from_db()
    connection.secrets = dict(connection.secrets)
    connection.save()
    specs = [FieldSpec("books", "Books", type="discovered", modes=(("read", "Read"),))]
    found = {"books": [DiscoveredOption("/books/1/", "Work")]}
    connection.config = discovery.record(connection.config, specs, found)
    connection.save(update_fields=["config", "updated_at"])
    assert kept(connection) == {"last_uid": 2}

    def boom(connection, before):
        raise RuntimeError("no answer")

    Counting.instead = boom
    run(connection)
    run(connection)
    stored = Connection.objects.get(pk=connection.pk)
    assert stored.last_error and syncing.wait_after_last_run(stored)
    assert stored.sync_state == {"last_uid": 2}


def a_reply(payload: dict):
    """One answer from a provider's token address."""
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = payload
    session = mock.MagicMock()
    session.__enter__.return_value.post.return_value = response
    return session


def an_agreed_sync(user) -> Connection:
    row = Connection(
        owner=user,
        kind="sync",
        plugin="agreeing",
        label="Mailbox",
        config={"client_id": "an-id", "interval": "60"},
        sync_state={"last_uid": 2},
    )
    row.secrets = {
        "client_secret": "a-secret",
        consent.ACCESS_TOKEN: "about-to-die",
        consent.REFRESH_TOKEN: "a-refresh",
        consent.EXPIRES_AT: time.time() + 5,
    }
    row.save()
    return row


def test_a_token_postulo_renews_by_itself_is_not_an_edit(user):
    """A consent token is renewed every hour or so, and written among the secrets: read as
    an edit, that would have had a sync start again from nothing after every renewal (#1165)."""
    connection = an_agreed_sync(user)
    reply = a_reply({"access_token": "a-new-one", "refresh_token": "rotated", "expires_in": 3600})

    with mock.patch("postulo.plugins.http.client", return_value=reply):
        assert consent.access_token(connection) == "a-new-one"

    stored = Connection.objects.get(pk=connection.pk)
    assert stored.secrets[consent.REFRESH_TOKEN] == "rotated"
    assert stored.sync_state == {"last_uid": 2}


def test_agreeing_again_at_the_provider_drops_the_state(rf, user):
    """Whoever agrees this time may be another account, and the place kept was in the last
    one's mailbox (#1165)."""
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.core import signing

    connection = an_agreed_sync(user)
    request = rf.get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = user
    state = signing.dumps(
        {
            "connection": connection.pk,
            "owner": user.pk,
            "nonce": consent.make_nonce(request, f"connection:{connection.pk}"),
        }
    )
    reply = a_reply({"access_token": "theirs", "refresh_token": "theirs-too", "expires_in": 3600})

    with mock.patch("postulo.plugins.http.client", return_value=reply):
        consent.finish(request, "a-code", state)

    assert kept(connection) == {}


# ------------------------------------------------- a sync that keeps nothing, as before


def test_a_sync_that_neither_reads_nor_returns_a_state_runs_as_it_always_did(user):
    """Every sync written before this: one statement writes its report, none reads the row
    again, nothing is kept, and the report holds what it held (#1165)."""
    connection = a_sync(user, plugin="stateless")
    fresh = Connection.objects.get(pk=connection.pk)

    with CaptureQueriesContext(database) as queries:
        report = syncing.sync_connection(fresh)

    on_the_row = [q["sql"] for q in queries if "plugins_connection" in q["sql"]]
    assert len(on_the_row) == 1 and on_the_row[0].startswith("UPDATE"), on_the_row
    assert "sync_state" not in on_the_row[0]
    assert report.state is None and report.summary() == "2 pushed · all quiet"
    stored = Connection.objects.get(pk=connection.pk)
    assert stored.sync_state == {}
    assert stored.last_report == {
        "pushed": 2,
        "pulled": 0,
        "removed": 0,
        "skipped": 0,
        "notes": ["all quiet"],
    }
    assert stored.last_error == "" and stored.last_ok_at is not None


def test_the_state_is_no_part_of_the_report_a_person_reads(user):
    """The report is drawn on the connection; the state is the plugin's own note (#1165)."""
    report = SyncReport(pulled=1, state={"uidvalidity": 1700000000})

    assert "state" not in report.record()
    assert "1700000000" not in report.summary()
    assert SyncReport(1, 2, 3, 4, ["a note"], "", False).state is None, "the old order still reads"


# ------------------------------------------------------- bounded, and nothing but JSON


def test_a_state_too_large_fails_the_run_and_keeps_the_last_one(client, user):
    """A few kilobytes, so the field cannot become a store. A plugin that returns more gets
    a failed run it is told about: refusing in silence would leave it reading everything
    again for ever with nobody the wiser (#1165)."""
    connection = after_two_runs(user)
    too_much = {"seen": "x" * MAX_SYNC_STATE_BYTES}

    Counting.instead = lambda connection, before: SyncReport(pulled=3, state=too_much)
    report = run(connection)

    assert "counting" in report.error and "too much" in report.error
    stored = Connection.objects.get(pk=connection.pk)
    assert stored.sync_state == {"last_uid": 2}
    assert stored.last_error == report.error
    assert stored.last_report["pulled"] == 3, "what the run did is still counted"
    client.force_login(user)
    said = "The counting plugin tried to keep too much between runs."
    assert said in client.get(reverse("connections:list")).content.decode(), "on its card"


def test_a_state_of_exactly_the_limit_is_kept(user):
    """The limit is on the state as compact JSON in UTF-8, and it is inclusive (#1165)."""
    connection = a_sync(user)
    padding = MAX_SYNC_STATE_BYTES - len(json.dumps({"s": ""}, separators=(",", ":")))
    fits = {"s": "é" * (padding // 2) + "x" * (padding % 2)}
    assert len(json.dumps(fits, ensure_ascii=False, separators=(",", ":")).encode()) == (
        MAX_SYNC_STATE_BYTES
    )

    Counting.instead = lambda connection, before: SyncReport(state=fits)
    assert run(connection).error == ""
    assert kept(connection) == fits

    over = {"s": fits["s"] + "x"}
    Counting.instead = lambda connection, before: SyncReport(state=over)
    assert run(connection).error
    assert kept(connection) == fits


@pytest.mark.parametrize(
    "state",
    [
        {"seen": {1, 2, 3}},
        {"when": object()},
        {"ratio": float("nan")},
        {"token": "a\x00b"},
        {("INBOX", 1): 7},
        ["not", "a", "dict"],
        "not a dict either",
    ],
    ids=["a set", "an object", "not a number", "a NUL", "a tuple as a key", "a list", "a string"],
)
def test_a_state_that_is_not_json_fails_that_run_and_no_other(user, state):
    """What does not serialise is the plugin's mistake: its run fails with a sentence, the
    last state stays, and the connections behind it in the pass still run (#1165)."""
    broken = a_sync(user, "Broken", state={"last_uid": 2})
    a_sync(user, "Fine", url="https://fine.example.org")

    def sometimes(connection, before):
        if connection.label == "Broken":
            return SyncReport(state=state)
        return SyncReport(state={"last_uid": 1})

    Counting.instead = sometimes
    assert syncing.run_syncs() == (2, 1)

    assert [label for label, _state in Counting.started_from] == ["Broken", "Fine"]
    stored = Connection.objects.get(pk=broken.pk)
    assert "counting" in stored.last_error and "cannot be stored" in stored.last_error
    assert stored.sync_state == {"last_uid": 2}
    assert Connection.objects.get(label="Fine").sync_state == {"last_uid": 1}


# --------------------------------------------------- the plugin's own note, shown nowhere


SERVER_SIDE = "uidvalidity-1700000000"


def test_the_state_is_shown_on_no_page_and_offered_by_no_form(client, user):
    """It can hold a server's identifiers, and it is nothing the person typed (#1165)."""
    from postulo.plugins.forms import ConnectionForm

    connection = a_sync(user, state={"marker": SERVER_SIDE})
    client.force_login(user)

    for address in (
        reverse("connections:list"),
        reverse("connections:edit", args=[connection.pk]),
        reverse("connections:delete", args=[connection.pk]),
    ):
        assert SERVER_SIDE not in client.get(address).content.decode(), address
    assert not Connection._meta.get_field("sync_state").editable
    assert "sync_state" not in ConnectionForm(Counting(), instance=connection).fields
    assert kept(connection) == {"marker": SERVER_SIDE}, "and posting the form cannot set it"
    client.post(
        reverse("connections:edit", args=[connection.pk]),
        the_form(connection, sync_state='{"marker": "typed"}'),
    )
    assert kept(connection) == {"marker": SERVER_SIDE}


def test_the_state_is_in_nobodys_export_and_goes_with_its_connection(user):
    """Connections are not in the personal archive, and the state must not be the first
    thing of theirs to get there; removing the connection removes it (#1165)."""
    from postulo.core.export import build_document

    connection = a_sync(user, state={"marker": SERVER_SIDE})

    assert SERVER_SIDE not in json.dumps(build_document(user), default=str)
    connection.delete()
    assert not Connection.objects.filter(sync_state__marker=SERVER_SIDE, owner=user).exists()


# ------------------------------------------------------------- the published helpers


def test_a_plugins_own_tests_can_run_a_sync_and_read_what_it_kept(user):
    """`postulo.plugins.testing` runs a connection as Postulo does and reads the state back,
    so a plugin's suite need not know which column holds it (#1165)."""
    from postulo.plugins.testing import kept_sync_state, run_sync

    connection = a_sync(user)

    assert kept_sync_state(connection) == {}
    assert run_sync(connection).pulled == 1
    assert kept_sync_state(connection) == {"last_uid": 1}
    assert run_sync(connection, state={"last_uid": 40}).error == ""
    assert Counting.started_from[-1] == ("Mailbox", {"last_uid": 40})
    assert kept_sync_state(connection) == {"last_uid": 41}
    with contextlib.suppress(KeyError):
        kept_sync_state(connection)["last_uid"] = 0
    assert kept_sync_state(connection) == {"last_uid": 41}, "a copy, not the row's own dict"
