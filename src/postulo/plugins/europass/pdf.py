"""Reaching the file a PDF carries attached, without a PDF library.

europass.europa.eu hands a CV over as a PDF and nothing else ("Currently, you can save your
Europass CV in a Europass PDF version", its FAQ), and the CV itself travels inside that PDF:
the Candidate XML is attached to the document as an embedded file. The Commission's
interoperability page said as much in 2020 ("the option to receive the document in PDF
format with the XML attached"), and people who have downloaded one since describe a single
attachment called ``attachment.xml`` (#244).

**Why no library.** Reaching an attachment in general means reading a PDF's structure --
the cross-reference table, the catalogue's name tree, object streams -- and that is a parser
Postulo does not have, and a dependency it would have to trust with a stranger's file. The
case that matters needs none of it. An attached file is a *stream* whose dictionary says
``/Type /EmbeddedFile``, and a stream may never be stored inside an object stream (ISO
32000-1, 7.5.7), so that dictionary is always in the file as written, however much of the
rest was compressed. This module finds those dictionaries, takes the bytes after
``stream``, and inflates them.

**It does not guess.** An encrypted PDF, a filter other than Flate, a stream it cannot
delimit: each is a :class:`Refused` carrying its reason, which the reader turns into words
for the person who chose the file. Nothing here is user-facing, so nothing here is
translated.

**Bounded.** At most :data:`MAX_ATTACHMENTS` are looked at, and each is inflated under a hard
cap on what comes *out*: a few kilobytes of deflate can unpack to gigabytes, and the cap
stops at the first byte over it rather than after the damage. What comes out is handed back
as the second file it is, and the reader puts it through every guard an upload gets.
"""

from __future__ import annotations

import re
import zlib

#: How many attached files are looked at. A Europass PDF carries one.
MAX_ATTACHMENTS = 8

#: How many ``/Type /EmbeddedFile`` markers are looked at, attachments or not. Each costs a
#: look either side of it, so a file made of nothing but markers must not cost that look
#: once per marker.
MAX_MARKERS = 4 * MAX_ATTACHMENTS

#: How far either side of ``/Type /EmbeddedFile`` the object's header and its ``stream``
#: keyword may be. The dictionary of an embedded file is a handful of short entries; one
#: longer than this is not one understood.
DICTIONARY_REACH = 4096

# The reasons, which the reader words. Strings rather than subclasses, because the only
# thing anybody does with one is choose a sentence.
ENCRYPTED = "encrypted"
FILTER = "filter"
DAMAGED = "damaged"
TOO_LARGE = "too-large"

#: A PDF name ends at whitespace or at a delimiter (ISO 32000-1, 7.2.2 and 7.3.5), which is
#: what keeps ``/EmbeddedFiles`` -- the catalogue's name tree -- from matching.
_NAME_ENDS = rb"(?![^\s/<>\[\]()%{}])"
_EMBEDDED_FILE = re.compile(rb"/Type\s*/EmbeddedFile" + _NAME_ENDS)
_OBJECT_HEADER = re.compile(rb"\d{1,10}\s+\d{1,5}\s+obj" + _NAME_ENDS)
#: The keyword that ends a stream's dictionary, with the end of line after it. Anchored on
#: the ``>>`` it follows, so that a name such as ``/application#2Foctet-stream`` written
#: at the end of a line is not taken for it, and neither is ``endstream``.
_STREAM = re.compile(rb">>\s*stream(?:\r\n|\n|\r)")
#: A trailer, or a cross-reference stream standing in for one, that points at encryption.
_ENCRYPT = re.compile(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)")
_FILTER = re.compile(rb"/Filter\s*(\[[^\]]{0,512}\]|/[^\s/<>\[\]()%{}]{1,64}|\d+\s+\d+\s+R)")
_FILTER_NAME = re.compile(rb"/([^\s/<>\[\]()%{}]{1,64})")
_PREDICTOR = re.compile(rb"/Predictor\s+(\d{1,3})")
#: A length written in place. ``/Length 12 0 R`` points elsewhere and is not one.
_LENGTH = re.compile(rb"/Length\s+(\d{1,10})(?!\d)(?!\s+\d+\s+R)")


