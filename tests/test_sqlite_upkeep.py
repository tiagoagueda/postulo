"""A SQLite file is looked after: its statistics, and the room deleted rows leave (#1128).

Nothing ever ran `PRAGMA optimize`, so the query planner chose its indexes with no
statistics at all, and nothing could give back the pages that deleted rows leave inside the
file, which only ever grew and which every backup copied whole.

The statistics are counted on the suite's own database, where the scheduler's pass and the
hourly tidy run. What needs a file (free pages, a `VACUUM`, a lock held by somebody else)
builds a scratch one and puts it in the place of the suite's database for the test.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from postulo.config import sqlite as sqlite_options
from postulo.core import slow, sqlite_upkeep
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

Usage = namedtuple("Usage", "total used free")


def optimizes(captured) -> int:
    """How many of the statements a block issued were the refresh of the statistics."""
    return sum(query["sql"].startswith("PRAGMA optimize") for query in captured)


def on_sqlite() -> None:
    if connection.vendor != "sqlite":
        pytest.skip("the suite's own database is asked, and here it is not SQLite")


def as_postgresql(monkeypatch) -> None:
    """The module sees the other engine; the suite's database is still there to be counted."""
    monkeypatch.setattr(sqlite_upkeep, "connection", SimpleNamespace(vendor="postgresql"))


@pytest.fixture(autouse=True)
def the_connection_kept(monkeypatch):
    """The scheduler command closes old connections when a pass ends, and a test's
    connection is old at once (see `tests/test_cache_purge.py`)."""
    monkeypatch.setattr(
        "postulo.notifications.management.commands.send_due_reminders.close_old_connections",
        lambda: None,
    )


def open_as_an_instance_does(name: str, **options) -> DatabaseWrapper:
    database = sqlite_options.apply_options(
        {
            **connections["default"].settings_dict,
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name,
            "OPTIONS": dict(options),
        }
    )
    return DatabaseWrapper(database, "default")


@pytest.fixture
def scratch(tmp_path):
    """A SQLite file of its own, opened with the options an instance opens one with, and
    standing where the suite's database stands until the test is over."""
    original = connections["default"]
    wrapper = open_as_an_instance_does(str(tmp_path / "postulo.sqlite3"))
    connections["default"] = wrapper
    try:
        yield wrapper
    finally:
        wrapper.close()
        connections["default"] = original


def leave_free_pages(wrapper, megabytes: int = 2) -> None:
    """Rows written and then deleted, which is what a purge or a reaping leaves behind."""
    with wrapper.cursor() as cursor:
        cursor.execute("CREATE TABLE kept (id INTEGER PRIMARY KEY, body BLOB)")
        cursor.execute("CREATE TABLE gone (id INTEGER PRIMARY KEY, body BLOB)")
        cursor.execute("INSERT INTO kept (body) VALUES (randomblob(100000))")
        for _ in range(megabytes * 10):
            cursor.execute("INSERT INTO gone (body) VALUES (randomblob(100000))")
        cursor.execute("DELETE FROM gone")


def pragma(wrapper, name: str) -> int:
    with wrapper.cursor() as cursor:
        cursor.execute(f"PRAGMA {name}")
        return cursor.fetchone()[0]


# ------------------------------------------------------- the planner's statistics


def test_a_pass_refreshes_the_statistics_once_a_day_however_many_passes():
    """Nothing ran `PRAGMA optimize`, so `sqlite_stat1` was whatever a migration left. The
    scheduler's pass runs it now, and the passes that follow within the day do not."""
    on_sqlite()
    with CaptureQueriesContext(connection) as captured:
        for _ in range(3):
            call_command("send_due_reminders")
    assert optimizes(captured) == 1

    cache.delete(sqlite_upkeep.GATE)
    with CaptureQueriesContext(connection) as captured:
        call_command("send_due_reminders")
    assert optimizes(captured) == 1, "and again once the day has gone by"


def test_the_tidy_up_that_pages_ask_for_refreshes_them_too_and_not_every_hour():
    """An instance without a scheduler is covered by the tidy a request does. That one comes
    round every hour; the statistics keep their own day."""
    on_sqlite()
    with CaptureQueriesContext(connection) as captured:
        slow.tidy_up_if_due()
    assert optimizes(captured) == 1

    cache.delete("core:tidied-up")
    with CaptureQueriesContext(connection) as captured:
        slow.tidy_up_if_due()
    assert optimizes(captured) == 0


