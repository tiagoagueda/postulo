"""Whether a file is plausibly what its name says, and whether it is still what was kept (#663).

One place answers the first question for every way a file arrives: the upload form, the
archive importer and the scrub. It is **not a parser**. A PDF is not opened by a PDF
library, an ODT or a DOCX is read from its zip directory and never unpacked, and nothing
here executes or renders what it is given; the evidence is the bytes a format must start
and end with. The one decoder is Pillow's, for a picture, held to the same cap on pixels
the avatar is (`core.pictures.decode`), and the bytes are kept as they came: an upload is
kept as it was. The one thing done to a picture is `without_metadata` (#964), at the person's
request and without re-encoding it.

A library for the container questions (`puremagic`) was weighed and not taken for now: the
zip directory answers both questions that matter, in a dozen lines and with nothing new to
vet; the hand-written agreement between an extension and what it should hold is the part no
library supplies either way.

The second question is `matches`: a size and a SHA-256 read in chunks.
"""

from __future__ import annotations

import hashlib
import io
import re
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


# ---------------------------------------------- metadata, cut out without re-encoding (#964)

#: A phone's photograph carries where it was taken, when and with what, in EXIF (and XMP and
#: IPTC). An uploaded certificate is often one, and is then attached to mail sent to an
#: employer. These are cut out of the file by its structure, so the compressed picture is
#: byte for byte what it was: a re-encode would cost a scan its quality and the stored
#: checksum its meaning.
#:
#: JPEG segments dropped: APP1 (Exif, XMP), APP3 (extended Exif), APP13 (Photoshop, IPTC) and
#: COM (a comment). APP0 (JFIF), APP2 (the ICC colour profile), APP14 (Adobe's colour
#: transform) and everything the decoder needs stay.
_JPEG_DROPPED = frozenset({0xE1, 0xE3, 0xED, 0xFE})
#: PNG chunks dropped: Exif, the three kinds of text, and the modification time.
_PNG_DROPPED = frozenset({b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: In the compressed data of a scan a 0xFF is followed by 0x00 (stuffing), a restart marker or
#: more 0xFF (padding); anything else is the next marker.
_NEXT_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
_ORIENTATION = 0x0112


def _orientation_segment(data: bytes) -> bytes:
    """An APP1 segment holding only the picture's orientation, or nothing where it is upright.

    The orientation is not about the photographer: it is how the pixels must be turned to be
    read the right way up, and without it a portrait photograph of a page lies on its side.
    """
    from PIL import Image

    try:
        turned = Image.open(io.BytesIO(data)).getexif().get(_ORIENTATION)
    except Exception:  # a picture Pillow cannot read the tags of has none to keep
        return b""
    if not isinstance(turned, int) or not 2 <= turned <= 8:
        return b""
    tiff = (
        b"II*\x00"
        + (8).to_bytes(4, "little")  # little-endian, first directory at 8
        + (1).to_bytes(2, "little")  # one entry
        + _ORIENTATION.to_bytes(2, "little")
        + (3).to_bytes(2, "little")  # tag, type SHORT
        + (1).to_bytes(4, "little")
        + turned.to_bytes(2, "little")
        + b"\x00\x00"
        + (0).to_bytes(4, "little")  # no further directory
    )
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload


def _jpeg_without_metadata(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        return data
    kept = [b"\xff\xd8"]
    orientation_at: int | None = None
    position = 2
    while position < len(data):
        if data[position] != 0xFF:
            return data  # not the structure of a JPEG: leave it alone
        while position + 1 < len(data) and data[position + 1] == 0xFF:
            position += 1  # fill bytes before a marker
        if position + 1 >= len(data):
            return data
        marker = data[position + 1]
        if marker == 0xD9:  # the end of the image; whatever follows is not part of it
            kept.append(b"\xff\xd9")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # no length
            kept.append(data[position : position + 2])
            position += 2
            continue
        if position + 4 > len(data):
            return data
        end = position + 2 + int.from_bytes(data[position + 2 : position + 4], "big")
        if end > len(data) or end < position + 4:
            return data
        if marker in _JPEG_DROPPED:
            if marker == 0xE1 and orientation_at is None:
                orientation_at = len(kept)
                kept.append(b"")  # where the orientation goes back
        else:
            kept.append(data[position:end])
        position = end
        if marker == 0xDA:  # a scan: its compressed data runs to the next marker
            following = _NEXT_MARKER.search(data, position)
            stop = following.start() if following else len(data)
            kept.append(data[position:stop])
            position = stop
    if orientation_at is not None:
        kept[orientation_at] = _orientation_segment(data)
    return b"".join(kept)


def _png_without_metadata(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        return data
    kept = [_PNG_SIGNATURE]
    position = len(_PNG_SIGNATURE)
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        end = position + 12 + length
        if end > len(data):
            return data
        name = data[position + 4 : position + 8]
        if name not in _PNG_DROPPED:
            kept.append(data[position:end])
        position = end
        if name == b"IEND":  # whatever follows is not part of the picture
            return b"".join(kept)
    return data  # no IEND: not a whole PNG, left alone


def without_metadata(extension: str, data: bytes) -> bytes:
    """The picture's bytes with its EXIF, XMP, IPTC, comments and text chunks cut out (#964).

    Not re-encoded: the compressed image is untouched, so it decodes to the same pixels. A JPEG
    keeps an APP1 holding only its orientation, so a photograph taken sideways is not shown
    sideways. Bytes that do not have the structure of the kind named are returned as they came,
    and so is anything that is not a PNG or a JPEG.
    """
    extension = extension.lower()
    if extension == "png":
        return _png_without_metadata(data)
    if extension in ("jpg", "jpeg"):
        return _jpeg_without_metadata(data)
    return data
