"""What a file says about itself: its properties, the person's to see, edit or leave out (#480).

A PDF, a Word file and an OpenDocument file each carry a title, an author, a subject, some
keywords and a language beside the words. Until now they were written without asking: the
title and the author came from the document, and nobody could see them, change them, or
send a file with their name left out of it.

**One set of fields, written to each format in its own way.** The PDF is given them through
the page's `<title>` and `<meta>` elements, which is how WeasyPrint reads them; a Word file
through `docProps/core.xml`; an OpenDocument file through `meta.xml`. Plain text has none.

**"Without" has a floor.** PDF/UA requires a title and a language in the file, because that
is what a screen reader announces, and taking them out makes the file worse for the people
the standard exists for. So a file without its properties has no author, no subject, no
keywords and nothing that names the person in its title -- a neutral one, the kind of
document it is -- and keeps its language. No date and no producing software is written by
Postulo's own writers; the PDF renderer adds its own name where it adds one, and says so.

**Everything typed is text in somebody else's XML or PDF.** It is cut to a length here and
escaped where it is written (`docx.clean`, `escape`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from django.utils.translation import gettext_lazy as _

from postulo.core import languages

from .outline import Outline

#: How much of each a file is given. A title is a line, and keywords are a short list: a
#: viewer shows the first and truncates the rest, and an applicant tracking system reading a
#: field of ten thousand words is being fed something that is not a keyword.
MAX_TITLE = 200
MAX_AUTHOR = 200
MAX_SUBJECT = 300
MAX_KEYWORDS = 300

#: What the choice is called in a form and in the query string of a download.
FIELD_NAMES = ("title", "author", "subject", "keywords", "language")
WITH_FIELD = "with_properties"

#: What a file kept without its properties still says, said on the page where the choice is
#: made (#480).
WHAT_IS_KEPT = _(
    "Without them the file has no author, subject, keywords or dates, and a neutral title "
    "instead of your name. It keeps its language and a title, because a screen reader "
    "announces both and the PDF standard for accessibility requires them. The program that "
    "draws a PDF may still name itself."
)

KEYWORDS_HELP = _(
    "A few words a search of your own files could use, separated by commas. Stuffing them "
    "does not help with applicant tracking software, and can count against an application."
)


def _tidy(text, limit: int) -> str:
    """One line, without what XML cannot carry, and no longer than a field is allowed."""
    from .docx import clean

    return " ".join(clean(str(text or "")).split())[:limit]


@dataclass(frozen=True)
class Properties:
    """What was chosen for one export: whether to carry the properties, and any edits.

    A blank field means *the document's own value*, which is what every field has as its
    default. Nothing here is stored on the document: it is what this one file is given.
    """

    include: bool = True
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    language: str = ""

    @classmethod
    def from_data(cls, data: Mapping | None) -> Properties:
        """Read the choice from a query string, a form or the JSON of an errand.

        ``with_properties`` is on unless it says otherwise, so a link from before this
        existed, and a client that has never heard of it, get what they always got. A
        language that is not a well-formed tag is dropped: it would go into a file's
        `lang` and a viewer would announce it.
        """
        data = data or {}
        raw = data.get(WITH_FIELD)
        include = str(raw).strip().lower() not in {"0", "false", "off", "no"} if raw else True
        language = _tidy(data.get("language"), 35)
        return cls(
            include=include,
            title=_tidy(data.get("title"), MAX_TITLE),
            author=_tidy(data.get("author"), MAX_AUTHOR),
            subject=_tidy(data.get("subject"), MAX_SUBJECT),
            keywords=_tidy(data.get("keywords"), MAX_KEYWORDS),
            language=language if languages.well_formed(language) else "",
        )

    def as_data(self) -> dict:
        """What an errand carries and `from_data` reads back."""
        return {
            WITH_FIELD: "1" if self.include else "0",
            **{name: getattr(self, name) for name in FIELD_NAMES},
        }

    @property
    def edited(self) -> bool:
        return any(getattr(self, name) for name in FIELD_NAMES)


#: The choice nobody made: today's behaviour.
DEFAULT = Properties()


def apply(outline: Outline, chosen: Properties, *, neutral_title: str) -> Outline:
    """The outline as the file should describe itself, under this choice.

    Without properties the title is the neutral one and the author, subject and keywords
    are empty; whatever was typed beside the choice is ignored, because a person who said
    *without* did not mean *except for what I typed*. The language stays (see the note at
    the top).
    """
    if not chosen.include:
        return replace(
            outline,
            title=neutral_title or outline.title,
            author="",
            subject="",
            keywords="",
        )
    return replace(
        outline,
        title=chosen.title or outline.title,
        author=chosen.author or outline.author,
        subject=chosen.subject,
        keywords=chosen.keywords,
        language=chosen.language or outline.language,
    )
