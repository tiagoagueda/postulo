"""What is installed, where it lives, and how every process knows it has changed.

The record on the data volume -- read, written, stamped -- and the directory beside it. It
was the first half of `installing`, and everything that asks a plugin anything reads it: the
registry for what is switched off and whether to catch up, the manifest for where a plugin
came from, the data guard for which package a model belongs to, the policy for its memo.
`installing` in turn imports the registry, so all of those reached up into the module that
reached back down to them, through imports moved inside functions to stop the loop closing
at startup (#248). Here it depends on nothing of Postulo's, and `installing` hands the same
names out as it always did.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import site
import sys
import threading
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

#: The file on the volume that says what is installed. Its absence means "nothing".
RECORD_NAME = "plugins.json"
#: Held while the record is read, changed and written back, so two changes at once do not
#: lose one of them (#382). Never copied into a snapshot.
LOCK_NAME = ".record.lock"
#: Where the next record is written before it is moved over the real one.
SCRATCH_NAME = ".plugins.json.writing"


class RecordUnreadable(RuntimeError):
    """``plugins.json`` exists but cannot be parsed.

    Not the same as "nothing installed": the record is the only list of what is installed
    and what is switched off, so a file nobody can read must not be taken for an empty one
    (#382). Writes are refused until an administrator has put it right.
    """


@dataclass
class Installed:
    """One line of the record: what is installed, and where it came from."""

    name: str
    version: str
    origin: str = "upload"  # upload | catalogue:<name>
    source: str = ""  # the wheel's filename, or the URL it came from
    sha256: str = ""
    installed_at: str = ""
    installed_by: str = ""
    entry_points: list[str] = field(default_factory=list)
    disabled: bool = False
    #: What the wheel said about itself. Read at install time and, until #97, thrown away
    #: the moment the confirmation screen had shown it -- so an administrator could see who
    #: wrote a plugin exactly once, and never again.
    summary: str = ""
    licence: str = ""
    author: str = ""
    source_url: str = ""
    #: Which Postulo the plugin says it is for, as the catalogue release declared it -- a
    #: version specifier such as ``>=0.3,<1.0``. Empty for an upload, which declares
    #: nothing: a wheel's own metadata cannot say it, since a plugin does not depend on
    #: Postulo. Checked at install and shown on the page ever after, so a core upgrade
    #: that leaves a plugin behind is visible there and not only in a log (#186).
    requires_postulo: str = ""
    #: Everything that arrived alongside the wheel, as ``name==version``. The signature
    #: and the checksum cover the plugin's own file and nothing else: its requirements are
    #: resolved from PyPI at install time and are whatever was served that day. Recording
    #: them is what lets an administrator see what is actually in their instance without a
    #: shell, and notice when an update quietly brings something new.
    dependencies: list[str] = field(default_factory=list)

    @property
    def is_from_catalogue(self) -> bool:
        return self.origin.startswith("catalogue:")


# ---------------------------------------------------------------- the directory


def plugins_dir() -> Path:
    return Path(settings.POSTULO_PLUGINS_DIR)


def record_path() -> Path:
    return plugins_dir() / RECORD_NAME


def activate() -> None:
    """Put the plugins directory on the import path, if it exists.

    ``site.addsitedir`` rather than a plain ``sys.path`` append, so that ``.pth`` files a
    package ships are honoured, exactly as they would be in a normal installation.
    """
    directory = plugins_dir()
    if not directory.is_dir():
        return
    if str(directory) not in sys.path:
        site.addsitedir(str(directory))


# ------------------------------------------------------------------- the record


#: The record as it was last parsed, and the stamp it was parsed at (#231). Read and parsed
#: on every plugin lookup before this: `decide` asks, `shipped_inside` asks, and a company
#: page that draws six plugin marks asked six times, each a file read and a JSON parse.
#:
#: Keyed on `record_stamp`, which is one `stat`. That is the same test every other process
#: uses to decide whether to reload, and `write_record` guarantees it moves whenever the
#: record does -- so this cannot go stale behind an install made by the scheduler or by a
#: second worker, which a plain memo would.
_record_cache: tuple[str, list[Installed]] | None = None


def read_record() -> list[Installed]:
    global _record_cache

    stamp = record_stamp()
    if _record_cache is not None and _record_cache[0] == stamp:
        # A copy, because callers filter and sort what they are handed and one of them
        # mutating the list, or a line in it, would change what the next one reads -- and
        # would change it even when the write that followed failed (#382).
        return [replace(entry) for entry in _record_cache[1]]
    path = record_path()
    if not path.is_file():
        _record_cache = (stamp, [])
        return []
    try:
        found = _parse(path)
    except RecordUnreadable:
        # Fail closed (#382): not "nothing is installed and nothing is switched off", which
        # is what an empty list says. What this process last read stands until the file is
        # mended, and it is not cached under the new stamp, so the next call looks again.
        return [replace(entry) for entry in _record_cache[1]] if _record_cache else []
    _record_cache = (stamp, list(found))
    return found


def _parse(path: Path) -> list[Installed]:
    """The record's lines, or `RecordUnreadable` if the file is there and cannot be read."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.error("The plugins record %s cannot be read: %s", path, error)
        raise RecordUnreadable(str(path)) from error
    entries = raw.get("plugins", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        logger.error("The plugins record %s is not a list of plugins", path)
        raise RecordUnreadable(str(path))
    found = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name"):
            fields = {key: entry.get(key) for key in Installed.__dataclass_fields__ if key in entry}
            found.append(Installed(**fields))
    return found


def is_unreadable() -> bool:
    """Whether the record is there and cannot be parsed."""
    path = record_path()
    if not path.is_file():
        return False
    try:
        _parse(path)
    except RecordUnreadable:
        return True
    return False


#: Which distribution each importable top-level package came from, cached on the record's
#: stamp (#231).
#:
#: `importlib.metadata.packages_distributions()` walks *every* installed distribution and
#: reads its file list to build this -- Django, Pillow, WeasyPrint, the lot. On the
#: administrator's plugins page it was about four and a half seconds of a page load, per
#: load, and something like forty seconds of every CI run.
#:
#: The answer changes only when something is installed or removed, which is exactly when the
#: record's stamp moves, so that is the key. Nothing else in the process installs packages
#: while it runs.
_distributions_cache: tuple[str, dict[str, list[str]]] | None = None


def packages_by_distribution() -> dict[str, list[str]]:
    """``{top-level package: [distribution names]}``, read once per record. See above."""
    global _distributions_cache

    stamp = record_stamp()
    if _distributions_cache is None or _distributions_cache[0] != stamp:
        from importlib.metadata import packages_distributions

        try:
            mapping = dict(packages_distributions())
        except Exception:  # pragma: no cover - a broken distribution explains nobody's name
            mapping = {}
        _distributions_cache = (stamp, mapping)
    return _distributions_cache[1]


_locks = threading.local()


@contextlib.contextmanager
def record_lock():
    """Hold the record's lock: every read-modify-write, install, removal and rollback.

    Reentrant, so `install_wheel` can hold it across the whole install and still call
    `write_record` inside. The lock is on a file in the plugins directory, which is what
    every process that can change the record shares.
    """
    depth = getattr(_locks, "depth", 0)
    if depth:
        _locks.depth = depth + 1
        try:
            yield
        finally:
            _locks.depth -= 1
        return
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / LOCK_NAME, "a+b") as handle:
        if os.name == "nt":  # pragma: no cover - the image is Linux
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _locks.depth = 1
        try:
            yield
        finally:
            _locks.depth = 0
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            # On Linux closing the file releases the flock.


def write_record(entries: list[Installed]) -> None:
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = record_path()
    if path.is_file():
        # Refuse to write over a record that cannot be read: what it held is lost the
        # moment it is replaced, and it may be one character away from being mended.
        _parse(path)
    payload = {
        "version": 1,
        "plugins": [asdict(entry) for entry in sorted(entries, key=lambda e: e.name)],
    }
    before = record_stamp()
    # Written beside the record and moved over it, so a write that fails half way (a full
    # disk) leaves the record that was there, never a truncated one (#382).
    scratch = directory / SCRATCH_NAME
    try:
        with open(scratch, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, path)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise
    if record_stamp() == before:
        # Every other process decides whether to reload from this stamp, so a write that
        # does not move it is a change nobody else ever sees. Two writes land in the same
        # stamp only where the filesystem keeps coarse timestamps *and* the record came out
        # the same length -- switching a plugin off and on again within the same second is
        # exactly that -- so the timestamp is pushed into the next tick rather than left
        # ambiguous. Nothing reads this time as a time; it only has to differ.
        status = path.stat()
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))


