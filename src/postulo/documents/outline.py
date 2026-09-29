"""A document as its words: the outline every format is handed (#236).

Headings, paragraphs and lists, in the order the document says them, in the language it
declares. `formats` writes one as text and keeps the registry of formats; `docx` writes one
as a Word file, and is itself one of the formats that registry offers -- which is why the
outline is a module of its own, below both, rather than the top of `formats`, where `docx`
reading it and `formats` registering `docx` were a pair of imports (#248). `formats` hands
out every name here as well.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The three things an outline is made of.
HEADING = "heading"
PARAGRAPH = "paragraph"
BULLETS = "bullets"

#: What a bullet is written with in plain text. A hyphen rather than a typographic bullet:
#: this is the text that gets pasted into somebody else's form, and a form that mangles
#: anything mangles what is not ASCII first.
BULLET = "- "


@dataclass(frozen=True)
class Block:
    """One piece of a document: a heading, a paragraph, or a list."""

    kind: str
    text: str = ""
    #: 1 for the document's own name, 2 for a section, 3 for an entry inside one. The same
    #: three levels the themes draw as `<h1>`, `<h2>` and `<h3>`, so a Word file has the
    #: outline the PDF's tag tree has.
    level: int = 0
    items: tuple[str, ...] = ()
    #: Whether this stands apart from what is above it, where nothing else says so. A
    #: heading below the first always does; a paragraph does when it opens a new part of
    #: the page, as the summary does under the contact details.
    apart: bool = False


def heading(text: str, level: int) -> Block:
    return Block(kind=HEADING, text=text, level=level)


def paragraph(text: str, *, apart: bool = False) -> Block:
    return Block(kind=PARAGRAPH, text=text, apart=apart)


def bullets(items) -> Block:
    return Block(kind=BULLETS, items=tuple(items))


@dataclass(frozen=True)
class Outline:
    """A document as its words: what every format is handed.

    ``title`` is what a viewer shows for the file and what it is called on the way out;
    ``author`` is empty where the person left their name off the document, because a name
    taken off the page was not meant to stay in the file's properties.
    """

    title: str
    language: str
    direction: str = "ltr"
    author: str = ""
    blocks: tuple[Block, ...] = ()
