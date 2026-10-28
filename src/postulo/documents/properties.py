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

**A default for every file, under Settings → Export (#878).** For each field the person says
whether a file carries the document's own value (what it always did), a value of their own,
or nothing; and one switch, *Strip all metadata*, leaves out everything but the language. The
language stays unless that field is turned off as well, and the page says why: a screen reader
reads the file in it, and PDF/UA requires it. `Policy` is that choice and `resolve` the one
place the layers meet: the document's values, then the default, then whatever was typed for
one file in the export dialog -- which still goes over the default, as it went over the
document's value before there was one -- and *strip all* last, because it is a promise about
every file. Stripping also takes the PDF renderer's own name out of a PDF (`pdf.finish`).

A language left out leaves the file's *properties*, not its text: a Word file still marks its
paragraphs as being in it and a PDF is still drawn with it -- the hyphenation, the fonts a
script needs -- and only the `dc:language` and the PDF's `/Lang` are gone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from django.db import models
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

#: Why *Strip all metadata* keeps the language, said where the language is chosen (#878).
LANGUAGE_KEPT = _(
    "A screen reader reads a file in the language it declares, and the PDF standard for "
    "accessibility, PDF/UA, requires one. So stripping all metadata keeps the language "
    "unless you choose to leave it out here as well."
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


class MetadataSource(models.TextChoices):
    """Where one property of an exported file comes from, by default (#878)."""

    DOCUMENT = "document", _("From the document")
    FIXED = "fixed", _("Always this")
    OMIT = "omit", _("Leave it out")


#: What *Strip all metadata* takes out. Not the language: see the note at the top.
STRIPPED = ("title", "author", "subject", "keywords")

#: What a page tells the PDF renderer to take out of the file once it is drawn, as the content
#: of a `<meta>` element of this name (`pdf.finish`), in words from `SCRUB_WORDS`. WeasyPrint
#: reads a `<meta>` it does not know into its "custom" metadata, which it writes into a PDF
#: only when asked to, and Postulo never asks; so the element reaches the renderer and goes
#: no further.
SCRUB_META = "postulo-scrub"
#: ``producer``: the renderer's name, and a program and dates where it writes them;
#: ``language``: the `/Lang` the page's `lang` puts in the file.
SCRUB_WORDS = ("producer", "language")


@dataclass(frozen=True)
class Policy:
    """What a person's Settings → Export say every exported file carries (#878).

    ``rules`` is a ``(field, source, value)`` triple for each of `FIELD_NAMES`; a field it
    does not name comes from the document. ``strip`` is *Strip all metadata*.
    """

    strip: bool = False
    rules: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def of(cls, owner) -> Policy:
        """The policy on ``owner``'s profile, or the one nobody chose where there is none."""
        profile = getattr(owner, "profile", None) if owner is not None else None
        if profile is None:
            return NO_POLICY
        return cls(
            strip=bool(getattr(profile, "export_strip_metadata", False)),
            rules=tuple(
                (
                    name,
                    getattr(profile, f"export_{name}_source", MetadataSource.DOCUMENT),
                    getattr(profile, f"export_{name}", ""),
                )
                for name in FIELD_NAMES
            ),
        )

    def default_for(self, name: str, found: str) -> str:
        """What the field says by default, given what the document says (``found``).

        A fixed value is read through the same bounds as one typed in the export dialog, so a
        row written some other way -- an archive, the shell -- is held to them too, and a
        fixed language that is not a tag is no language rather than a `lang` a viewer reads.
        """
        for field, source, value in self.rules:
            if field != name:
                continue
            if source == MetadataSource.OMIT:
                return ""
            if source == MetadataSource.FIXED:
                return getattr(Properties.from_data({name: value}), name)
        return found


#: The policy of somebody who has never opened Settings → Export: today's behaviour.
NO_POLICY = Policy()


def resolve(
    found: Mapping[str, str],
    chosen: Properties,
    policy: Policy = NO_POLICY,
    *,
    neutral_title: str = "",
) -> dict[str, str]:
    """What a file carries for each of `FIELD_NAMES`, empty where it carries nothing.

    ``found`` is what the document says for each; ``chosen`` what the export dialog said for
    this one file; ``policy`` the person's default. Without properties (`chosen.include`
    false) the title is the neutral one -- none, where the default leaves the title out --
    and the author, subject and keywords are empty whatever was typed: somebody who said
    *without* did not mean *except for what I typed*.
    """
    resolved = {name: policy.default_for(name, found.get(name) or "") for name in FIELD_NAMES}
    if not chosen.include:
        title = (neutral_title or resolved["title"]) if resolved["title"] else ""
        resolved.update(title=title, author="", subject="", keywords="")
    else:
        resolved.update(
            {name: getattr(chosen, name) for name in FIELD_NAMES if getattr(chosen, name)}
        )
    if policy.strip:
        resolved.update(dict.fromkeys(STRIPPED, ""))
    return resolved


def scrubbed(resolved: Mapping[str, str], policy: Policy) -> str:
    """What the PDF renderer is told to take out of the file, as `SCRUB_META` says it."""
    words = []
    if policy.strip:
        words.append("producer")
    if not resolved.get("language"):
        words.append("language")
    return " ".join(words)


def apply(
    outline: Outline, chosen: Properties, *, neutral_title: str, policy: Policy = NO_POLICY
) -> Outline:
    """The outline as the file should describe itself, under this choice and this default.

    A language left out of the properties stays the language of the text: the outline keeps
    it and says it is not declared (`Outline.declares_language`), so the writers leave it out
    of the properties and still set the paragraphs in it. A file whose title was left out is
    still called something on the way out: the neutral title (`Outline.file_name`).
    """
    resolved = resolve(
        {name: getattr(outline, name) for name in FIELD_NAMES},
        chosen,
        policy,
        neutral_title=neutral_title,
    )
    return replace(
        outline,
        title=resolved["title"],
        author=resolved["author"],
        subject=resolved["subject"],
        keywords=resolved["keywords"],
        language=resolved["language"] or outline.language,
        declares_language=bool(resolved["language"]),
        file_name=resolved["title"] or neutral_title or outline.title,
    )
