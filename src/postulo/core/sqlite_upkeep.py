"""What a SQLite file needs done to it now and then, and nothing did (#1128).

Three things, each one statement, beside the options `config.sqlite` opens the file with
(which is where the first of them, the limit on the write-ahead log, is set).

**The planner's statistics, once a day.** SQLite chooses an index by what ``sqlite_stat1``
says about it, and that table is whatever the last ``ANALYZE`` wrote: with none ever run,
nothing. ``PRAGMA optimize`` runs it for the tables that need it and is a no-op for the
rest. `optimize_if_due` is called by the scheduler's pass and by the hourly tidy a request
does (`core.slow.tidy_up_if_due`) for the instance that runs no scheduler.

Asked with a mask, because the plain form judges a table by what *this connection* has
been asked about it, and a connection here lives for one pass or one request: measured on
SQLite 3.50, a table that had grown two hundredfold was left alone by a connection that
had not queried it. ``0x10000`` looks at every table; ``0x10`` holds each analysis to a
sample, as the plain form does, so that the request it may run in is not kept waiting on a
large table (18 ms for one of 400,000 rows); ``0x02`` is the analysis itself. An SQLite
older than 3.46 has only the last of the three, and judges as the plain form does.

It runs inside a transaction as well as outside one, and is rolled back with it; where the
default cache is the database's, the gate is a row of the same transaction, so the two go
together. Run while another connection holds the write lock, it waits as any write does and
then fails with *database is locked*: that is logged and left for the next day, never raised.

**The room deleted rows leave, on request.** A deleted row's page goes on a list inside the
file, to be used again; the file itself never shrinks, and a backup copies it whole.
`reclaimable` says how much is on that list, for the staff overview, and `compact` gives it
back with ``VACUUM``. Never by itself: ``VACUUM`` holds the write lock for its whole run,
and writes everything the database holds twice before the file shrinks, once to a temporary
file and once to the write-ahead log beside the database (measured: a 235 MB file with a
third of it free wrote some 155 MB to each). So `compact` looks at the free disk in both
places first and refuses when there is not enough.

PostgreSQL does all of this by itself. Pointed at one, nothing here issues a statement or
takes a gate.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.core.cache import cache
from django.db import Error, connection
from django.template.defaultfilters import filesizeformat

from postulo.config import sqlite as sqlite_options

logger = logging.getLogger(__name__)

#: How long one refresh of the statistics leaves the next caller alone.
OPTIMIZE_EVERY_SECONDS = 86400

#: The key, in the default cache, that says the statistics were refreshed within the day.
GATE = "core:sqlite-optimized"

#: Analyse (0x02) every table that needs it (0x10000), from a sample of its rows (0x10).
OPTIMIZE = "PRAGMA optimize=0x10012"


class CannotCompact(Exception):
    """Why a compaction did not happen, in words for whoever asked for it."""


@dataclass(frozen=True)
class Space:
    """What a SQLite database occupies, and how much of that holds nothing. In bytes."""

    size: int
    free: int

    @property
    def used(self) -> int:
        return self.size - self.free


def is_sqlite() -> bool:
    return connection.vendor == "sqlite"


def in_words(size: int) -> str:
    """A size as the pages write one, for a terminal: with a space any console can draw."""
    return filesizeformat(size).replace("\xa0", " ")


def optimize() -> bool:
    """Refresh the planner's statistics where they are missing or out of date.

    Whether the statement ran. A database that could not be written to within its timeout
    is not an error anybody can act on from a page or a pass, so it is logged and no more.
    """
    if not is_sqlite():
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute(OPTIMIZE)
    except Error:
        logger.warning("Could not refresh the query planner's statistics", exc_info=True)
        return False
    return True


def optimize_if_due() -> bool:
    """Refresh the planner's statistics, at most once a day. SQLite only.

    Cheap enough to call on every pass: on another engine it returns at once, and here the
    gate is one ``add``. Whoever loses the race for it, or finds the cache down, does
    nothing, as in `cache_purge.purge_if_due`.
    """
    if not is_sqlite():
        return False
    try:
        if not cache.add(GATE, True, OPTIMIZE_EVERY_SECONDS):
            return False
    except Exception:
        return False
    return optimize()


def _pragma(cursor, name: str) -> int:
    cursor.execute(f"PRAGMA {name}")  # one of three names written below, never a person's
    return int(cursor.fetchone()[0])


def reclaimable() -> int | None:
    """The bytes compacting would give back, or ``None`` where the question has no answer.

    Two pragmas, both answered from the first page of the file, which every statement reads
    anyway. ``None`` on another engine, and when the database cannot be asked.
    """
    if not is_sqlite():
        return None
    try:
        with connection.cursor() as cursor:
            return _pragma(cursor, "freelist_count") * _pragma(cursor, "page_size")
    except Error:
        return None


def space() -> Space:
    """How large the database is and how much of it is free pages."""
    with connection.cursor() as cursor:
        page = _pragma(cursor, "page_size")
        return Space(
            size=_pragma(cursor, "page_count") * page,
            free=_pragma(cursor, "freelist_count") * page,
        )


def database_file() -> Path | None:
    """The file this instance's database is, or ``None`` for one kept in memory."""
    database = connection.settings_dict
    if not sqlite_options.is_file(database):
        return None
    return Path(str(database["NAME"]))


