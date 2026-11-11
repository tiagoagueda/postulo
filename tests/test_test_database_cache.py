"""The migrated test database is kept between runs, and never trusted over a migration (#1144).

Every run migrated a few hundred migrations into an empty in-memory database before its
first test: about a minute of a one-file run on a contributor's machine, and the same in
every plugin repository's suite. `postulo.plugins.testdb` keeps the migrated database in a
file named by what the migrations are and restores it into the next run's database;
`postulo.plugins.testing.cached_django_db_setup` is the fixture that does it, here and in a
plugin's own suite.

A stale copy is the danger, so most of this file is about the name and about the file being
whole. Everything here works on small databases of its own: the real one takes that minute.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from postulo.plugins import testdb, testing

SCHEMA = """
CREATE TABLE django_migrations (id integer PRIMARY KEY AUTOINCREMENT, app text, name text);
CREATE TABLE word (id integer PRIMARY KEY AUTOINCREMENT, text text NOT NULL UNIQUE);
CREATE INDEX word_text ON word (text);
INSERT INTO django_migrations (app, name) VALUES ('words', '0001_initial');
INSERT INTO word (text) VALUES ('kept'), ('between'), ('runs');
DELETE FROM word WHERE text = 'between';
"""


def contents(database: sqlite3.Connection) -> list[str]:
    """A database as the statements that would rebuild it: schema, rows and sequences."""
    return list(database.iterdump())


@pytest.fixture
def memory():
    """Empty in-memory databases, named and shared the way Django names a test database."""
    opened = []

    def make(name: str) -> sqlite3.Connection:
        database = sqlite3.connect(
            f"file:memorydb_{name}_1144?mode=memory&cache=shared",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        opened.append(database)
        return database

    yield make
    for database in opened:
        database.close()


def migrated(database: sqlite3.Connection) -> sqlite3.Connection:
    database.executescript(SCHEMA)
    return database


# ------------------------------------------------------------------------------- the name
def test_the_name_follows_what_a_migration_says_and_not_when_it_was_saved(tmp_path):
    """A checkout, a rebase or a touch gives every file a new date and no new content."""
    migration = tmp_path / "0001_initial.py"
    migration.write_text("operations = []\n", encoding="utf-8")
    files = [("app.migrations/0001_initial.py", migration)]
    before = testdb.digest(files)

    os.utime(migration, (1, 1))
    assert testdb.digest(files) == before, "a date is not a change"

    migration.write_text("operations = ['one']\n", encoding="utf-8")
    assert testdb.digest(files) != before, "an edited migration is another database"


def test_the_name_is_the_same_in_another_checkout_and_another_for_another_app(tmp_path):
    """Two worktrees with the same migrations share one database: the path is not in the name."""
    here, there = tmp_path / "here", tmp_path / "there"
    for directory in (here, there):
        directory.mkdir()
        (directory / "0001_initial.py").write_text("operations = []\n", encoding="utf-8")
    label = "app.migrations/0001_initial.py"

    assert testdb.digest([(label, here / "0001_initial.py")]) == testdb.digest(
        [(label, there / "0001_initial.py")]
    )
    assert testdb.digest([(label, here / "0001_initial.py")]) != testdb.digest(
        [("other.migrations/0001_initial.py", here / "0001_initial.py")]
    )


def test_a_fact_and_a_file_that_has_gone_are_part_of_the_name(tmp_path):
    present = tmp_path / "0001_initial.py"
    present.write_bytes(b"")
    files = [("app.migrations/0001_initial.py", present)]
    empty = testdb.digest(files, ["django 6.1"])

    assert testdb.digest(files, ["django 6.2"]) != empty
    present.unlink()
    assert testdb.digest(files, ["django 6.1"]) != empty, "missing is not the same as empty"


def test_the_settings_a_migration_reads_are_found_by_name():
    """`plugins/0004` seeds a row from `POSTULO_PLUGIN_CATALOGUES`: a setting, not a file."""
    source = (
        "raw = getattr(settings, 'POSTULO_PLUGIN_CATALOGUES', '')\n"
        "swappable = settings.AUTH_USER_MODEL\n"
        'for config in settings.CACHES.values(): ...\nother = getattr(settings, "X_2")\n'
    )

    assert testdb.settings_named(source) == {
        "POSTULO_PLUGIN_CATALOGUES",
        "AUTH_USER_MODEL",
        "CACHES",
        "X_2",
    }


def test_what_a_migration_imports_is_found_wherever_the_import_is_written():
    """Most data migrations import the code they run inside the function that runs it."""
    source = (
        "import re\n"
        "import django.db.models.deletion\n"
        "from django.db import migrations\n"
        "import postulo.core.language_field\n"
        "from ..seeds import rows\n"
        "def forwards(apps, schema_editor):\n"
        "    from postulo.core import slugs\n"
        "    from postulo.jobs.models import split_location\n"
    )

    assert testdb.modules_imported(source, "postulo_x.migrations") == {
        "postulo.core.language_field",
        "postulo_x.seeds",
        "postulo_x.seeds.rows",
        "postulo.core",
        "postulo.core.slugs",
        "postulo.jobs.models",
        "postulo.jobs.models.split_location",
    }
    assert testdb.modules_imported("from django.db import migrations\n", "x.migrations") == set()


def test_every_installed_apps_migrations_are_listed():
    """Postulo's, Django's, a library's and the test app's: whatever is installed."""
    files, without = testdb.migration_files()
    labels = {label for label, _path in files}

    assert "postulo.jobs.migrations/0001_initial.py" in labels
    assert "postulo.plugins.migrations/0004_seed_plugin_repositories.py" in labels
    assert "django.contrib.auth.migrations/0001_initial.py" in labels
    assert "tests.testapp.migrations/0001_initial.py" in labels
    assert "staticfiles" in without and "jobs" not in without

    jobs = Path(testdb.__file__).parents[1] / "jobs" / "migrations"
    listed = {path.name for label, path in files if label.startswith("postulo.jobs.migrations/")}
    assert listed == {path.name for path in jobs.glob("*.py")}, "every file, none skipped"


@pytest.fixture
def one_app(tmp_path, monkeypatch, settings):
    """An app of one migration, which reads a setting and imports a module to seed its rows."""
    migration = tmp_path / "0001_seed.py"
    migration.write_text(
        "from django.conf import settings\n"
        "def seed(apps, schema_editor):\n"
        "    from seeds_1144 import rows\n"
        "    return rows, settings.POSTULO_SEED_1144\n",
        encoding="utf-8",
    )
    seeds = tmp_path / "seeds_1144.py"
    seeds.write_text("rows = ['one']\n", encoding="utf-8")
    data = tmp_path / "regions.json"
    data.write_text("[]", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        testdb, "migration_files", lambda: ([("one.migrations/0001_seed.py", migration)], [])
    )
    settings.POSTULO_SEED_1144 = "a"
    connection = SimpleNamespace(
        alias="default",
        vendor="sqlite",
        settings_dict={"ENGINE": "django.db.backends.sqlite3", "OPTIONS": {}, "TEST": {}},
    )
    yield SimpleNamespace(migration=migration, seeds=seeds, data=data, connection=connection)
    sys.modules.pop("seeds_1144", None)


def test_the_name_changes_with_everything_the_migration_reads(one_app, settings, monkeypatch):
    """A stale copy would pass tests against a database today's migrations no longer build.

    So the name moves with the migration, with the module it imports to seed its rows, with
    the setting it reads, with a file its author named, and with the versions underneath.
    """
    import django

    def name():
        return testdb.fingerprint(one_app.connection, also=[one_app.data])

    names = [name()]
    assert name() == names[0], "the same twice, or nothing would ever be found again"

    one_app.migration.write_text(
        one_app.migration.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    names.append(name())
    one_app.seeds.write_text("rows = ['one', 'two']\n", encoding="utf-8")
    names.append(name())
    settings.POSTULO_SEED_1144 = "b"
    names.append(name())
    one_app.data.write_text('["north"]', encoding="utf-8")
    names.append(name())
    monkeypatch.setattr(django, "get_version", lambda: "99.0")
    names.append(name())
    monkeypatch.setattr(sqlite3, "sqlite_version", "99.0.0")
    names.append(name())
    one_app.connection.settings_dict["ENGINE"] = "django.db.backends.postgresql"
    names.append(name())

    assert len(set(names)) == len(names), "each of those is another database"


def test_a_changed_migration_is_migrated_and_going_back_finds_the_first_copy(
    one_app, memory, tmp_path
):
    """The next run after an edit sees it: another name, nothing kept under it, a migration."""
    ran = []
    first = one_app.migration.read_text(encoding="utf-8")

    def run(name):
        database = memory(name)
        entry = tmp_path / f"{testdb.fingerprint(one_app.connection)}.sqlite3"
        return testdb.migrate_or_restore(
            lambda: database, lambda: ran.append(migrated(database)), entry
        )

    assert [run("one"), run("two")] == ["migrated", "restored"]

    one_app.migration.write_text(first + "# another operation\n", encoding="utf-8")
    assert [run("three"), run("four")] == ["migrated", "restored"]
    assert len(ran) == 2

    one_app.migration.write_text(first, encoding="utf-8")
    assert run("five") == "restored", "back on the branch it was made for"
    assert len(list(tmp_path.glob("*.sqlite3"))) == 2


def test_postulo_has_no_receiver_the_name_cannot_see():
    """A `post_migrate` receiver writes to the database on every migrate and is no migration,
    so its code is not in the name and a change to it would be restored over. There is none
    today; the day there is one, its file goes into `also` in `tests/conftest.py`, and here.

    A library's receivers are covered: the library's version is in the name."""
    import weakref

    from django.db.models.signals import post_migrate, pre_migrate

    modules = set()
    for signal in (pre_migrate, post_migrate):
        for entry in signal.receivers:
            receiver = entry[1]
            if isinstance(receiver, weakref.ReferenceType):
                receiver = receiver()
            modules.add(getattr(receiver, "__module__", ""))

    assert "django.contrib.auth.management" in modules, "the receivers were read"
    assert sorted(name for name in modules if name.startswith(("postulo", "tests"))) == []


def test_an_installed_distribution_is_named_with_its_version(tmp_path):
    (tmp_path / "Django-6.1.3.dist-info").mkdir()
    (tmp_path / "django").mkdir()

    assert testdb.distributions([str(tmp_path), str(tmp_path / "nowhere")]) == [
        "Django-6.1.3.dist-info"
    ]
    assert any(name.lower().startswith("django-") for name in testdb.distributions())


# ------------------------------------------------------------------- store and restore
def test_a_restored_database_is_the_one_that_was_stored(memory, tmp_path):
    """Schema, rows, indexes and the sequences: the next row gets the id a migration left."""
    source = migrated(memory("source"))
    entry = tmp_path / "name.sqlite3"

    assert testdb.store(source, entry)
    assert testdb.is_whole(entry)
    assert [path.name for path in tmp_path.iterdir()] == ["name.sqlite3"], "no temporary left"

    restored = memory("restored")
    assert testdb.restore(entry, restored)
    assert contents(restored) == contents(source)
    assert restored.execute("SELECT seq FROM sqlite_sequence WHERE name = 'word'").fetchone() == (
        3,
    )
    restored.execute("INSERT INTO word (text) VALUES ('again')")
    assert restored.execute("SELECT id FROM word WHERE text = 'again'").fetchone() == (4,)
    assert contents(source) != contents(restored), "a copy, not the same database"


def test_a_database_with_something_in_it_is_never_overwritten(memory, tmp_path):
    entry = tmp_path / "name.sqlite3"
    testdb.store(migrated(memory("source")), entry)
    occupied = memory("occupied")
    occupied.execute("CREATE TABLE mine (id integer)")

    assert not testdb.restore(entry, occupied)
    assert [name for (name,) in occupied.execute("SELECT name FROM sqlite_master")] == ["mine"]


def test_a_file_that_is_not_whole_is_never_used(memory, tmp_path):
    """Cut short, empty, or not a database at all: the run migrates, and the file is replaced."""
    whole = tmp_path / "whole.sqlite3"
    testdb.store(migrated(memory("source")), whole)
    data = whole.read_bytes()
    entry = tmp_path / "name.sqlite3"

    for number, broken in enumerate((data[: len(data) // 2], b"", b"x" * len(data), data[:100])):
        entry.write_bytes(broken)
        empty = memory(f"empty{number}")
        assert not testdb.is_whole(entry)
        assert not testdb.restore(entry, empty)
        assert testdb.is_empty(empty)

    ran = []
    target = memory("target")
    outcome = testdb.migrate_or_restore(lambda: target, lambda: ran.append(migrated(target)), entry)
    assert (outcome, len(ran)) == ("migrated", 1)
    assert testdb.is_whole(entry), "and the next run finds a good one"


def test_a_run_killed_while_writing_leaves_nothing_a_later_run_trusts(
    memory, tmp_path, monkeypatch
):
    """The file is written under another name and renamed: half of one never has the name."""
    entry = tmp_path / "name.sqlite3"
    source = migrated(memory("source"))
    seen = []

    def killed(temporary, final):
        seen.append((Path(temporary).name, Path(temporary).exists(), Path(final).exists()))
        raise OSError("killed")

    with monkeypatch.context() as patch:
        patch.setattr(testdb.os, "replace", killed)
        assert not testdb.store(source, entry)

    (name, written, named), *_ = seen
    assert name.endswith(".tmp") and written and not named
    assert not entry.exists()

    # What a kill would have left: a whole database under a temporary name.
    testdb.store(source, entry)
    entry.rename(tmp_path / "name.123-abcd.tmp")
    ran = []
    target = memory("target")
    assert (
        testdb.migrate_or_restore(lambda: target, lambda: ran.append(migrated(target)), entry)
        == "migrated"
    )
    assert len(ran) == 1, "the temporary file was not taken for the database"


def test_the_second_run_restores_and_does_not_migrate(memory, tmp_path):
    """The saving itself: one migration for any number of runs with the same migrations."""
    entry = tmp_path / "cache" / "name.sqlite3"
    migrations = []

    def run(name, **options):
        database = memory(name)
        outcome = testdb.migrate_or_restore(
            lambda: database, lambda: migrations.append(migrated(database)), entry, **options
        )
        return outcome, database

    first, built = run("first")
    second, restored = run("second")
    third, again = run("third")

    assert (first, second, third) == ("migrated", "restored", "restored")
    assert len(migrations) == 1
    assert contents(restored) == contents(built) == contents(again)

    afresh, _database = run("afresh", afresh=True)
    assert afresh == "migrated" and len(migrations) == 2, "--create-db migrates whatever is kept"


def test_a_directory_that_cannot_be_made_costs_nothing_but_the_migration(memory, tmp_path):
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    database = memory("blocked")
    ran = []

    outcome = testdb.migrate_or_restore(
        lambda: database, lambda: ran.append(migrated(database)), blocked / "name.sqlite3"
    )

    assert (outcome, len(ran)) == ("migrated", 1)


def test_workers_that_start_together_migrate_once(memory, tmp_path):
    """`pytest -n 4` with nothing kept: one worker migrates, the others wait and restore.

    Each of them has an in-memory database of its own, as each xdist worker's process has.
    """
    entry = tmp_path / "name.sqlite3"
    together = threading.Barrier(4)
    counting = threading.Lock()
    migrations, outcomes, databases = [], [], []

    def worker(number):
        database = memory(f"worker{number}")
        databases.append(database)

        def migrate():
            with counting:
                migrations.append(number)
            time.sleep(0.2)
            migrated(database)

        together.wait()
        outcomes.append(testdb.migrate_or_restore(lambda: database, migrate, entry))

    threads = [threading.Thread(target=worker, args=(number,)) for number in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert len(migrations) == 1
    assert sorted(outcomes) == ["migrated", "restored", "restored", "restored"]
    assert all(contents(database) == contents(databases[0]) for database in databases)
    assert testdb.is_whole(entry)


def test_the_lock_is_let_go_of_and_never_waited_on_for_ever(tmp_path):
    path = tmp_path / "name.lock"

    with testdb.locked(path) as held:
        assert held
        with testdb.locked(path, wait=0) as second:
            assert not second, "held by somebody else: go on without it"
    with testdb.locked(path, wait=0) as after:
        assert after

    with testdb.locked(tmp_path / "no-such-directory" / "name.lock", wait=0) as nowhere:
        assert not nowhere


def test_a_run_asked_to_start_afresh_waits_for_nobody(memory, tmp_path, monkeypatch):
    """`--create-db -n 4`: four workers migrating in turn would be four migrations end to end."""
    entry = tmp_path / "name.sqlite3"
    database = memory("afresh")
    ran, waits = [], []
    real = testdb.locked

    def locked(path, wait):
        waits.append(wait)
        return real(path, wait)

    monkeypatch.setattr(testdb, "locked", locked)
    with real(entry.with_suffix(".lock")) as held:
        assert held
        outcome = testdb.migrate_or_restore(
            lambda: database, lambda: ran.append(migrated(database)), entry, afresh=True, wait=5
        )

    assert waits == [0], "somebody else holds the lock, and it went on without it"
    assert (outcome, len(ran)) == ("migrated", 1)
    assert testdb.is_whole(entry)


def test_only_the_databases_used_last_are_kept(tmp_path):
    """One file per set of migrations ever met would grow for as long as the project does."""
    now = time.time()
    for age in range(5):
        entry = tmp_path / f"name{age}.sqlite3"
        entry.write_bytes(b"database")
        entry.with_suffix(".lock").write_bytes(b"")
        os.utime(entry, (now - age * 60, now - age * 60))
    abandoned, writing = tmp_path / "old.1-a.tmp", tmp_path / "new.2-b.tmp"
    for path in (abandoned, writing):
        path.write_bytes(b"half")
    os.utime(abandoned, (now - 2 * testdb.ABANDONED_AFTER, now - 2 * testdb.ABANDONED_AFTER))

    testdb.prune(tmp_path, kept=2)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "name0.lock",
        "name0.sqlite3",
        "name1.lock",
        "name1.sqlite3",
        "new.2-b.tmp",
    ]


def test_a_restore_marks_the_database_as_used(memory, tmp_path):
    entry = tmp_path / "name.sqlite3"
    testdb.store(migrated(memory("source")), entry)
    os.utime(entry, (1, 1))

    assert testdb.restore(entry, memory("restored"))
    assert entry.stat().st_mtime > time.time() - 60


# ------------------------------------------------------------------- whether, and where
def test_ci_migrates_as_it_always_did(monkeypatch):
    """Core CI is unchanged until #1033 decides: with what the runner sets, nothing is kept
    and Django's `call_command` is not so much as looked at."""
    from django.core import management

    assert testdb.cache_directory({"CI": "true"}) is None
    assert testdb.cache_directory({"CI": "1", testdb.VARIABLE: ""}) is None
    assert testdb.report_header({"CI": "true"}) is None

    real = management.call_command
    monkeypatch.setattr(testdb, "fingerprint", None)
    with testdb.answering_migrate(testdb.cache_directory({"CI": "true"})):
        assert management.call_command is real
    assert management.call_command is real


