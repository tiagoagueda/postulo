"""What the plugin install and rollback tests share: the environment, read once (#1033).

Neither file runs an installer or builds an environment: a wheel there is four small members
written with `zipfile`, and "installing" unpacks it. What made them the slowest files of the
suite was reading the *running* environment again and again. One `install_wheel` asks which
versions Postulo runs on five times, and each answer parses the METADATA of every installed
distribution twice; it then rebuilds the registry, which asks every distribution for its
entry points once for each kind of plugin. None of those files changes while the suite runs,
and no test asserts that they are read again.

So what `importlib.metadata` reads from an installed distribution is remembered here, for the
life of the process: an xdist worker reads its environment once, and nothing is shared between
workers or left on disk. Only the reading is remembered. Every scan, every comparison and the
import check's subprocess run as they did, and none of Postulo's own code is stood in for.

What keeps a remembered answer from being a wrong one:

- Only a distribution that sits directly in a directory that was on `sys.path` when this
  module was imported, which is before any test has run. A plugins directory is a test's own,
  put on the path by the install under test, and what is in it is read from the disk every
  time: that is the part the tests change, and break on purpose.
- The file is asked for its size and its modification time at every read, and an answer is
  kept for the file as it then was. A distribution that is replaced is read again.
"""

from __future__ import annotations

import os
import sys
from importlib import metadata

import pytest


def _as_listed(directory: str) -> str:
    return os.path.normcase(os.path.abspath(directory or "."))


#: Where installed distributions are looked for before any test adds a directory of its own.
ENVIRONMENT = frozenset(_as_listed(entry) for entry in sys.path)

#: Whether a directory is one of those, by the spelling `importlib.metadata` hands over.
_listed: dict[str, bool] = {}

#: What was read, by the file and how it stood: its size and its modification time.
_remembered: dict[tuple[str, int | None, int], object] = {}


def _in_environment(directory: str) -> bool:
    if directory not in _listed:
        _listed[directory] = _as_listed(directory) in ENVIRONMENT
    return _listed[directory]


def _remembering(read, filename: str, *, absence_is_an_answer: bool = False) -> property:
    """A property of a distribution that reads ``filename`` once for each state of the file."""

    def answer(distribution):
        try:
            folder = os.fspath(distribution._path)
            if not _in_environment(os.path.dirname(folder)):
                return read(distribution)
            path = os.path.join(folder, filename)
            try:
                found = os.stat(path)
                key = (path, found.st_size, found.st_mtime_ns)
            except FileNotFoundError:
                if not absence_is_an_answer:
                    raise
                # No such file is an answer too, kept for the directory as it stands: a file
                # added to a directory moves the directory's own modification time.
                key = (path, None, os.stat(folder).st_mtime_ns)
        except (AttributeError, TypeError, OSError):
            # Not a directory on the disk, or without the file: read as it always was.
            return read(distribution)
        if key not in _remembered:
            _remembered[key] = read(distribution)
        return _remembered[key]

    return property(answer, doc=read.__doc__)


_METADATA = _remembering(metadata.Distribution.metadata.fget, "METADATA")
_ENTRY_POINTS = _remembering(
    metadata.Distribution.entry_points.fget, "entry_points.txt", absence_is_an_answer=True
)


@pytest.fixture(autouse=True)
def environment_read_once():
    """What the running environment's distributions say is read once and remembered (#1033).

    Imported by the test files that install plugins, and in force for each of their tests.
    With a patch of its own, so that a test undoing its `monkeypatch` does not undo this.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(metadata.Distribution, "metadata", _METADATA)
        patch.setattr(metadata.Distribution, "entry_points", _ENTRY_POINTS)
        yield
