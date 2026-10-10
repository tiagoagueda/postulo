"""What Postulo writes to disk is readable by its owner alone (#789).

Inside the published image everything belongs to one user in a root-only volume, so none of
this changes anything there. On a bare-metal install the process umask is usually 022, and
every file and directory made with the defaults -- the database, the media tree, the log --
was readable by every other account on the machine. Permission bits mean nothing on
Windows, so the mode checks run on POSIX only; the settings checks run anywhere.
"""

from __future__ import annotations

import logging
import os
import stat

import pytest
from django.conf import settings

from postulo.config import sqlite
from postulo.core import logs

posix = pytest.mark.skipif(os.name == "nt", reason="permission bits are POSIX")


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def open_umask():
    """The umask a typical host has, which is what made these files readable by anybody."""
    before = os.umask(0o022)
    yield
    os.umask(before)


def test_uploaded_files_and_their_directories_are_the_owners():
    assert settings.FILE_UPLOAD_PERMISSIONS == 0o600
    assert settings.FILE_UPLOAD_DIRECTORY_PERMISSIONS == 0o700


@posix
def test_an_upload_lands_with_the_owners_mode(open_umask, tmp_path, settings):
    from django.core.files.base import ContentFile
    from django.core.files.storage import FileSystemStorage

    storage = FileSystemStorage(location=str(tmp_path))
    name = storage.save("documents/2026/cv.pdf", ContentFile(b"%PDF"))

    assert mode_of(storage.path(name)) == 0o600
    assert mode_of(storage.path("documents")) == 0o700


@posix
def test_a_database_directory_made_here_is_the_owners(open_umask, tmp_path):
    database = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(tmp_path / "data" / "postulo.sqlite3"),
    }

    sqlite.ensure_private_directory(database)

    assert mode_of(tmp_path / "data") == 0o700


@posix
def test_a_directory_that_exists_is_left_as_somebody_chose_it(open_umask, tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)  # noqa: S103 - the point: a directory somebody made open

    sqlite.ensure_private_directory(
        {"ENGINE": "django.db.backends.sqlite3", "NAME": str(shared / "postulo.sqlite3")}
    )

    assert mode_of(shared) == 0o755


def test_an_in_memory_database_makes_no_directory(tmp_path):
    sqlite.ensure_private_directory({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"})
    sqlite.ensure_private_directory({"ENGINE": "django.db.backends.postgresql", "NAME": "x"})


@posix
def test_the_log_directory_and_the_log_are_the_owners(open_umask, tmp_path):
    place = tmp_path / "logs"
    logs.ensure_directory_at(place)
    handler = logs.SharedRotatingFileHandler(place / "postulo.log", encoding="utf-8")
    try:
        record = logging.LogRecord("postulo.tests", logging.INFO, __file__, 1, "hello", None, None)
        handler.emit(record)
    finally:
        handler.close()

    assert mode_of(place) == 0o700
    assert mode_of(place / "postulo.log") == 0o600


@posix
@pytest.mark.skipif(logs.fcntl is None, reason="no flock, no lock file")
def test_the_rotation_lock_is_the_owners_too(open_umask, tmp_path):
    handler = logs.SharedRotatingFileHandler(
        tmp_path / "postulo.log", maxBytes=50, backupCount=2, encoding="utf-8"
    )
    try:
        for index in range(6):
            handler.emit(
                logging.LogRecord(
                    "postulo.tests", logging.INFO, __file__, 1, f"line {index} " * 4, None, None
                )
            )
    finally:
        handler.close()

    locks = [p for p in tmp_path.iterdir() if p.name.endswith(logs.LOCK_SUFFIX)]
    assert locks, "a rotation took the lock"
    assert all(mode_of(p) == 0o600 for p in locks)