def test_the_variable_switches_it_on_and_off_and_names_the_directory(tmp_path):
    local = {"LOCALAPPDATA": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)}
    default = testdb.default_directory(local)

    assert testdb.cache_directory(local) == default, "on by default on a person's machine"
    for off in ("0", "false", "No", "OFF"):
        assert testdb.cache_directory({**local, testdb.VARIABLE: off}) is None
    for on in ("1", "true", "Yes", "ON"):
        assert testdb.cache_directory({**local, "CI": "true", testdb.VARIABLE: on}) == default
    named = {**local, "CI": "true", testdb.VARIABLE: str(tmp_path / "kept here")}
    assert testdb.cache_directory(named) == tmp_path / "kept here"
    assert str(tmp_path / "kept here") in testdb.report_header(named)
    assert testdb.cache_directory({testdb.VARIABLE: "~/kept"}) == Path.home() / "kept"


def test_the_default_directory_is_the_persons_own_cache_and_no_checkout(tmp_path):
    home = Path.home()
    tail = Path("postulo") / "test-databases"

    assert testdb.default_directory({"LOCALAPPDATA": str(tmp_path)}, "win32") == tmp_path / tail
    assert testdb.default_directory({}, "win32") == home / "AppData" / "Local" / tail
    assert testdb.default_directory({}, "darwin") == home / "Library" / "Caches" / tail
    assert testdb.default_directory({"XDG_CACHE_HOME": str(tmp_path)}, "linux") == tmp_path / tail
    assert testdb.default_directory({}, "linux") == home / ".cache" / tail

    checkout = Path(__file__).resolve().parents[1]
    assert checkout not in testdb.default_directory().resolve().parents


