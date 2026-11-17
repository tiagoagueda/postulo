"""A letter as the writing checkers read it, the one list of what they found, and putting a
suggested replacement in (#1258).

`core.writing_checks` is the mechanism and knows no model. This module is where a letter
becomes the plain `Document` a checker is handed, where what the checkers said becomes the
list a page draws, and where one replacement is put into the stored text.

**The text is the person's own, as stored.** Placeholders are not filled in the text a
checker reads; each is marked where it was typed, with what would fill it beside it, from
the same `rendering.letter_values` the renderer fills them from. A span is therefore a span
of what the person typed, and a replacement can be put exactly there.

**Nothing here is stored.** The list is worked out when the page is drawn. What is written
is the letter itself, by `apply`, on the person's press, one replacement at a time, and
refused when the text is no longer the text the list was drawn from (`digest`).

**Still no template engine.** A replacement is plain text put into a text field
(`rendering.fill_placeholders` says why a letter is never rendered through Django's
templates), and whatever a checker said is escaped where it is drawn.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from postulo.core import languages, writing_checks
from postulo.core.htmlstrings import sentence
from postulo.core.writing_checks import Document, Finding, Passage, Placeholder, Reported

from . import rendering
from .models import CoverLetter, document_language

SUBJECT, BODY = "subject", "body"

#: A letter's passages, and what each is called beside a quotation from it.
PASSAGE_LABELS = {SUBJECT: gettext_lazy("Subject"), BODY: gettext_lazy("Body")}

#: The placeholders an application fills in. With none chosen they are not asked about: a
#: template is not told that its company is missing.
FROM_THE_APPLICATION = ("company", "role", "location", "contact")

#: How much of the line around a span is quoted on each side, and how much of a long span.
AROUND = 60
MARKED = 120
#: How much of a text a button's name quotes.
NAMED = 40
#: How many of a finding's replacements are offered as buttons.
OFFERED = 3


def digest(text: str) -> str:
    """A short fingerprint of a passage's text, to tell whether it is still the text a list
    was drawn from. Not a secret and not for anything else."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------ the document


def _placeholders(text: str, values: dict[str, str], application) -> tuple[Placeholder, ...]:
    found = []
    for match in rendering.PLACEHOLDER_PATTERN.finditer(text):
        name = match.group(1)
        known = name in values
        value = None
        if known and (application is not None or name not in FROM_THE_APPLICATION):
            value = values[name]
        found.append(
            Placeholder(name=name, start=match.start(), end=match.end(), known=known, value=value)
        )
    return tuple(found)


def _company_names(application) -> str:
    """Every name the application's company goes by, one a line: a letter that names it by
    its acronym names it."""
    company = application.posting.company
    names = [company.name, company.list_name, *(row.name for row in company.names.all())]
    kept: list[str] = []
    for name in names:
        name = " ".join(str(name or "").split())
        if name and name not in kept:
            kept.append(name)
    return "\n".join(kept)


def _document(
    *, kind, variant, language, subject, body, values, application, facts, names
) -> Document:
    return Document(
        kind=kind,
        variant=variant,
        language=language,
        direction=languages.direction(language),
        passages=(
            Passage(
                key=SUBJECT,
                text=subject,
                language=language,
                role="heading",
                placeholders=_placeholders(subject, values, application),
            ),
            Passage(
                key=BODY,
                text=body,
                language=language,
                role="body",
                placeholders=_placeholders(body, values, application),
            ),
        ),
        facts=MappingProxyType(dict(facts)),
        placeholder_names=tuple(names),
    )


def letter_document(letter: CoverLetter, application=None) -> Document:
    """A letter as a checker is handed it, for the application chosen or for none."""
    language = document_language(letter)
    with languages.override(language):
        values = rendering.letter_values(letter, application)
    facts: dict[str, str] = {}
    if application is not None:
        facts = {name: values[name] for name in (*FROM_THE_APPLICATION, "name")}
        facts["company_names"] = _company_names(application)
    return _document(
        kind="letter",
        variant=str(letter.kind),
        language=language,
        subject=letter.subject or "",
        body=letter.body or "",
        values=values,
        application=application,
        facts=facts,
        names=CoverLetter.PLACEHOLDERS,
    )


def text_document(
    body: str,
    *,
    subject: str = "",
    kind: str = "letter",
    variant: str = "cover",
    language: str = "en-GB",
    facts=None,
) -> Document:
    """A document made of plain strings, shaped as a letter's is: for a checker's own tests
    (`postulo.plugins.testing`), which have no letter to hand.

    With ``facts`` it is read as for an application: ``company``, ``role``, ``location``,
    ``contact`` and ``name`` fill the placeholders, and one left out fills nothing. Without,
    as for none: only the person's name and the date are filled in.
    """
    values = dict.fromkeys(CoverLetter.PLACEHOLDERS, "")
    values.update(name="A. Person", date="1 January 2026")
    chosen = facts is not None
    if chosen:
        values.update({name: str(text) for name, text in facts.items() if name in values})
    return _document(
        kind=kind,
        variant=variant,
        language=language,
        subject=subject,
        body=body,
        values=values,
        application=True if chosen else None,
        facts=dict(facts) if chosen else {},
        names=CoverLetter.PLACEHOLDERS,
    )


