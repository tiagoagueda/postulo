"""What reads a document's own words and says what it noticed, and who may be asked (#1258).

A letter used to be checked by one function for one thing: a placeholder with nothing behind
it, said in one sentence on the page that freezes a letter. A second check beside it would
have been a second list, and a third a third. So the checks are a registry, supplied by
`writing-checker` plugins the way a field's suggestions are (`core.suggestion_sources`), and
the placeholder check is the first of them. **There is one list**, drawn by Postulo from plain
data: a checker supplies findings and never markup.

**A checker reads and changes nothing.** It is handed a `Document`: the person's own text
exactly as stored, in `Passage`s, with every placeholder marked where it was typed and what
would fill it beside it. It answers with `Finding`s: a sentence, where in which passage, and
the replacements it proposes, if it has any. Putting one in is a press by the person who
read it (`documents.checking.apply`), never anything a checker does.

**Offsets are code points**, end exclusive, over the text exactly as stored. A stored body
may hold ``\\r\\n`` and nothing normalises it between the check and the replacement; a
service that answers in UTF-16 units converts before it answers.

**Only offline checkers are asked by a page** (#879, #1187, #1252). A checker that calls a
service names its hosts and what it sends, is asked only when the person presses for it, and
is handed the text `without_facts`: it reads ``Dear {{ company }},`` and never learns which
company. This module refuses one that names no host, and `found` never asks one.

**Every finding is checked before it is drawn** (`problem_with`), and one that breaks a rule
is dropped with a line in the log that names the checker and the rule and never quotes the
text: a letter is personal data. **A checker that raises is left out**, logged by its key and
the exception's type alone, so a broken plugin makes a shorter list and never a broken page.

**With no checker enabled there is no list.** Nothing is an error.
"""

from __future__ import annotations

import itertools
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from . import modules

logger = logging.getLogger(__name__)

#: How sure a finding is. A ``problem`` is certain and will be seen (a blank where a name
#: should be); anything a person could reasonably disagree with is a ``hint``.
SEVERITIES = ("problem", "hint")
#: The documents a checker may say it reads.
DOCUMENTS = ("letter", "message", "cv")
#: What a passage is: a subject line needs no full stop, and a list is not a paragraph.
ROLES = ("heading", "body", "list")
#: The most findings taken from one checker for one document.
MAX_FINDINGS = 100
#: The longest passage handed to any checker. A checker cannot be interrupted, so the cap is
#: on what it is given; the list says which passage was not read.
MAX_PASSAGE = 20_000
#: The longest sentence a finding may say.
MAX_MESSAGE = 300
#: The most replacements one finding may propose, and the longest one may be. A span that
#: replacements are proposed for is held to the same length, so that putting the old words
#: back is always a replacement the rules allow.
MAX_REPLACEMENTS = 5
MAX_REPLACEMENT = 500
#: How long a checker's key may be.
MAX_KEY_LENGTH = 40

#: What a finding's ``check`` and a checker's ``key`` look like.
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class Placeholder:
    """One ``{{ name }}`` in a passage, marked where it was typed and not filled in."""

    #: The name between the braces: ``company``.
    name: str
    #: Where the whole token, braces included, starts and ends in its passage's text.
    start: int
    end: int
    #: Whether it is one of the names Postulo fills in. A misspelt one is not.
    known: bool
    #: What fills it for the application chosen. ``""`` is nothing does; ``None`` is not
    #: asked: no application was chosen for a placeholder only one can fill, or the checker
    #: is not offline and is told nothing about the person or the employer.
    value: str | None = None


@dataclass(frozen=True)
class Passage:
    """One stretch of a document's text: a letter's subject, its body, one entry of a CV."""

    #: Which passage this is: ``subject``, ``body``. Opaque to a checker, which hands it back.
    key: str
    #: The person's own words exactly as stored, placeholders as typed.
    text: str
    #: The language this passage is written in, as a BCP 47 tag.
    language: str
    #: One of `ROLES`.
    role: str = "body"
    #: Every placeholder in ``text``, in order.
    placeholders: tuple[Placeholder, ...] = ()


