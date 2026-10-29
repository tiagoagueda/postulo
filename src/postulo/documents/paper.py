"""The paper a document is printed on: a size and an orientation (#344).

Every document Postulo drew was A4 portrait. The constant was in three places -- the
Chromium call, the Word file's section, the OpenDocument file's page layout -- and a fourth
in every theme, which wrote ``@page { size: A4 }`` itself. A CV meant for an employer in the
United States was A4 all the same, and a wide report (#341, #342) had no way to be printed
on its side.

**Four papers.** A4 and US Letter, each upright or on its side. More when somebody asks for
one: the vocabulary is a size and an orientation, so a size added here is four lines.

**Postulo writes the page rule, not the theme.** One ``@page { size: … }`` rule is written
into the document for the paper chosen (`with_paper`), last in its ``<head>``, so a theme
cannot disagree with what the person picked: a rule a theme wrote earlier loses to it, as
the later of two equal rules does. The theme is told the paper as well, as ``paper`` in its
context, so a fluid theme can adapt where it wants to -- a wider column, a smaller margin.
The rule carries a ``<meta>`` beside it, which is how a renderer that is handed a size
rather than reading the rule is told it (`read_from`, which the Chromium backend asks).

**A theme declares the papers it prints on** (`Theme.papers`), for every kind or kind by
kind: every paper (`ANY`, a *fluid* theme), or the ones it names. **A theme that declares
nothing is read as A4 portrait only**, which is true of every theme written before this:
they were drawn on nothing else and nobody can say how they look on anything else.

**Who chooses, in order** (`paper_choice`, which reads the records this module is below).
The instance's administrator sets a default under *Server settings → Defaults*
(`SiteSettings.default_paper`), and nothing set there is A4 portrait; a person sets their
own under *Settings → Export* (`Profile.paper`), and nothing set there is the instance's;
and each export may choose, among the papers its theme prints on. A
default the theme does not print on gives way to A4 portrait where the theme prints on
that, and to the theme's first paper where it does not: a default is a wish, and the
document is still drawn. **A choice made for one export is not a wish** -- somebody picked
it in the dialog -- so a paper the theme does not print on is refused, in a sentence that
says which it does (`CannotRender`), rather than quietly drawn on another.

**Not from the language.** A French CV is not necessarily printed in France, and a person in
Quebec writes in French on Letter. The administrator knows where the instance is used; a
language does not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db import models
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

from . import themes


class Size(models.TextChoices):
    """A sheet's size, before it is turned."""

    A4 = "a4", "A4"
    LETTER = "letter", _("US Letter")


class Orientation(models.TextChoices):
    PORTRAIT = "portrait", _("portrait")
    LANDSCAPE = "landscape", _("landscape")


#: Each size, upright, in millimetres: the width, then the height. US Letter is 8½ × 11
#: inches, exactly.
MILLIMETRES = {Size.A4: (210.0, 297.0), Size.LETTER: (215.9, 279.4)}

#: What CSS calls each size, in an ``@page`` rule's ``size``.
CSS_NAMES = {Size.A4: "A4", Size.LETTER: "letter"}

#: Each paper in words, whole, so that a translator sees the phrase rather than two halves of
#: it put together in an order their language may not use.
LABELS = {
    (Size.A4, Orientation.PORTRAIT): _("A4, portrait"),
    (Size.A4, Orientation.LANDSCAPE): _("A4, landscape"),
    (Size.LETTER, Orientation.PORTRAIT): _("US Letter, portrait"),
    (Size.LETTER, Orientation.LANDSCAPE): _("US Letter, landscape"),
}

#: What a fluid theme declares: every paper Postulo has, and any it gains later.
ANY = "any"

#: The longest key, which is what a column holding one is sized for.
MAX_KEY_LENGTH = 20

#: The ``<meta>`` that tells a renderer the paper, beside the rule that tells CSS.
META_NAME = "postulo-paper"


@dataclass(frozen=True)
class Paper:
    """One paper: a size, turned one way."""

    size: str
    orientation: str = Orientation.PORTRAIT

    @property
    def key(self) -> str:
        """``a4-portrait``, ``letter-landscape``: what a form posts and a column keeps."""
        return f"{self.size}-{self.orientation}"

    @property
    def landscape(self) -> bool:
        return self.orientation == Orientation.LANDSCAPE

    @property
    def width_mm(self) -> float:
        width, height = MILLIMETRES[self.size]
        return height if self.landscape else width

    @property
    def height_mm(self) -> float:
        width, height = MILLIMETRES[self.size]
        return width if self.landscape else height

    @property
    def css(self) -> str:
        """The value of an ``@page`` rule's ``size``: ``A4 portrait``, ``letter landscape``."""
        return f"{CSS_NAMES[self.size]} {self.orientation}"

    @property
    def label(self):
        return LABELS[(self.size, self.orientation)]

    def __str__(self) -> str:
        return str(self.label)


