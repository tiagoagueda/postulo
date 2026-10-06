"""Whether a file is plausibly what its name says, and whether it is still what was kept (#663).

One place answers the first question for every way a file arrives: the upload form, the
archive importer and the scrub. It is **not a parser**. A PDF is not opened by a PDF
library, an ODT or a DOCX is read from its zip directory and never unpacked, and nothing
here executes or renders what it is given; the evidence is the bytes a format must start
and end with. The one decoder is Pillow's, for a picture, held to the same cap on pixels
the avatar is (`core.pictures.decode`), and the bytes are kept as they came: an upload is
kept as it was.

A library for the container questions (`puremagic`) was weighed and not taken for now: the
zip directory answers both questions that matter, in a dozen lines and with nothing new to
vet; the hand-written agreement between an extension and what it should hold is the part no
library supplies either way.

The second question is `matches`: a size and a SHA-256 read in chunks.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Callable
from typing import IO

from django.utils.translation import gettext as _

#: The extensions an uploaded document may carry. Nine, as the field has always allowed: a
#: narrower list would refuse what people already keep (#663).
EXTENSIONS = ("pdf", "doc", "docx", "odt", "rtf", "txt", "png", "jpg", "jpeg")

#: How much of the start and of the end of a file is looked at.
HEAD = 1024
TAIL = 1024

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _description(extension: str) -> str:
    """What the file was expected to be, in words, for the sentence that refuses it."""
    return {
        "pdf": _("a PDF"),
        "doc": _("a Word document"),
        "docx": _("a Word document"),
        "odt": _("an OpenDocument text"),
        "rtf": _("an RTF document"),
        "txt": _("a plain text file"),
        "png": _("a PNG picture"),
        "jpg": _("a JPEG picture"),
        "jpeg": _("a JPEG picture"),
    }.get(extension, _("the kind of file its name says"))


def extension_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _ends_with_eof(tail: bytes) -> bool:
    return b"%%EOF" in tail


def looks_like(extension: str, head: bytes, tail: bytes = b"") -> bool:
    """Whether the first and last bytes are those of the kind the extension names.

    Answers the kinds the two ends settle: PDF, DOC, RTF and text. A picture needs a decoder
    and a zip-based document needs its directory, so those are `problem`'s; asked about one,
    this says yes, because the ends are not what settles it.
    """
    extension = extension.lower()
    if extension == "pdf":
        return head.startswith(b"%PDF-") and _ends_with_eof(tail)
    if extension == "doc":
        return head.startswith(OLE2)
    if extension == "rtf":
        return head.startswith(b"{\\rtf")
    if extension == "txt":
        return b"\x00" not in head and b"\x00" not in tail
    return True


def _zip_problem(extension: str, handle: IO[bytes]) -> bool:
    """Whether the zip directory is *not* that of the document: listed, never unpacked."""
    try:
        with zipfile.ZipFile(handle) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return True
    names = [entry.filename for entry in entries]
    if extension == "odt":
        # The first entry, by the standard: it is how a reader tells one without opening it.
        return not names or names[0] != "mimetype"
    return "[Content_Types].xml" not in names


def _picture_problem(extension: str, handle: IO[bytes]) -> bool:
    from postulo.core import pictures

    kind = "image/png" if extension == "png" else "image/jpeg"
    handle.seek(0)
    try:
        pictures.decode(handle.read(), kinds={kind})
    except pictures.UnusablePicture:
        return True
    return False


_CONTAINERS: dict[str, Callable[[str, IO[bytes]], bool]] = {
    "odt": _zip_problem,
    "docx": _zip_problem,
    "png": _picture_problem,
    "jpg": _picture_problem,
    "jpeg": _picture_problem,
}


def problem(extension: str, handle: IO[bytes], size: int) -> str | None:
    """A sentence saying what was expected, or ``None`` where the file is plausible.

    ``handle`` is seekable and ``size`` is its length. The sentence says what the file was
    expected to be and not how it failed: what a stranger's file did is not for a form to
    explain. An extension that is not one of ours is not this function's to judge.
    """
    extension = extension.lower()
    refused = _("That does not look like %(kind)s. Check that it is the right file.") % {
        "kind": _description(extension)
    }
    if extension in _CONTAINERS:
        handle.seek(0)
        broken = _CONTAINERS[extension](extension, handle)
        handle.seek(0)
        return refused if broken else None
    if extension not in ("pdf", "doc", "rtf", "txt"):
        return None  # not one of ours: the extension validator's to refuse
    handle.seek(0)
    head = handle.read(HEAD)
    handle.seek(max(0, size - TAIL))
    tail = handle.read(TAIL)
    handle.seek(0)
    return None if looks_like(extension, head, tail) else refused


def problem_with_bytes(extension: str, content: bytes) -> str | None:
    """`problem` for bytes already in memory, as the importer has them."""
    import io

    return problem(extension, io.BytesIO(content), len(content))


# ----------------------------------------------------------------- what was kept


def digest_of(handle: IO[bytes]) -> tuple[int, str]:
    """The size and SHA-256 of a file, read in chunks and left as it was found."""
    digest = hashlib.sha256()
    size = 0
    while chunk := handle.read(64 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def digest_of_bytes(content: bytes) -> tuple[int, str]:
    return len(content), hashlib.sha256(content).hexdigest()


class Damage:
    """What a scrub found wrong with a kept file. Stored values, so never reworded."""

    MISSING = "missing"
    CHANGED = "changed"


def damage_of(name: str, *, size: int | None, checksum: str) -> str:
    """Whether the file kept under ``name`` is missing or no longer what was recorded.

    Empty where it is as it was. A row with no recorded figure (one from before they were
    kept) is held to whichever it has, and to nothing if it has neither: absence of a record
    is not evidence of damage.
    """
    from . import filestore

    if not filestore.exists(name):
        return Damage.MISSING
    try:
        with filestore.open_file(name) as handle:
            found_size, found = digest_of(handle)
    except OSError:
        return Damage.MISSING
    if size is not None and found_size != size:
        return Damage.CHANGED
    if checksum and found != checksum:
        return Damage.CHANGED
    return ""
