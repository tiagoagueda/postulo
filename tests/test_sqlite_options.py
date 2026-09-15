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


def test_an_immediate_second_writer_waits_its_turn(database):
    results = race(database, "IMMEDIATE")
    assert results == ["ok", "ok"], results
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM t").fetchone()[0] == 2