A4_PORTRAIT = Paper(Size.A4, Orientation.PORTRAIT)

#: Every paper, in the order a picker lists them.
EVERY: tuple[Paper, ...] = tuple(
    Paper(size, orientation) for size in Size.values for orientation in Orientation.values
)

#: What nothing chosen anywhere is, and what a theme that declares nothing prints on.
DEFAULT = A4_PORTRAIT

#: Every paper as a form's choices, which is also what the two columns that keep one accept.
CHOICES = [(paper.key, paper.label) for paper in EVERY]


def find(key) -> Paper | None:
    """The paper ``key`` names, or ``None`` for anything else."""
    if not isinstance(key, str):
        return None
    for paper in EVERY:
        if paper.key == key:
            return paper
    return None


def choices(papers: Iterable[Paper] = EVERY) -> list[tuple[str, str]]:
    return [(paper.key, str(paper.label)) for paper in papers]


def listed(papers: Iterable[Paper]) -> str:
    """Papers in words, for a sentence: ``A4, portrait; US Letter, portrait``."""
    return "; ".join(str(paper.label) for paper in papers)


# ------------------------------------------------------------------- what a theme prints on


def declared(theme, kind: str) -> tuple[Paper, ...]:
    """The papers ``theme`` prints ``kind`` on, in the order a picker lists them.

    ``Theme.papers`` is a list for every kind, or a mapping of kind to list; each entry a
    paper's key or `ANY`. Nothing declared for the kind -- and a key Postulo does not know,
    which a plugin written against a later Postulo may name -- is A4 portrait, because that
    is what every theme was drawn on before there was a choice.
    """
    held = getattr(theme, "papers", None) or ()
    if isinstance(held, Mapping):
        held = held.get(kind) or ()
    if isinstance(held, str):
        held = (held,)
    if ANY in held:
        return EVERY
    named = {key for key in held if isinstance(key, str)}
    return tuple(paper for paper in EVERY if paper.key in named) or (DEFAULT,)


def offered(theme_name: str, kind: str) -> tuple[Paper, ...]:
    """The papers a document set in ``theme_name`` may be printed on.

    The theme that actually draws it: an unknown name is drawn by `plain`
    (`themes.drawn_by`), so it is offered what `plain` prints on.
    """
    theme = themes.find(theme_name) or themes.find(themes.DEFAULT)
    return declared(theme, kind) if theme is not None else (DEFAULT,)


def restriction(theme_name: str, kind: str) -> str:
    """Why the picker offers fewer than every paper, in a sentence, or ``""`` where it does
    not. Said beside the picker, so that a missing paper is a fact about the theme rather
    than something the person has to guess at."""
    papers = offered(theme_name, kind)
    if len(papers) == len(EVERY):
        return ""
    return str(
        _("The %(theme)s theme prints this only on: %(papers)s.")
        % {"theme": themes.label_for(themes.drawn_by(theme_name)), "papers": listed(papers)}
    )


# ------------------------------------------------------------------- the page rule


def page_rule(paper: Paper, nonce=None) -> str:
    """The markup `with_paper` writes: the ``<meta>`` and the ``@page`` rule."""
    attribute = f' nonce="{escape(nonce)}"' if nonce else ""
    return (
        f'<meta name="{META_NAME}" content="{paper.key}">'
        f"<style{attribute}>@page {{ size: {paper.css}; }}</style>"
    )


#: Where the rule goes: before the end of the head, or failing one, before the body.
_HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY = re.compile(r"<body[\s>]", re.IGNORECASE)


def with_paper(html: str, paper: Paper, *, nonce=None) -> str:
    """``html`` with Postulo's page rule for ``paper`` written into it.

    Last in the head, after every rule the theme wrote, so that the paper is the one chosen
    whatever the theme says. The preview is the same HTML and its policy refuses a
    ``<style>`` without the request's nonce, so the nonce goes on this one as on the theme's.
    """
    rule = page_rule(paper, nonce)
    for pattern in (_HEAD_END, _BODY):
        found = pattern.search(html)
        if found is not None:
            return html[: found.start()] + rule + html[found.start() :]
    return rule + html


_META = re.compile(
    r'<meta\s+name="' + re.escape(META_NAME) + r'"\s+content="([a-z0-9-]+)"\s*/?>',
    re.IGNORECASE,
)


def read_from(html: str) -> Paper | None:
    """The paper `with_paper` wrote into ``html``, or ``None``: the last one, which is
    Postulo's own where a theme wrote one of its own before it."""
    found = _META.findall(html)
    return find(found[-1]) if found else None