@dataclass(frozen=True)
class Document:
    """What a checker is handed: plain values, and nothing that reaches a model."""

    #: One of `DOCUMENTS`.
    kind: str
    #: A letter's kind (``cover``, ``motivation``, ``speculative``, ``follow_up``), a
    #: message's purpose, ``cv`` or ``portfolio``.
    variant: str
    #: The language the document declares, as a BCP 47 tag, and the direction it is set in.
    language: str
    direction: str
    passages: tuple[Passage, ...]
    #: What is known for the application chosen: ``company``, ``role``, ``location``,
    #: ``contact``, ``name``, and ``company_names``, every name the company goes by, one a
    #: line. Empty with no application, and always empty for a checker that is not offline.
    facts: Mapping[str, str] = field(default_factory=dict)
    #: The placeholder names Postulo fills in for a document of this kind: ``company``,
    #: ``role`` ... A checker that meets another name knows it for a misspelling.
    placeholder_names: tuple[str, ...] = ()

    def passage(self, key: str) -> Passage | None:
        """The passage called ``key``, or nothing."""
        return next((passage for passage in self.passages if passage.key == key), None)


@dataclass(frozen=True)
class Finding:
    """One thing a checker noticed: a sentence, where, and what it would put there instead."""

    #: The checker's own id for the rule: ``repeated-word``.
    check: str
    #: One sentence of plain text in the reader's language. It quotes what it means, because
    #: a screen reader does not announce a highlight.
    message: str
    #: A `Passage.key`; ``""`` is about the whole document, and then there is no span.
    passage: str = ""
    #: Code points into the passage's text, end exclusive. Equal, they are a place between
    #: two characters.
    start: int = 0
    end: int = 0
    #: Proposed texts for ``[start:end]``, best first. None is nothing to put in.
    replacements: tuple[str, ...] = ()
    #: One of `SEVERITIES`.
    severity: str = "hint"
    #: The language the check read the words as, where that matters.
    language: str = ""


@dataclass(frozen=True)
class Checker:
    """One reader of documents, as a plugin declares it."""

    #: Its own key, unique across the checkers installed.
    key: str
    #: What the list says a line came from.
    label: str
    #: Which of `DOCUMENTS` it reads.
    documents: tuple[str, ...]
    #: The languages it reads, as BCP 47 tags (``en`` reads ``en-GB`` too); none is any.
    languages: tuple[str, ...]
    #: Whether it answers from what ships with it, with no network.
    offline: bool
    #: The function that answers: handed a `Document`, it returns `Finding`s.
    check: Callable[[Document], Iterable[Finding]]
    #: Every host a checker that is not offline may ask, and the only ones it may.
    hosts: tuple[str, ...] = ()
    #: What it sends there, in a sentence.
    sends: str = ""
    #: The licence of a dictionary or rule set it ships, if it ships one.
    licence: str = ""
    #: The id of the plugin's own sub-plugin that switches this checker, if one does: Postulo
    #: then asks it only for a person that sub-plugin is on for.
    sub_plugin: str = ""


@dataclass(frozen=True)
class Reported:
    """A finding that passed the rules, and who said it."""

    finding: Finding
    #: The checker's key and what it is called.
    checker: str
    label: str
    #: The plugin that holds the checker, and whether Postulo ships it.
    plugin: str
    shipped: bool


# ------------------------------------------------------------------ what may be asked