class Refused(Exception):
    """Why the attached file could not be reached. The reader says it in words."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def is_pdf(data: bytes) -> bool:
    """Whether these bytes are a PDF, by the header every PDF starts with."""
    return data[:1024].lstrip(b"\xef\xbb\xbf").lstrip().startswith(b"%PDF-")


def attachments(data: bytes, *, limit: int) -> list[bytes]:
    """Every file attached to the PDF, unpacked, in the order the PDF holds them.

    Each is at most ``limit`` bytes once unpacked; one that is larger refuses the whole
    PDF. An empty list means the PDF carries nothing, which is for the caller to say.
    """
    if _ENCRYPT.search(data):
        # Encryption covers the streams, so every attachment would come out as noise; and
        # the password that opens it is not Postulo's to ask for.
        raise Refused(ENCRYPTED)
    found: list[bytes] = []
    for looked, marker in enumerate(_EMBEDDED_FILE.finditer(data), start=1):
        stream = _stream_after(data, marker.start(), marker.end())
        if stream is not None:
            dictionary, start = stream
            found.append(_unpack(data, dictionary, start, limit))
        if len(found) >= MAX_ATTACHMENTS or looked >= MAX_MARKERS:
            break
    return found


def _stream_after(data: bytes, at: int, after: int) -> tuple[bytes, int] | None:
    """The dictionary around ``/Type /EmbeddedFile`` and where its stream's bytes begin.

    Nothing, when the marker turns out not to be in a stream's dictionary at all: the
    object ends before any ``stream`` does, or no object header precedes it closely enough
    to say whose dictionary this is.
    """
    # The closest header before the marker is the marker's object's own.
    headers = list(_OBJECT_HEADER.finditer(data, max(0, at - DICTIONARY_REACH), at))
    if not headers:
        return None
    header = headers[-1]
    reach = after + DICTIONARY_REACH
    keyword = _STREAM.search(data, after, reach)
    if keyword is None:
        return None
    end_of_object = data.find(b"endobj", after, reach)
    if end_of_object != -1 and end_of_object < keyword.start():
        return None
    dictionary = data[header.end() : keyword.start() + 2]
    if (
        len(dictionary) > 2 * DICTIONARY_REACH
        or b"endstream" in dictionary
        or b"endobj" in dictionary
    ):
        return None
    return dictionary, keyword.end()


def _filters(dictionary: bytes) -> list[str]:
    written = _FILTER.search(dictionary)
    if written is None:
        return []
    value = written.group(1)
    if value.rstrip().endswith(b"R"):
        # A filter kept in another object, which would mean resolving references -- the
        # parser this module exists not to be.
        raise Refused(FILTER, "an indirect /Filter")
    return [name.decode("latin-1") for name in _FILTER_NAME.findall(value)]


def _unpack(data: bytes, dictionary: bytes, start: int, limit: int) -> bytes:
    filters = _filters(dictionary)
    if filters not in ([], ["FlateDecode"]):
        raise Refused(FILTER, " ".join(filters))
    predictor = _PREDICTOR.search(dictionary)
    if predictor is not None and int(predictor.group(1)) > 1:
        raise Refused(FILTER, "FlateDecode with a predictor")

    length = _LENGTH.search(dictionary)
    if length is not None and start + int(length.group(1)) <= len(data):
        raw = data[start : start + int(length.group(1))]
    else:
        # No length in place: the bytes run to ``endstream``, less the end of line that
        # separates them from it. Inflating ignores anything after the end of its stream.
        end = data.find(b"endstream", start)
        if end == -1:
            raise Refused(DAMAGED)
        raw = data[start:end]
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith((b"\n", b"\r")):
            raw = raw[:-1]

    if not filters:
        if len(raw) > limit:
            raise Refused(TOO_LARGE)
        return raw
    return _inflate(raw, limit)


def _inflate(raw: bytes, limit: int) -> bytes:
    """Flate, stopping at the first byte over ``limit`` rather than after the last one."""
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(raw, limit + 1)
    except zlib.error as error:
        raise Refused(DAMAGED) from error
    if len(out) > limit:
        raise Refused(TOO_LARGE)
    return out