def test_only_an_in_memory_sqlite_built_by_migrations_is_kept():
    """PostgreSQL, the browser suite's file and a database built without migrations migrate."""

    def connection(vendor="sqlite", memory=True, test=None):
        return SimpleNamespace(
            vendor=vendor,
            is_in_memory_db=lambda: memory,
            settings_dict={"TEST": {} if test is None else test},
        )

    assert testdb._may_be_kept(connection())
    assert testdb._may_be_kept(connection(test={"MIGRATE": True}))
    assert not testdb._may_be_kept(connection(vendor="postgresql", memory=False))
    assert not testdb._may_be_kept(connection(memory=False))
    assert not testdb._may_be_kept(connection(test={"MIGRATE": False}))


# ----------------------------------------------------------------------- inside Django
@pytest.fixture
def scratch(django_db_blocker):
    """Django connections to in-memory databases of the test's own, beside the suite's."""
    from django.db import connections

    made = []

    def make(alias: str):
        sqlite = {"ENGINE": "django.db.backends.sqlite3"}
        name = f"file:memorydb_{alias}?mode=memory&cache=shared"
        connections.settings[alias] = connections.configure_settings(
            {"default": dict(sqlite, NAME=":memory:"), alias: dict(sqlite, NAME=name)}
        )[alias]
        made.append(alias)
        return connections[alias]

    with django_db_blocker.unblock():
        yield make
        for alias in made:
            wrapper = connections[alias]
            if wrapper.connection is not None:
                wrapper.connection.close()
                wrapper.connection = None
            del connections[alias]
            del connections.settings[alias]