def checker_problems(checker) -> list[str]:
    """What is wrong with a checker as declared, in words for its author; nothing if usable."""
    if not isinstance(checker, Checker):
        return ["it is not a WritingChecker"]
    problems = []
    try:
        if not isinstance(checker.key, str) or not _SLUG.fullmatch(checker.key):
            problems.append("its key is not lower-case letters, digits and hyphens, at most 40")
        if not str(checker.label or "").strip():
            problems.append("it has no label")
        documents = tuple(checker.documents or ())
        if not documents or any(kind not in DOCUMENTS for kind in documents):
            problems.append(f"its documents are not among {DOCUMENTS}")
        if not all(isinstance(tag, str) and tag for tag in tuple(checker.languages or ())):
            problems.append("its languages are not language tags")
        if not callable(checker.check):
            problems.append("its check is not callable")
        # A checker that dials out says where and what it sends; one that does not has
        # nothing to say.
        if checker.offline and checker.hosts:
            problems.append("it is offline and names hosts")
        if not checker.offline and not checker.hosts:
            problems.append("it is not offline and names no host")
        if not checker.offline and not str(checker.sends or "").strip():
            problems.append("it is not offline and does not say what it sends")
    except Exception:  # pragma: no cover - a hostile object
        return ["it cannot be read"]
    return problems


def _offered(document_kind: str, user) -> list[tuple[Checker, str, bool]]:
    """Every usable checker of the enabled plugins, with its plugin's name and whether
    Postulo ships that plugin."""
    from postulo.plugins import policy
    from postulo.plugins import registry as plugin_registry

    shipped = tuple(plugin_registry.builtins().get("writing-checker", ()))
    found: dict[str, tuple[Checker, str, bool]] = {}
    installed = plugin_registry.plugins("writing-checker")
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        name = str(getattr(plugin, "name", "") or "")
        if user is not None and not modules.is_on(user, name):
            continue
        try:
            declared = list(getattr(plugin, "checkers", ()) or ())
        except Exception as error:
            logger.warning(
                "Plugin %r's writing checkers could not be read (%s)", name, type(error).__name__
            )
            continue
        for checker in declared:
            if problems := checker_problems(checker):
                logger.warning(
                    "Plugin %r offered a writing checker that cannot be used: %s",
                    name,
                    "; ".join(problems),
                )
            elif checker.key in found:
                logger.warning(
                    "Writing checker %r was offered twice; the first one keeps it", checker.key
                )
            elif (
                user is not None
                and checker.sub_plugin
                and not policy.sub_plugin_enabled(name, checker.sub_plugin, user)
            ):
                # Its own switch is off for this person. Not a second claim on the key.
                continue
            else:
                found[checker.key] = (checker, name, type(plugin) in shipped)
    return [
        entry
        for entry in found.values()
        if not document_kind or document_kind in entry[0].documents
    ]


def checkers(document_kind: str = "", user=None) -> list[Checker]:
    """Every usable checker of the enabled plugins, those that read ``document_kind`` if given.

    With a ``user``, the plugins switched off for that person are left out, and so is a
    checker whose own sub-plugin is off for them. Postulo's own come first, then others in
    the order they were installed; the first to claim a key keeps it and a second is logged
    and left out.
    """
    return [checker for checker, _plugin, _shipped in _offered(document_kind, user)]


def reads(checker: Checker, language: str) -> bool:
    """Whether a checker reads this language: any, the tag itself, or the tag's language."""
    if not checker.languages:
        return True
    wanted = language.casefold()
    return any(
        wanted == tag.casefold() or wanted.startswith(f"{tag.casefold()}-")
        for tag in checker.languages
    )


# ----------------------------------------------------------------- what is handed over


def unread(document: Document) -> tuple[str, ...]:
    """The keys of the passages too long to hand to any checker."""
    return tuple(passage.key for passage in document.passages if len(passage.text) > MAX_PASSAGE)


def readable(document: Document) -> Document:
    """The document as a checker is handed it: without the passages that are too long."""
    kept = tuple(passage for passage in document.passages if len(passage.text) <= MAX_PASSAGE)
    return document if len(kept) == len(document.passages) else replace(document, passages=kept)


