"""How Postulo opens a SQLite file when several workers share it (#206).

The image runs three gunicorn workers against one file, and SQLite's defaults fail the
moment two of them write at once. Every request is a transaction (``ATOMIC_REQUESTS``),
and SQLite's default transaction is *deferred*: it takes no lock until the first
statement, so a request that reads before it writes -- a form that validates and then
saves -- holds a read lock and asks to upgrade it. If another connection holds the write
lock, SQLite refuses the upgrade at once with *database is locked*, and does not wait out
the busy timeout, because waiting there could deadlock. That is how a form sent twice
answered a 500 for a company that had been saved: the second request failed in a hundred
milliseconds rather than after five seconds.

Three options together make two writers take turns instead:

* ``transaction_mode: IMMEDIATE`` -- the write lock is taken when the transaction begins,
  so a second writer *waits*, and the busy timeout applies. The cost is that every
  request's transaction, a read-only page included, waits for the one before it; on a
  personal instance with three workers that is milliseconds, and it is the price of a
  request never failing for having been sent at the same time as another.
* ``journal_mode=WAL`` -- readers stop blocking the writer and the writer stops blocking
  readers, which is what makes the serialisation above cheap. WAL keeps two files beside
  the database, ``-wal`` and ``-shm``, in the same directory; the backup uses SQLite's own
  backup API rather than copying the file, so a backup under WAL is a consistent copy.
* a ``timeout`` of twenty seconds rather than the default five, since a wait is now a
  wait rather than a refusal.

Applied only to a file: an in-memory database has one connection and nothing to share.
Applied with ``setdefault``, so an operator who sets an option in ``OPTIONS`` keeps it.
"""

from __future__ import annotations

from pathlib import Path

#: What every file-backed SQLite connection gets unless the operator says otherwise.
FILE_OPTIONS: dict = {
    "transaction_mode": "IMMEDIATE",
    "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
    "timeout": 20,
}


def is_sqlite(database: dict) -> bool:
    return "sqlite3" in str(database.get("ENGINE", ""))


def is_file(database: dict) -> bool:
    name = str(database.get("NAME", "") or "")
    return is_sqlite(database) and Path(name).name != ":memory:" and "mode=memory" not in name


def apply_options(database: dict) -> dict:
    """Put the file options on a SQLite database's settings, keeping anything already set."""
    if not is_file(database):
        return database
    options = database.setdefault("OPTIONS", {})
    for key, value in FILE_OPTIONS.items():
        options.setdefault(key, value)
    return database