def test_django_restores_where_it_would_have_migrated(scratch, tmp_path, monkeypatch):
    """The whole of it, on Django's own connections: the first database is migrated and
    kept, the second is restored from it, and no other command is touched."""
    from django.core import management
    from django.db import connections

    ran = []

    def stand_in(name, *args, **options):
        ran.append((name, args, options))
        if name == "migrate" and not args:
            with connections[options["database"]].cursor() as cursor:
                cursor.execute("CREATE TABLE made (id integer PRIMARY KEY AUTOINCREMENT, n text)")
                cursor.execute("INSERT INTO made (n) VALUES ('by a migration')")

    monkeypatch.setattr(management, "call_command", stand_in)
    monkeypatch.setattr(testdb, "fingerprint", lambda connection, also=(): "name")
    first, second = scratch("first_1144"), scratch("second_1144")
    outcomes = []

    with testdb.answering_migrate(tmp_path, outcomes=outcomes):
        management.call_command("migrate", database="first_1144", run_syncdb=True, verbosity=0)
        management.call_command("createcachetable", database="first_1144")
        management.call_command("migrate", database="second_1144", run_syncdb=True, verbosity=0)
        management.call_command("migrate", "jobs", "zero", database="second_1144")

    assert management.call_command is stand_in, "put back as it was found"
    assert outcomes == [("first_1144", "migrated"), ("second_1144", "restored")]
    assert ran == [
        ("migrate", (), {"database": "first_1144", "run_syncdb": True, "verbosity": 0}),
        ("createcachetable", (), {"database": "first_1144"}),
        ("migrate", ("jobs", "zero"), {"database": "second_1144"}),
    ]
    assert contents(second.connection) == contents(first.connection)
    assert (tmp_path / "name.sqlite3").exists()


