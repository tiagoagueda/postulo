"""What a document can leave as, beside the PDF, said once (#236).

A PDF is the document as somebody reads it, and it is the wrong thing to hand to a form.
Applicant tracking systems and public-sector portals ask for a Word file or for text pasted
into a box, and a CV that exists only as a PDF is retyped into each of them by hand.

**A format is given the words, not the page.** A theme sets a document for reading: it is
markup, a stylesheet and a renderer. What a portal wants is the opposite -- the same claims
with the setting taken off -- so a format is handed an `Outline`: headings, paragraphs and
lists, in the order the document says them, in the language the document declares. It never
sees the theme, and a theme never sees it.

**The registry is `kinds.py`'s, for the reason that one gives.** A key already taken is
refused rather than overridden, Postulo's own are registered as the app loads, and a second
implementation is a call to `register` rather than a branch in a view. Plain text, OpenDocument
and Word are Postulo's; anything a portal invents next arrives the same way, and a plugin
registers one through `postulo.plugins.api` (#480).

**The core stays dependency-light.** The formats here are written with the standard
library: a text file is a string, and a `.docx` (`docx.py`) and an `.odt` (`odt.py`) are zips
of XML parts. A
library that reads and writes every corner of a format is the right tool for a program
that edits documents, and this one only ever writes a list of paragraphs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

# What an outline is made of lives in `outline`, which `docx` reads without reaching the
# registry below (#248). Handed out here too, where the renderer and the views look for it.
from .outline import (  # noqa: F401 - re-exported: rendering, views
    BULLET,
    BULLETS,
    HEADING,
    PARAGRAPH,
    Block,
    Outline,
    bullets,
    heading,
    paragraph,
)

logger = logging.getLogger(__name__)


def as_text(outline: Outline) -> str:
    """The outline as plain text: one line a line, a blank one between the parts.

    Headings are written as they stand. Capitals or a rule of hyphens underneath would be
    typography, and typography is what this format is for leaving behind -- besides which
    upper-casing is English's idea of emphasis, and misspells a word in Turkish and German.
    """
    lines: list[str] = []

    def stand_apart() -> None:
        if lines and lines[-1]:
            lines.append("")

    for block in outline.blocks:
        if block.kind == HEADING:
            if not block.text.strip():
                continue
            if block.level > 1:
                stand_apart()
            lines.append(block.text.strip())
            if block.level == 2:
                lines.append("")
        elif block.kind == BULLETS:
            lines.extend(f"{BULLET}{item.strip()}" for item in block.items if item.strip())
        else:
            if not block.text.strip():
                continue
            if block.apart:
                stand_apart()
            lines.extend(line.rstrip() for line in block.text.strip().splitlines())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def write_text(outline: Outline) -> bytes:
    """A text file: UTF-8, no byte-order mark, and a newline at the end of the last line.

    No mark, because what reads this is as likely to be a portal's upload box as an editor,
    and a parser that does not expect one shows it as three stray characters before the
    person's name.
    """
    return (as_text(outline) + "\n").encode("utf-8")


# ------------------------------------------------------------------ the registry


@dataclass(frozen=True)
class Format:
    """One format a document can be downloaded in."""

    #: What goes in the address, and what the registry is keyed by.
    key: str
    label: object
    #: Without the dot. What the downloaded file is called by, and what the button says.
    extension: str
    content_type: str
    write: Callable[[Outline], bytes]
    #: Who provides it, in words. Empty for Postulo's own.
    provider: str = ""
    #: Whether the file carries properties -- a title, an author -- that the person may
    #: leave out or edit (#480). Plain text has none; a plugin's format says so if it does,
    #: and is handed an outline already carrying the choice.
    has_properties: bool = False


#: Every format, in the order a page should offer them.
REGISTRY: dict[str, Format] = {}


def register(offered: Format) -> bool:
    """Offer a format. Returns whether it was taken.

    A key already in use is refused rather than overridden, as a kind's is and a theme's
    is: a plugin able to replace `docx` could change what every Word file Postulo hands
    over contains, without anybody having chosen that.
    """
    if not offered.key or not offered.extension:
        logger.warning("A document format with no key or no extension was ignored")
        return False
    if offered.key in REGISTRY:
        logger.warning("Document format %r was offered twice; the first one keeps it", offered.key)
        return False
    REGISTRY[offered.key] = offered
    return True


def forget(key: str = "") -> None:
    """Drop a plugin's format, or every format. For tests and for uninstalling."""
    if key:
        REGISTRY.pop(key, None)
    else:
        REGISTRY.clear()


def get(key: str) -> Format | None:
    return REGISTRY.get(key)


def all_formats() -> list[Format]:
    """What a page offers, Postulo's own first."""
    return list(REGISTRY.values())


def register_the_ones_postulo_has() -> None:
    """Postulo's own formats, described once.

    Called from `DocumentsConfig.ready`, and idempotent, for the reason
    `kinds.register_the_ones_postulo_has` gives: a registry filled at import time is empty
    in whichever test imported the module first.
    """
    from . import docx, odt

    described = (
        Format(
            key="txt",
            label=_("Plain text"),
            extension="txt",
            content_type="text/plain; charset=utf-8",
            write=write_text,
        ),
        Format(
            key="odt",
            label=_("OpenDocument text"),
            extension="odt",
            content_type=odt.CONTENT_TYPE,
            write=odt.write,
            has_properties=True,
        ),
        Format(
            key="docx",
            label=_("Word document"),
            extension="docx",
            content_type=docx.CONTENT_TYPE,
            write=docx.write,
            has_properties=True,
        ),
    )
    for one in described:
        if one.key not in REGISTRY:
            register(one)
