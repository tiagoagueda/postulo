"""The ZIP LinkedIn sends, opened without unpacking it anywhere (#1260).

A LinkedIn data archive is a ZIP of CSV files, the career's among a great deal else:
messages, connections, invitations, ads, logins. **Only the members this importer knows by
name are opened**, each through a bounded read; every other member is named on the review
and never opened. Nothing is written to disk and no member's path is followed: a member is
known by its base name alone, in whatever folder the archive put it.

The bounded read is the one `core.importer` uses for a Postulo archive's files: the size the
directory declares is asked first, and then one byte more than the limit is read, so a
directory that understates a member is caught rather than believed.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from dataclasses import dataclass, field

#: What a ZIP starts with, and an empty one.
ZIP_STARTS = (b"PK\x03\x04", b"PK\x05\x06")

#: The most one member may unpack to: a career sheet is a few kilobytes.
MAX_MEMBER_BYTES = 2 * 1024 * 1024

#: The most directory entries looked at. A LinkedIn archive has a few dozen.
MAX_ENTRIES = 2000

#: Members that say the ZIP is something else: a word processor's file, an OpenDocument
#: file, a browser extension, Postulo's own archive.
NOT_AN_EXPORT = frozenset({"[content_types].xml", "mimetype", "manifest.json", "postulo.json"})


@dataclass
class Opened:
    """What came out of the archive: the known members' bytes, and what was left."""

    #: Each known member's bytes, by its base name in lower case.
    members: dict[str, bytes] = field(default_factory=dict)
    #: The base name each was stored under, for the review: ``Positions.csv``.
    names: dict[str, str] = field(default_factory=dict)
    #: Every other file, by its base name, never opened.
    unopened: list[str] = field(default_factory=list)
    #: Known members that were not read, with why: ``(name, reason)``.
    refused: list[tuple[str, str]] = field(default_factory=list)
    #: Whether the directory was longer than `MAX_ENTRIES`.
    cut_short: bool = False


#: Why a known member was not read, as a key the reader puts into words.
TOO_LARGE = "too-large"
ENCRYPTED = "encrypted"
DAMAGED = "damaged"
TWICE = "twice"


def is_zip(data: bytes) -> bool:
    return data.startswith(ZIP_STARTS)


def base_name(stored: str) -> str:
    """The last part of a member's name, whatever separator the archive used."""
    return stored.replace("\\", "/").rstrip("/").rpartition("/")[2]


def directory(data: bytes) -> list[zipfile.ZipInfo] | None:
    """The archive's entries, at most `MAX_ENTRIES` + 1, or ``None`` if it is no ZIP."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.infolist()[: MAX_ENTRIES + 1]
    except (zipfile.BadZipFile, zlib.error, ValueError, EOFError, OSError):
        return None


def names_in(data: bytes) -> set[str] | None:
    """The base names in the archive, in lower case; ``None`` if it is no ZIP."""
    entries = directory(data)
    if entries is None:
        return None
    return {base_name(entry.filename).lower() for entry in entries if not entry.is_dir()}


def open_known(data: bytes, known: frozenset[str]) -> Opened:
    """The members whose base name, in lower case, is in ``known``; the rest only named."""
    opened = Opened()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            opened.cut_short = True
            entries = entries[:MAX_ENTRIES]
        for entry in entries:
            if entry.is_dir():
                continue
            name = base_name(entry.filename)
            key = name.lower()
            if key not in known:
                opened.unopened.append(name)
                continue
            if key in opened.members or any(key == seen.lower() for seen, _ in opened.refused):
                opened.refused.append((name, TWICE))
                continue
            if entry.flag_bits & 0x1:
                opened.refused.append((name, ENCRYPTED))
                continue
            if entry.file_size > MAX_MEMBER_BYTES:
                opened.refused.append((name, TOO_LARGE))
                continue
            try:
                with archive.open(entry) as handle:
                    content = handle.read(MAX_MEMBER_BYTES + 1)
            except (
                zipfile.BadZipFile,
                zlib.error,
                NotImplementedError,
                RuntimeError,
                EOFError,
                ValueError,
                OSError,
            ):
                opened.refused.append((name, DAMAGED))
                continue
            if len(content) > MAX_MEMBER_BYTES:
                opened.refused.append((name, TOO_LARGE))
                continue
            opened.members[key] = content
            opened.names[key] = name
    return opened
