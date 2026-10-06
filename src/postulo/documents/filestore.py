"""Where the bytes of a kept file live: the one boundary, and nothing else says (#663).

Postulo keeps files with Django's storage API, which is what `FileField` already uses, and
local media stays the source of truth (#13). Around that, five places used to reach for the
directory itself: delivering a file, finding the ones nothing names, deleting an account,
taking a backup and counting the files for the overview. They all call this module now, so
the day a storage backend has to be primary the change is here and in what it provides.

**What a backend has to provide**, beyond Django's own `Storage` (`open`, `save`, `delete`,
`exists`, `size`, `listdir`, `get_modified_time`):

* ``path`` is optional. Where a backend has none, a file is streamed by the application and
  no proxy is handed a path (`local_path` says so by returning nothing).
* ``root`` is local-only and answers the two things that are about a directory as such: a
  backup tars it, and a restore writes into it. A backend with no directory has no
  tree to tar, and says so by having no root.

Nothing here is published on `postulo.plugins.api`: it waits for a plugin that needs it.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from django.conf import settings
from django.core.files.storage import default_storage


@dataclass(frozen=True)
class Stored:
    """One file the store holds: its name as a row stores it, its size, its age."""

    name: str
    size: int
    modified: datetime | None


def root() -> Path:
    """The directory local media lives in. The only place that reads the setting."""
    return Path(settings.MEDIA_ROOT)


def local_path(name: str) -> Path | None:
    """The path of a stored name on this machine, or ``None`` where it has none.

    Raises `SuspiciousFileOperation` for a name that leaves the root, which is the guard
    `core.files` has always applied to a stored name.
    """
    try:
        return Path(default_storage.path(name))
    except NotImplementedError:
        return None


def exists(name: str) -> bool:
    try:
        return default_storage.exists(name)
    except OSError:
        return False


def size(name: str) -> int:
    return default_storage.size(name)


def open_file(name: str) -> IO[bytes]:
    return default_storage.open(name, "rb")


def delete(name: str) -> None:
    default_storage.delete(name)


def _walk(directory: str) -> Iterator[str]:
    try:
        directories, files = default_storage.listdir(directory)
    except (FileNotFoundError, NotADirectoryError):
        return
    for file in sorted(files):
        yield posixpath.join(directory, file) if directory else file
    for child in sorted(directories):
        yield from _walk(posixpath.join(directory, child) if directory else child)


def names(prefix: str = "") -> Iterator[str]:
    """Every file name under ``prefix``, as a row would store it, in order."""
    yield from _walk(prefix.strip("/"))


def listing(prefix: str = "") -> Iterator[Stored]:
    """`names`, each with its size and when it was last written."""
    for name in names(prefix):
        try:
            modified = default_storage.get_modified_time(name)
        except (OSError, NotImplementedError):
            modified = None
        try:
            length = default_storage.size(name)
        except OSError:
            continue
        yield Stored(name, length, modified)


def totals() -> tuple[int, int]:
    """How many files, and how many bytes, the store holds."""
    count = 0
    total = 0
    for stored in listing():
        count += 1
        total += stored.size
    return count, total


def prune_empty_directories(prefix: str) -> int:
    """Remove ``prefix`` and any empty directory inside it, never a file. How many went.

    A directory is a thing of the local disk: a backend that has none leaves nothing to do.
    """
    directory = local_path(prefix)
    if directory is None or not directory.is_dir():
        return 0
    removed = 0
    for child in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
                removed += 1
            except OSError:
                pass
    try:
        directory.rmdir()
        removed += 1
    except OSError:
        pass
    return removed


def would_prune(prefix: str, going: set[str]) -> int:
    """How many directories `prune_empty_directories` will remove once ``going`` are gone."""
    directory = local_path(prefix)
    if directory is None or not directory.is_dir():
        return 0
    gone = set()
    for name in going:
        path = local_path(name)
        if path is not None:
            gone.add(path.resolve())
    count = 0
    emptied: set[Path] = set()
    nodes = sorted(
        [directory, *(p for p in directory.rglob("*") if p.is_dir())],
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for node in nodes:
        try:
            children = list(node.iterdir())
        except OSError:
            continue
        if all(
            (child in emptied) or (child.is_file() and child.resolve() in gone)
            for child in children
        ):
            emptied.add(node)
            count += 1
    return count