def temporary_directory() -> Path:
    """Where SQLite writes a temporary file, which is not quite where Python does.

    Its own search, in its order (`unixTempFileDir`): two variables, then ``/var/tmp``
    before ``/tmp``, taking the first that is a directory it may write to. That order
    matters on a host whose ``/tmp`` is memory. On Windows both ask the system.
    """
    if os.name != "nt":
        named = (os.environ.get("SQLITE_TMPDIR"), os.environ.get("TMPDIR"))
        for candidate in (*named, "/var/tmp", "/usr/tmp", "/tmp", "."):  # noqa: S108
            if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK | os.X_OK):
                return Path(candidate)
    return Path(tempfile.gettempdir())


def check_room(path: Path, used: int) -> None:
    """Refuse when the disk cannot hold what ``VACUUM`` writes before the file shrinks.

    The pages in use are written twice: to the write-ahead log beside the database, and to
    a temporary file wherever SQLite keeps those. Where both are one disk it has to hold
    both. A twentieth is added for what the log needs around each page.
    """
    each = used + used // 20
    places: dict[int, list] = {}
    for directory in (path.parent, temporary_directory()):
        place = places.setdefault(directory.stat().st_dev, [directory, 0])
        place[1] += each
    for directory, needed in places.values():
        free = shutil.disk_usage(directory).free
        if free < needed:
            raise CannotCompact(
                f"Not enough free disk to compact {path.name}: {directory} has "
                f"{in_words(free)} free, and compacting needs {in_words(needed)} "
                "there while it runs. Nothing was changed."
            )


def compact() -> Space:
    """Give the free pages back to the disk. What the database occupies afterwards.

    ``VACUUM`` rebuilds the file without them. It cannot run inside a transaction, it holds
    the write lock until it is done, so every other writer waits, and it fails with nothing
    changed if the disk fills or the lock cannot be had.

    The checkpoint after it is what makes the saving show at once: the rebuilt database is
    written through the write-ahead log, which would otherwise keep that size, beside a
    file already smaller, until later writes had gone round it. A checkpoint that a reader
    holds up is left to those writes.
    """
    path = database_file()
    if path is None:
        raise CannotCompact("This database is not a SQLite file, so there is nothing to compact.")
    if connection.in_atomic_block:
        raise CannotCompact("A database cannot be compacted from inside a transaction.")
    check_room(path, space().used)
    try:
        with connection.cursor() as cursor:
            cursor.execute("VACUUM")
    except Error as error:
        raise CannotCompact(
            f"{path.name} could not be compacted: {error}. Nothing was changed."
        ) from error
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            cursor.fetchall()
    except Error:
        logger.info("The write-ahead log could not be emptied after compacting", exc_info=True)
    return space()
