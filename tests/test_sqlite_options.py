"""Two workers writing to one SQLite file take turns rather than failing (#206).

A form sent twice answered a 500 for a company that had been saved: SQLite's default
deferred transaction cannot upgrade a read lock to a write lock while another connection
holds it, and refuses at once rather than waiting. The settings ask for an immediate
transaction, a write-ahead log and a longer wait, for a file and not for memory; and the
raw behaviour is shown here, in two threads on a file, so the reason for the options is
on record beside them.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time

import pytest

from postulo.config import sqlite as sqlite_options

# ------------------------------------------------------------------ the settings


def test_a_file_database_gets_the_options_and_memory_is_left_alone(tmp_path):
    file = {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "postulo.sqlite3")}
    sqlite_options.apply_options(file)
    assert file["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    assert "journal_mode=WAL" in file["OPTIONS"]["init_command"]
    assert file["OPTIONS"]["timeout"] == 20

    memory = {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    assert "OPTIONS" not in sqlite_options.apply_options(memory)
    postgres = {"ENGINE": "django.db.backends.postgresql", "NAME": "postulo"}
    assert "OPTIONS" not in sqlite_options.apply_options(postgres)


def test_what_the_operator_set_is_kept(tmp_path):
    database = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(tmp_path / "postulo.sqlite3"),
        "OPTIONS": {"timeout": 3},
    }
    sqlite_options.apply_options(database)
    assert database["OPTIONS"]["timeout"] == 3
    assert database["OPTIONS"]["transaction_mode"] == "IMMEDIATE"


def test_the_settings_module_applies_them():
    """`base.py` calls this for the default database, whatever URL the operator gave."""
    from pathlib import Path

    source = (Path(sqlite_options.__file__).parent / "settings" / "base.py").read_text(
        encoding="utf-8"
    )
    assert 'sqlite.apply_options(DATABASES["default"])' in source


@pytest.mark.django_db
def test_a_file_database_holds_its_write_ahead_log_to_a_limit(tmp_path):
    """SQLite never shrinks the `-wal` file by itself: after one large write it stayed at
    its largest for as long as any connection was open, which with three workers, a
    scheduler and a worker is for good. Every connection to a file now asks for 64 MB, and
    a freshly opened one says so (#1128)."""
    from django.db import connections
    from django.db.backends.sqlite3.base import DatabaseWrapper

    database = sqlite_options.apply_options(
        {
            **connections["default"].settings_dict,
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(tmp_path / "postulo.sqlite3"),
            "OPTIONS": {},
        }
    )
    assert "PRAGMA journal_size_limit=67108864;" in database["OPTIONS"]["init_command"]
    assert "journal_mode=WAL" in database["OPTIONS"]["init_command"]

    opened = DatabaseWrapper(database, "scratch")
    try:
        with opened.cursor() as cursor:
            cursor.execute("PRAGMA journal_size_limit")
            assert cursor.fetchone() == (67108864,)
            cursor.execute("PRAGMA journal_mode")
            assert cursor.fetchone() == ("wal",), "and the options before it still apply"
    finally:
        opened.close()


# ----------------------------------------------------------------- the behaviour


def _writer(path: str, mode: str, hold: float, results: list, *, start_after: float = 0.0):
    """One transaction: begin in ``mode``, read, hold the transaction open, then write."""
    time.sleep(start_after)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        connection.execute(f"BEGIN {mode}")
        connection.execute("SELECT count(*) FROM t").fetchone()
        time.sleep(hold)
        connection.execute("INSERT INTO t (v) VALUES (?)", (mode,))
        connection.execute("COMMIT")
        results.append("ok")
    except sqlite3.OperationalError as error:
        results.append(str(error))
    finally:
        connection.close()


@pytest.fixture
def database(tmp_path) -> str:
    path = str(tmp_path / "shared.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (v TEXT)")
    connection.commit()
    connection.close()
    return path


def race(path: str, mode: str) -> list[str]:
    results: list[str] = []
    first = threading.Thread(target=_writer, args=(path, mode, 0.6, results))
    second = threading.Thread(
        target=_writer, args=(path, mode, 0.0, results), kwargs={"start_after": 0.2}
    )
    first.start()
    second.start()
    first.join()
    second.join()
    return results


def test_a_deferred_second_writer_is_refused_at_once_which_is_the_bug(database):
    results = race(database, "DEFERRED")
    assert results.count("ok") == 1
    assert any("locked" in result for result in results), results


def _log_after_a_burst(path: str, limit: int | None) -> tuple[int, int]:
    """The size of the `-wal` file after one large write, and after the small ones that follow."""
    from pathlib import Path

    connection = sqlite3.connect(path, isolation_level=None)
    # A second connection, as an instance always has: the log is only removed when the last
    # one closes.
    other = sqlite3.connect(path)
    try:
        if limit is not None:
            connection.execute(f"PRAGMA journal_size_limit={limit}")
        connection.execute("BEGIN")
        connection.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 600) "
            "INSERT INTO t (v) SELECT hex(randomblob(5000)) FROM n"
        )
        connection.execute("COMMIT")
        log = Path(f"{path}-wal")
        burst = log.stat().st_size
        # The limit is applied when a write starts the log again after a checkpoint.
        for _ in range(2):
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            connection.execute("INSERT INTO t (v) VALUES ('small')")
        return burst, log.stat().st_size
    finally:
        other.close()
        connection.close()


def test_without_a_limit_the_log_stays_at_its_largest_and_with_one_it_shrinks_back(database):
    """What `journal_size_limit` is for, on a file (#1128). One large write leaves a log of
    its own size, and the small writes after it go on using that file without ever making
    it smaller; with a limit, the first of them to start the log again cuts it back. One
    megabyte here where an instance asks for sixty-four, so the test writes six and not
    four hundred."""
    megabyte = 1024 * 1024
    burst, later = _log_after_a_burst(database, None)
    assert burst > 4 * megabyte
    assert later == burst, "which is the bug: megabytes kept for a row of five letters"

    burst, later = _log_after_a_burst(database, megabyte)
    assert burst > 4 * megabyte
    assert later == megabyte


def test_an_immediate_second_writer_waits_its_turn(database):
    results = race(database, "IMMEDIATE")
    assert results == ["ok", "ok"], results
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM t").fetchone()[0] == 2
