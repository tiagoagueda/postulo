"""A recipe per board, for the boards that publish nothing a standard can read.

Most large boards embed a schema.org ``JobPosting`` and are read by the standard, which is
where every capture should come from: the site maintains it, and it survives a redesign.
Some publish nothing at all. LinkedIn is the plainest case -- no JSON-LD, no microdata, and
the company, the place and the advert itself marked only with class names -- so a reader
holding to standards alone comes back with a title and six empty fields.

A recipe is a board's own answer written down: which hosts it is for, and where on that
board each field lives. It is a *supplement* to the standards and never a replacement. The
order is recipe first and the standard filling whatever the recipe left empty, so a board
that starts publishing JSON-LD improves without anybody editing its recipe, and a recipe
that rots because a board redesigned costs the fields it used to fill rather than the
capture.

**A recipe states only what it is sure of.** Leaving a field empty puts it in front of the
person reviewing the capture, which is a small cost; filling it wrongly puts a wrong value
into their records quietly, which is the thing this whole file exists to avoid. No recipe
guesses, and none should be written against a board whose markup nobody has read -- selectors
invented from memory are the exact fault this is here to fix.

Adding one: write the module, add it to `BOARDS`, and add a page of that board to
``tests/fixtures/postings/`` with what should come out of it. A recipe with no fixture is a
recipe nobody can tell has stopped working.

The browser extension carries the same recipes, at ``postulo-chromium/src/lib/boards.js``,
because the extension shows somebody what was read before it is sent. The capture itself is
read again here, so a recipe that exists only in this file still corrects the record -- but
the preview would disagree with it, which is its own kind of wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from ..htmlutil import Element
from . import greenhouse, linkedin


@dataclass(frozen=True)
class Board:
    """One board's recipe: the hosts it speaks for, and how to read a page of it."""

    name: str
    hosts: tuple[str, ...]
    read: Callable[[Element, str], dict]

    def handles(self, host: str) -> bool:
        return any(host == known or host.endswith(f".{known}") for known in self.hosts)


#: Every recipe, in the order they are tried. One board matches, or none does.
BOARDS: tuple[Board, ...] = (
    Board("linkedin", linkedin.HOSTS, linkedin.read),
    Board("greenhouse", greenhouse.HOSTS, greenhouse.read),
)


def recipe_for(url: str) -> Board | None:
    """The recipe for this address, if a board claims it."""
    try:
        host = urlparse(url).netloc.split(":")[0].lower()
    except ValueError:
        return None
    if not host:
        return None
    for board in BOARDS:
        if board.handles(host):
            return board
    return None