# ---------------------------------------------------------------------- the list


@dataclass(frozen=True)
class Action:
    """One replacement a line offers, as the button that puts it in."""

    #: What the button is called: what it replaces, with what. Markup, already escaped.
    label: str
    #: The replacement as the form carries it: JSON, so that a line break in it arrives as
    #: the line break it is and not as whatever a browser makes of one in a form field.
    encoded: str


@dataclass(frozen=True)
class Line:
    """One finding as the list draws it."""

    reported: Reported
    #: What the passage is called, or nothing for a finding about the whole letter.
    passage_label: str
    #: The line the span is on: what is before it, the span, what is after it. Plain text,
    #: escaped where it is drawn.
    before: str
    marked: str
    after: str
    #: The fingerprint of the passage's text as this list was drawn from it.
    digest: str
    actions: tuple[Action, ...]

    @property
    def finding(self) -> Finding:
        return self.reported.finding

    @property
    def blocks(self) -> bool:
        """Whether this line may put a step in front of freezing a letter: a problem, said
        by a checker Postulo ships. An installed plugin adds lines, never a step everybody
        has to press through (#235)."""
        return self.reported.shipped and self.finding.severity == "problem"


@dataclass(frozen=True)
class Listing:
    """What the checkers said about one letter: the one list, problems first."""

    language: str
    direction: str
    problems: tuple[Line, ...]
    hints: tuple[Line, ...]
    #: Whether a passage was too long to hand to any checker.
    unread: bool
    #: Whether the letter uses placeholders only an application fills, and none was chosen.
    unasked: bool

    @property
    def total(self) -> int:
        return len(self.problems) + len(self.hints)

    @property
    def blocking(self) -> tuple[Line, ...]:
        return tuple(line for line in self.problems if line.blocks)

    @property
    def status(self) -> str:
        """The count, as the sentence a screen reader is told when the list changes."""
        said = []
        if self.problems:
            count = len(self.problems)
            said.append(
                ngettext(
                    "%(count)d thing will go out wrong.",
                    "%(count)d things will go out wrong.",
                    count,
                )
                % {"count": count}
            )
        if self.hints:
            count = len(self.hints)
            said.append(
                ngettext("%(count)d suggestion.", "%(count)d suggestions.", count)
                % {"count": count}
            )
        return " ".join(said) or _("Nothing to point out.")


def excerpt(passage: Passage, finding: Finding) -> tuple[str, str, str]:
    """The line a span is on, in three plain strings: before it, the span, after it.

    Clipped to about `AROUND` characters a side and to the span's own line, with an ellipsis
    where something was cut. Never markup: the template writes the highlight between them.
    """
    text = passage.text
    start, end = finding.start, finding.end
    before = text[max(0, start - AROUND) : start]
    cut = start - AROUND > 0
    if "\n" in before:
        before, cut = before.rsplit("\n", 1)[1], False
    after = text[end : end + AROUND]
    more = end + AROUND < len(text)
    if "\n" in after:
        after, more = after.split("\n", 1)[0], False
    marked = text[start:end]
    if len(marked) > MARKED:
        marked = marked[:MARKED] + "…"
    return (
        ("…" if cut else "") + before.replace("\r", ""),
        marked,
        after.replace("\r", "") + ("…" if more else ""),
    )


def _quoted(text: str, language: str, direction: str):
    """A stretch of the letter inside a sentence in the reader's language: marked as the
    letter's own language and direction, and with its spaces kept as typed."""
    if len(text) > NAMED:
        text = text[:NAMED] + "…"
    return format_html(
        '<bdi lang="{}" dir="{}" class="whitespace-pre-wrap">{}</bdi>', language, direction, text
    )


def replaced_sentence(old: str, new: str, language: str, direction: str, *, done: bool = False):
    """What a replacement does, in words: the name of its button, or what was done.

    One `<span>` round the whole sentence: a button and an alert lay their children out as a
    row, and the quotation marks and the quoted words would otherwise be items apart.
    """
    was, now = _quoted(old, language, direction), _quoted(new, language, direction)
    if done:
        if not old:
            said = sentence(_("Added “{new}”."), new=now)
        elif not new:
            said = sentence(_("Removed “{old}”."), old=was)
        else:
            said = sentence(_("Replaced “{old}” with “{new}”."), old=was, new=now)
    elif not old:
        said = sentence(_("Add “{new}”"), new=now)
    elif not new:
        said = sentence(_("Remove “{old}”"), old=was)
    else:
        said = sentence(_("Replace “{old}” with “{new}”"), old=was, new=now)
    return format_html("<span>{}</span>", said)