def test_on_postgresql_nothing_is_run_and_no_gate_is_taken(monkeypatch):
    """PostgreSQL keeps its own statistics. Pointed at one, a pass and a tidy issue no such
    statement and write no key."""
    as_postgresql(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        call_command("send_due_reminders")
        slow.tidy_up_if_due()
        assert sqlite_upkeep.optimize_if_due() is False
        assert sqlite_upkeep.optimize() is False
    assert optimizes(captured) == 0
    assert cache.get(sqlite_upkeep.GATE) is None


def test_the_statistics_are_written_for_a_table_this_connection_never_asked_about(scratch):
    """The connection that runs it has lived for one pass or one request, so every table is
    looked at, not only the ones it happened to query."""
    with scratch.cursor() as cursor:
        cursor.execute("CREATE TABLE listing (id INTEGER PRIMARY KEY, owner INT)")
        cursor.execute("CREATE INDEX listing_owner ON listing (owner)")
        cursor.executemany("INSERT INTO listing (owner) VALUES (%s)", [(n % 4,) for n in range(40)])
    scratch.close()

    assert sqlite_upkeep.optimize() is True

    with scratch.cursor() as cursor:
        cursor.execute("SELECT idx, stat FROM sqlite_stat1 WHERE tbl = 'listing'")
        assert cursor.fetchall() == [("listing_owner", "40 10")]


def test_a_database_somebody_else_is_writing_to_does_not_fail_the_pass(tmp_path, caplog):
    """Run outside a transaction it waits for the write lock like any write. When the wait
    runs out that is one refresh missed, said in the log, and not an error for the pass."""
    path = str(tmp_path / "busy.sqlite3")
    original = connections["default"]
    wrapper = open_as_an_instance_does(path, timeout=0.1)
    connections["default"] = wrapper
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        with wrapper.cursor() as cursor:
            cursor.execute("CREATE TABLE listing (id INTEGER PRIMARY KEY, owner INT)")
            cursor.execute("CREATE INDEX listing_owner ON listing (owner)")
            cursor.execute("INSERT INTO listing (owner) VALUES (1), (2), (3)")
        writer.execute("BEGIN IMMEDIATE")

        assert sqlite_upkeep.optimize() is False

        assert "statistics" in caplog.text
    finally:
        writer.close()
        wrapper.close()
        connections["default"] = original


# ------------------------------------------------------------- compact_database


def test_compacting_gives_back_the_room_deleted_rows_left_and_says_how_much(scratch, capsys):
    """Deleted rows leave their pages inside the file, and nothing ever gave them back: the
    file stayed at its largest. The command says how much is free, then shrinks the file."""
    leave_free_pages(scratch)
    before = sqlite_upkeep.space()
    assert before.free > 2_000_000
    assert before.free == pragma(scratch, "freelist_count") * pragma(scratch, "page_size")

    call_command("compact_database")

    said = capsys.readouterr().out
    assert f"would give back {sqlite_upkeep.in_words(before.free)}" in said
    assert sqlite_upkeep.in_words(before.size) in said
    after = sqlite_upkeep.space()
    assert after.free == 0
    assert after.size <= before.size - before.free
    assert f"now {sqlite_upkeep.in_words(after.size)}" in said
    # On the disk, and at once: the file is the smaller size and the log beside it is empty.
    file = Path(scratch.settings_dict["NAME"])
    assert file.stat().st_size == after.size
    assert file.with_name(f"{file.name}-wal").stat().st_size == 0
    with scratch.cursor() as cursor:
        cursor.execute("SELECT count(*), sum(length(body)) FROM kept")
        assert cursor.fetchone() == (1, 100000), "what was kept is still there"


def test_a_dry_run_says_the_figure_and_changes_nothing(scratch, capsys):
    leave_free_pages(scratch)
    before = sqlite_upkeep.space()

    with CaptureQueriesContext(scratch) as captured:
        call_command("compact_database", "--dry-run")

    said = capsys.readouterr().out
    assert f"would give back {sqlite_upkeep.in_words(before.free)}" in said
    assert "Nothing was changed" in said
    assert not any("VACUUM" in query["sql"] for query in captured)
    assert sqlite_upkeep.space() == before


def test_it_refuses_when_the_disk_has_no_room_for_the_copy(scratch, capsys, monkeypatch):
    """`VACUUM` writes the pages in use again before the file shrinks. With less free disk
    than that, it would fail half way through its run; the command says so first instead."""
    leave_free_pages(scratch)
    before = sqlite_upkeep.space()
    asked = []

    def nearly_full(directory):
        asked.append(str(directory))
        return Usage(total=10**9, used=10**9 - 4096, free=4096)

    monkeypatch.setattr(shutil, "disk_usage", nearly_full)

    with CaptureQueriesContext(scratch) as captured:
        with pytest.raises(CommandError, match="Not enough free disk"):
            call_command("compact_database")

    assert not any("VACUUM" in query["sql"] for query in captured)
    assert sqlite_upkeep.space() == before
    assert str(Path(scratch.settings_dict["NAME"]).parent) in asked, "the database's own directory"


def test_one_disk_has_to_hold_both_of_the_copies(scratch, monkeypatch):
    """The pages in use are written twice, to a temporary file and to the log beside the
    database. Where the two are on one disk, room for one of them is not room for the run."""
    leave_free_pages(scratch)
    used = sqlite_upkeep.space().used
    file = Path(scratch.settings_dict["NAME"])
    monkeypatch.setattr(sqlite_upkeep, "temporary_directory", lambda: file.parent)

    monkeypatch.setattr(shutil, "disk_usage", lambda where: Usage(10**9, 0, used * 3 // 2))
    with pytest.raises(sqlite_upkeep.CannotCompact, match="Not enough free disk"):
        sqlite_upkeep.check_room(file, used)

    monkeypatch.setattr(shutil, "disk_usage", lambda where: Usage(10**9, 0, used * 3))
    sqlite_upkeep.check_room(file, used)


def test_with_nothing_to_give_back_it_does_not_rewrite_the_file(scratch, capsys):
    with scratch.cursor() as cursor:
        cursor.execute("CREATE TABLE kept (id INTEGER PRIMARY KEY, body BLOB)")
    with CaptureQueriesContext(scratch) as captured:
        call_command("compact_database")
    assert "nothing to give back" in capsys.readouterr().out
    assert not any("VACUUM" in query["sql"] for query in captured)


def test_a_database_in_memory_has_nothing_to_compact(capsys):
    """The suite's own database, and anybody's who runs one that way: no file, no error."""
    original = connections["default"]
    wrapper = open_as_an_instance_does(":memory:")
    connections["default"] = wrapper
    try:
        call_command("compact_database")
    finally:
        wrapper.close()
        connections["default"] = original
    assert "in memory" in capsys.readouterr().out


def test_on_postgresql_the_command_says_it_does_nothing_there(monkeypatch, capsys):
    as_postgresql(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        call_command("compact_database")
    said = capsys.readouterr().out
    assert "PostgreSQL" in said and "does nothing here" in said
    assert not captured.captured_queries


def test_compacting_is_refused_inside_a_transaction(scratch):
    """`VACUUM` cannot run in one. A caller inside `atomic()` is told, not left with
    SQLite's own words for it."""
    from django.db import transaction

    leave_free_pages(scratch)
    with transaction.atomic():
        with pytest.raises(sqlite_upkeep.CannotCompact, match="inside a transaction"):
            sqlite_upkeep.compact()


# ----------------------------------------------------------- the staff overview


@pytest.fixture
def admin(db, django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        password="a-fairly-long-password-42",
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


def test_the_free_figure_is_the_free_pages_and_costs_two_statements(scratch):
    leave_free_pages(scratch)
    with CaptureQueriesContext(scratch) as captured:
        figure = sqlite_upkeep.reclaimable()
    assert figure == pragma(scratch, "freelist_count") * pragma(scratch, "page_size") > 2_000_000
    assert [query["sql"] for query in captured] == ["PRAGMA freelist_count", "PRAGMA page_size"]


def test_the_overview_says_how_much_compacting_would_give_back(client, admin, monkeypatch):
    """An administrator had no way to know the file held room to give back, or how much."""
    on_sqlite()
    monkeypatch.setattr(sqlite_upkeep, "reclaimable", lambda: 3 * 1024 * 1024)
    signed_in_recently(client, admin)

    html = client.get(reverse("server:overview")).content.decode()

    assert "data-database-free" in html
    row = html.split("data-database-free", 1)[1].split("</div>", 1)[0]
    assert "3.0\xa0MB" in row
    assert "manage.py compact_database" in row


def test_the_overview_asks_the_database_itself(client, admin):
    on_sqlite()
    signed_in_recently(client, admin)
    with CaptureQueriesContext(connection) as captured:
        html = client.get(reverse("server:overview")).content.decode()
    asked = [query["sql"] for query in captured if query["sql"].startswith("PRAGMA")]
    assert asked == ["PRAGMA freelist_count", "PRAGMA page_size"]
    assert "data-database-free" in html


def test_on_postgresql_the_overview_has_no_such_row(client, admin, monkeypatch):
    as_postgresql(monkeypatch)
    signed_in_recently(client, admin)

    html = client.get(reverse("server:overview")).content.decode()

    assert "data-database-free" not in html
    assert "compact_database" not in html
