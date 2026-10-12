"""The words in a Word or OpenDocument file, so that a search can find them (#877).

**This reads text, not a CV.** A CV or a letter typed in another program has no structure
Postulo can read reliably -- what is a heading, what is a job, where one ends and the next
starts -- and guessing would put wrong things into a career record that people send to
employers. Structured import stays with the formats that have a structure: Europass, and the
candidate file. A `.docx` or `.odt` that is uploaded stays what it is, a file, kept exactly as
it arrived; what this adds is the plain text of its body, kept beside it, so that a word from
inside a document finds the document.

**Safe to point at a hostile file.** A `.docx` and an `.odt` are zip archives of XML. The
archive is opened without extracting it, one member is read and only up to a ceiling, the XML is
parsed with `defusedxml` (no entities, no external references, no DOCTYPE), and the text kept
is capped. A file that is not what its name says, is encrypted, is too large when unpacked or is
not well-formed yields nothing: the upload still succeeds, and has no searchable text.
"""

from __future__ import annotations

import logging
import zipfile
from typing import IO

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as parse_xml

logger = logging.getLogger(__name__)

#: The most of the unpacked body that is read. A letter or a CV is a few hundred kilobytes of
#: XML at the very most; anything past this is a zip bomb or not a document.
MAX_BODY_BYTES = 20 * 1024 * 1024
#: The most text that is kept: enough to search a long document, small enough that a row holding
#: it is not the biggest thing in the database.
MAX_TEXT_CHARS = 200_000

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

#: Which member of the archive holds the body, and which format reads it.
EXTENSIONS = {"docx": "word/document.xml", "odt": "content.xml"}


def format_of(filename: str) -> str:
    """``docx``, ``odt`` or nothing, from a file's name."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return extension if extension in EXTENSIONS else ""


def text_of(handle: IO[bytes], extension: str) -> str:
    """The plain text of a `.docx` or `.odt` held open, or nothing if it cannot be read."""
    member = EXTENSIONS.get(extension)
    if member is None:
        return ""
    try:
        with zipfile.ZipFile(handle) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_BODY_BYTES or info.flag_bits & 0x1:
                return ""
            with archive.open(info) as body:
                data = body.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            return ""
        root = parse_xml(data)
    except (zipfile.BadZipFile, KeyError, OSError, ParseError, DefusedXmlException, ValueError):
        return ""
    except Exception:  # pragma: no cover - a reader of hostile files must not raise
        logger.exception("A %s file could not be read", extension)
        return ""
    return _collect(root, extension)[:MAX_TEXT_CHARS]


def _collect(root, extension: str) -> str:
    """Paragraph by paragraph, one per line, with tabs and breaks as the spaces they are."""
    lines = _word_lines(root) if extension == "docx" else _opendocument_lines(root)
    return chr(10).join(lines)


def _word_lines(root) -> list[str]:
    lines = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{WORD_NS}tab", f"{WORD_NS}br"):
                parts.append(" ")
        if line := "".join(parts).strip():
            lines.append(line)
    return lines


def _opendocument_lines(root) -> list[str]:
    lines = []
    for node in root.iter():
        if node.tag in (f"{TEXT_NS}p", f"{TEXT_NS}h"):
            if line := "".join(node.itertext()).strip():
                lines.append(line)
    return lines