def without_facts(document: Document) -> Document:
    """The document as a checker that is not offline is handed it: the text and where the
    placeholders are, and nothing about who the person is or whom they are writing to."""
    return replace(
        document,
        facts={},
        passages=tuple(
            replace(
                passage,
                placeholders=tuple(replace(held, value=None) for held in passage.placeholders),
            )
            for passage in document.passages
        ),
    )


# --------------------------------------------------------------- what may be drawn


def _whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def splits_a_placeholder(passage: Passage, start: int, end: int) -> bool:
    """Whether ``[start:end]`` takes part of a placeholder and not the whole of it.

    A span covers whole tokens or touches none: half of ``{{ company }}`` replaced would
    break the placeholder silently. A place between two characters (``start == end``) is
    inside a token only when it is strictly between its braces.
    """
    for held in passage.placeholders:
        if start == end:
            touches = held.start < start < held.end
        else:
            touches = start < held.end and end > held.start
        if touches and not (start <= held.start and held.end <= end):
            return True
    return False


def problem_with(finding, document: Document) -> str:
    """The rule a finding breaks, as one word for the log, or ``""`` where it may be drawn.

    The same rules are asked again when a replacement is put in (`documents.checking.apply`),
    of the text as it is then.
    """
    if not isinstance(finding, Finding):
        return "type"
    if not isinstance(finding.check, str) or not _SLUG.fullmatch(finding.check):
        return "check"
    message = finding.message
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_MESSAGE
        or "\n" in message
        or "\r" in message
    ):
        return "message"
    if finding.severity not in SEVERITIES:
        return "severity"
    if not isinstance(finding.language, str) or len(finding.language) > 35:
        return "language"
    if not _whole_number(finding.start) or not _whole_number(finding.end):
        return "span"
    replacements = finding.replacements
    if not isinstance(replacements, tuple | list) or len(replacements) > MAX_REPLACEMENTS:
        return "replacements"
    if not all(isinstance(text, str) and len(text) <= MAX_REPLACEMENT for text in replacements):
        return "replacements"
    if not isinstance(finding.passage, str):
        return "passage"
    if not finding.passage:
        # About the whole document: there is nowhere to point and nothing to put anywhere.
        return "span" if finding.start or finding.end or replacements else ""
    passage = document.passage(finding.passage)
    if passage is None:
        return "passage"
    if not 0 <= finding.start <= finding.end <= len(passage.text):
        return "span"
    if splits_a_placeholder(passage, finding.start, finding.end):
        return "placeholder"
    if replacements:
        covered = passage.text[finding.start : finding.end]
        if len(covered) > MAX_REPLACEMENT:
            return "replacements"
        if any(text == covered for text in replacements):
            return "replacements"
        if passage.role == "heading" and any("\n" in text or "\r" in text for text in replacements):
            return "replacements"
    return ""


def found(document: Document, user=None) -> list[Reported]:
    """What the enabled offline checkers say about a document, in the order they are asked.

    Postulo's own checkers first. Each is handed the document without the passages that are
    too long (`unread` names those), at most `MAX_FINDINGS` are taken from each, and every
    finding is held to `problem_with`. A checker that raises is logged and left out.
    """
    handed = readable(document)
    reported: list[Reported] = []
    for checker, plugin, shipped in _offered(document.kind, user):
        if not checker.offline or not reads(checker, document.language):
            continue
        try:
            answer = list(itertools.islice(iter(checker.check(handed)), MAX_FINDINGS))
        except Exception as error:
            # The type and never the message, which may quote the letter (#797).
            logger.warning(
                "Writing checker %r failed (%s) and was left out", checker.key, type(error).__name__
            )
            continue
        for finding in answer:
            if rule := problem_with(finding, handed):
                logger.warning(
                    "Writing checker %r gave a finding that breaks the rule %r; it was left out",
                    checker.key,
                    rule,
                )
                continue
            reported.append(
                Reported(
                    finding=finding,
                    checker=checker.key,
                    label=str(checker.label),
                    plugin=plugin,
                    shipped=shipped,
                )
            )
    return reported