def test_a_name_that_cannot_be_worked_out_means_a_migration(scratch, tmp_path, monkeypatch):
    from django.core import management

    ran = []
    monkeypatch.setattr(management, "call_command", lambda name, **options: ran.append(name))

    def broken(connection, also=()):
        raise RuntimeError("no name")

    monkeypatch.setattr(testdb, "fingerprint", broken)
    scratch("unnamed_1144")

    with testdb.answering_migrate(tmp_path):
        management.call_command("migrate", database="unnamed_1144")

    assert ran == ["migrate"]
    assert list(tmp_path.iterdir()) == []


def test_an_app_with_no_migrations_is_named_by_the_tables_its_models_make(scratch):
    """`migrate --run-syncdb` builds those from the models as they are, so they are the name."""
    connection = scratch("tables_1144")

    statements = testdb._tables_without_migrations(connection, ["testapp"])

    assert statements and all("testapp_" in statement for statement in statements)
    assert any(statement.startswith("CREATE TABLE") for statement in statements)
    assert statements == testdb._tables_without_migrations(connection, ["testapp"])
    assert testdb._tables_without_migrations(connection, ["staticfiles"]) == []
    assert testdb.is_empty(connection.connection), "read, and nothing made"


def test_django_still_asks_for_migrate_where_this_answers_it():
    """`answering_migrate` stands in for the one name `create_test_db` looks up as it runs.

    If Django comes to import it once at the top of the module, or to migrate another way,
    nothing breaks -- every run migrates, as before -- and nothing says so either. This does.
    """
    import inspect

    from django.db.backends.base import creation

    source = inspect.getsource(creation.BaseDatabaseCreation.create_test_db)

    assert "call_command" not in vars(creation), "looked up when called, not when imported"
    assert "from django.core.management import call_command" in source
    assert '"migrate"' in source and "database=self.connection.alias" in source


# -------------------------------------------------------------------- what is published
def test_the_suite_sets_its_database_up_through_the_published_fixture(request):
    """The fixture a plugin's suite gets is the one this suite runs on, around pytest-django's."""
    defined = request.session._fixturemanager.getfixturedefs("django_db_setup", request.node)

    assert [definition.func.__module__ for definition in defined] == [
        "pytest_django.fixtures",
        "postulo.plugins.testing",
    ]
    assert defined[-1].scope == "session"
    assert defined[-1].argnames == ("request", "django_db_use_migrations", "django_db_createdb")


def test_the_published_helpers_do_not_bring_pytest_into_a_running_instance():
    """`postulo.plugins.testing` is installed with Postulo; pytest is not. Imported when the
    fixture is asked for, as the other helpers import what they need."""
    code = (
        "import sys; import postulo.plugins.testing, postulo.plugins.testdb; "
        "print(sorted(name for name in ('pytest', '_pytest', 'pytest_django', 'django') "
        "if name in sys.modules))"
    )

    answer = subprocess.run(  # noqa: S603 -- this interpreter, a line of our own
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert answer.stdout.strip() == "[]"
    assert testing.migrated_database_header() == testdb.report_header()