def _line(reported: Reported, document: Document) -> Line:
    finding = reported.finding
    passage = document.passage(finding.passage) if finding.passage else None
    if passage is None:
        return Line(reported, "", "", "", "", "", ())
    before, marked, after = excerpt(passage, finding)
    covered = passage.text[finding.start : finding.end]
    return Line(
        reported=reported,
        passage_label=str(PASSAGE_LABELS.get(passage.key, "")),
        before=before,
        marked=marked,
        after=after,
        digest=digest(passage.text),
        actions=tuple(
            Action(
                label=replaced_sentence(covered, text, document.language, document.direction),
                encoded=json.dumps(text),
            )
            for text in finding.replacements[:OFFERED]
        ),
    )


def letter_findings(letter: CoverLetter, application=None, user=None) -> Listing | None:
    """The one list for a letter, for the application chosen or for none.

    ``None`` where no checker is on for this person at all: there is then no list to draw,
    and the pages are what they were before there was one.
    """
    if not writing_checks.checkers("letter", user):
        return None
    document = letter_document(letter, application)
    lines = [_line(reported, document) for reported in writing_checks.found(document, user)]
    return Listing(
        language=document.language,
        direction=document.direction,
        problems=tuple(line for line in lines if line.finding.severity == "problem"),
        hints=tuple(line for line in lines if line.finding.severity != "problem"),
        unread=bool(writing_checks.unread(document)),
        unasked=application is None
        and any(
            held.known and held.value is None
            for passage in document.passages
            for held in passage.placeholders
        ),
    )


def blocking(listing: Listing | None) -> tuple[Line, ...]:
    """The lines that put a step in front of freezing a letter, or none."""
    return listing.blocking if listing is not None else ()


# ------------------------------------------------------------- putting one in


class Stale(Exception):
    """The text is no longer the text the list was drawn from; nothing was changed."""


class Refused(Exception):
    """The replacement breaks a rule of the contract, or the letter would not be valid with
    it; nothing was changed."""


@dataclass(frozen=True)
class Applied:
    """What one replacement did, which is also what putting the old words back needs."""

    passage: str
    #: Where the new words are now.
    start: int
    end: int
    old: str
    new: str
    #: The fingerprint of the passage's text after the replacement.
    digest: str

    def as_record(self, letter: CoverLetter) -> dict:
        """What the session keeps until the next replacement, for *Undo*."""
        return {
            "letter": letter.pk,
            "passage": self.passage,
            "start": self.start,
            "end": self.end,
            "text": self.old,
            "digest": self.digest,
        }


def _write(letter: CoverLetter, passage: str, text: str) -> None:
    """Keep ``text`` as the letter's passage, held to what the field itself allows."""
    setattr(letter, passage, text)
    others = [held.name for held in letter._meta.fields if held.name != passage]
    try:
        letter.clean_fields(exclude=others)
    except ValidationError as invalid:
        raise Refused from invalid
    letter.save(update_fields=[passage, "updated_at"])


def apply(
    letter: CoverLetter, *, passage: str, start: int, end: int, replacement: str, seen: str
) -> Applied:
    """Put one replacement into a letter's stored text, where the list said it goes.

    ``seen`` is the fingerprint the list carried. Where the text has changed since,
    `Stale` is raised and nothing is replaced. The span and the replacement are held to
    the contract's rules again, against the text as it is now (`writing_checks.problem_with`):
    in range, whole placeholders or none, no line break in a subject. No checker is asked:
    the replacement is the text the person saw on the button.

    The caller has narrowed the letter to the requester's own and holds it for the write.
    """
    if passage not in PASSAGE_LABELS or not isinstance(replacement, str):
        raise Refused
    text = getattr(letter, passage) or ""
    if digest(text) != seen:
        raise Stale
    proposed = Finding(
        check="applied",
        message="-",
        passage=passage,
        start=start,
        end=end,
        replacements=(replacement,),
    )
    if writing_checks.problem_with(proposed, writing_checks.readable(letter_document(letter))):
        raise Refused
    old = text[start:end]
    changed = text[:start] + replacement + text[end:]
    _write(letter, passage, changed)
    return Applied(
        passage=passage,
        start=start,
        end=start + len(replacement),
        old=old,
        new=replacement,
        digest=digest(changed),
    )


def can_restore(letter: CoverLetter, record) -> bool:
    """Whether what the session kept is the last replacement made to this letter, and the
    text is still as that replacement left it."""
    try:
        return (
            record["letter"] == letter.pk
            and record["passage"] in PASSAGE_LABELS
            and digest(getattr(letter, record["passage"]) or "") == record["digest"]
            and 0 <= record["start"] <= record["end"]
            and isinstance(record["text"], str)
        )
    except (KeyError, TypeError):
        return False


def restore(letter: CoverLetter, record) -> None:
    """Put back the words the last replacement took out: *Undo*.

    From what the session kept and never from the request, so it can only ever put back
    what was there. Refused as `Stale` where the letter has changed since.
    """
    if not can_restore(letter, record):
        raise Stale
    passage = record["passage"]
    text = getattr(letter, passage) or ""
    _write(letter, passage, text[: record["start"]] + record["text"] + text[record["end"] :])