def record_stamp() -> str:
    """What the record looks like from outside, as "has it changed?" and nothing more.

    The modification time and the length, not the contents: this is asked on every plugin
    lookup in every process, and a `stat` is one cheap syscall where reading and parsing the
    file is several. ``write_record`` guarantees the pair moves whenever the record does.

    Empty when there is no record, or when the settings are not usable yet, which are both
    "nothing installed" as far as a caller is concerned.
    """
    try:
        status = record_path().stat()
    except (OSError, ImproperlyConfigured):
        return ""
    return f"{status.st_mtime_ns}:{status.st_size}"


def installed(name: str) -> Installed | None:
    canonical = canonicalise(name)
    for entry in read_record():
        if canonicalise(entry.name) == canonical:
            return entry
    return None


class _EverythingOff(set):
    """Stands in for the disabled names when they cannot be known: every name is in it."""

    def __contains__(self, name: object) -> bool:
        return True


def disabled_names() -> set[str]:
    """Canonical names of plugins the administrator has switched off.

    If the record cannot be parsed and this process never read it, nothing is known about
    what was switched off, so everything counts as switched off rather than nothing (#382).
    """
    if _record_cache is None and is_unreadable():
        return _EverythingOff()
    return {canonicalise(entry.name) for entry in read_record() if entry.disabled}


def canonicalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()
