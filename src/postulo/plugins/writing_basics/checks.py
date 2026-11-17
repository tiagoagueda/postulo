"""The five checks of *Writing basics*, each a pure function over a `WritingDocument` (#1258).

Regular expressions over the text the person stored, and nothing else: no dictionary, no
model, no network, no query. Every finding is a hint, because each is a matter of judgement.
Each sentence quotes what it means, since a screen reader does not announce a highlight.

**Scripts written without spaces** (Chinese, Japanese, Thai and their neighbours) have no
words a regular expression can count or repeat: the checks about words and sentences leave
them alone, and the length is counted in characters instead.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext, ngettext

from postulo.plugins.api import WritingFinding

#: The languages written without spaces between words, by their primary subtag.
WITHOUT_SPACES = frozenset({"zh", "ja", "th", "lo", "km", "my", "bo", "dz"})

#: About as long as each kind of letter keeps to, in words, and in characters for a script
#: written without spaces: common guidance for one page, not measured against a theme.
WORDS = {"cover": 400, "speculative": 400, "follow_up": 150, "motivation": 800}
CHARACTERS = {"cover": 800, "speculative": 800, "follow_up": 300, "motivation": 1600}

#: Longer than this, a sentence is pointed at.
LONG_SENTENCE = 40

#: How much of a word or a name a sentence quotes.
QUOTED = 40

PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
#: The same word twice, with only spaces or tabs between: letters alone, so a number or a
#: placeholder's braces are never a word. The second word is looked ahead at rather than
#: taken, so that it is the first word of the next comparison: "led the the" is two pairs.
REPEATED = re.compile(r"(?<![\w{])([^\W\d_]+)(?=([ \t]+)([^\W\d_]+)(?![\w}]))")
#: Two or more spaces between two things that are not spaces, on one line.
SPACES = re.compile(r"(\S+)( {2,})(?=\S)")
#: A bracketed note, as the starters write them: `[Why this role, in a sentence.]`.
NOTE = re.compile(r"\[[^\[\]\r\n]{1,200}\]")
#: Where a sentence ends: its punctuation in the scripts Postulo is written in, or a line.
SENTENCE_END = re.compile(r"[.!?…。！？؟۔।]+|\n")


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= QUOTED else text[:QUOTED] + "…"


def _without_spaces(document) -> bool:
    return document.language.split("-")[0].casefold() in WITHOUT_SPACES


def _body(document):
    return document.passage("body")


def _words(text: str) -> int:
    """How many words: each placeholder is one, whatever it holds."""
    return len(PLACEHOLDER.findall(text)) + len(WORD.findall(PLACEHOLDER.sub(" ", text)))


# ---------------------------------------------------------------------------- names


def _names(document, placeholder: str, said: str) -> bool:
    """Whether the letter names it: by its placeholder, or by any of ``said``'s lines."""
    for passage in document.passages:
        if any(held.known and held.name == placeholder for held in passage.placeholders):
            return True
    text = " ".join(passage.text for passage in document.passages).casefold()
    return any(name.strip().casefold() in text for name in said.splitlines() if name.strip())


def names(document):
    """A letter for an application that names neither the company nor the role. Not asked
    with no application chosen, and not of a follow-up note, which may well name neither."""
    facts = document.facts
    if not facts or document.variant == "follow_up":
        return
    company = facts.get("company_names") or facts.get("company", "")
    if company.strip() and not _names(document, "company", company):
        yield WritingFinding(
            check="company-not-named",
            message=gettext(
                "The letter does not name the company: neither {{ company }} nor “%(name)s” "
                "is in it."
            )
            % {"name": _quote(facts.get("company") or company.splitlines()[0])},
        )
    role = facts.get("role", "")
    if role.strip() and not _names(document, "role", role):
        yield WritingFinding(
            check="role-not-named",
            message=gettext(
                "The letter does not name the role: neither {{ role }} nor “%(role)s” is in it."
            )
            % {"role": _quote(role)},
        )


# --------------------------------------------------------------------- starter notes


def starter_notes(document):
    """A note in square brackets, as the starters leave them, still in the letter."""
    for passage in document.passages:
        for match in NOTE.finditer(passage.text):
            yield WritingFinding(
                check="starter-note",
                message=gettext("“%(note)s” looks like a note left from the starter text.")
                % {"note": _quote(match.group(0))},
                passage=passage.key,
                start=match.start(),
                end=match.end(),
            )


# ---------------------------------------------------------------------------- length


def length(document):
    """The body against what its kind of letter usually keeps to."""
    body = _body(document)
    if body is None:
        return
    variant = document.variant if document.variant in WORDS else "cover"
    if _without_spaces(document):
        count, limit = len("".join(body.text.split())), CHARACTERS[variant]
        if count > limit:
            yield WritingFinding(
                check="long-for-its-kind",
                message=ngettext(
                    "The letter is %(count)d character long, more than the %(limit)d a "
                    "letter of this kind usually keeps to. The PDF shows how many pages it "
                    "takes.",
                    "The letter is %(count)d characters long, more than the %(limit)d a "
                    "letter of this kind usually keeps to. The PDF shows how many pages it "
                    "takes.",
                    count,
                )
                % {"count": count, "limit": limit},
            )
        return
    count, limit = _words(body.text), WORDS[variant]
    if count > limit:
        yield WritingFinding(
            check="long-for-its-kind",
            message=ngettext(
                "The letter is %(count)d word long, more than the %(limit)d a letter of this "
                "kind usually keeps to. The PDF shows how many pages it takes.",
                "The letter is %(count)d words long, more than the %(limit)d a letter of this "
                "kind usually keeps to. The PDF shows how many pages it takes.",
                count,
            )
            % {"count": count, "limit": limit},
        )


# ------------------------------------------------------------------------ repetition


def repetition(document):
    """The same word twice in a row, and several spaces where one would do."""
    for passage in document.passages:
        if not _without_spaces(document):
            for match in REPEATED.finditer(passage.text):
                first, second = match.group(1), match.group(3)
                if first.casefold() != second.casefold():
                    continue
                yield WritingFinding(
                    check="repeated-word",
                    message=gettext("“%(word)s” is written twice in a row.")
                    % {"word": _quote(first)},
                    passage=passage.key,
                    start=match.start(1),
                    end=match.end(3),
                    replacements=(first,),
                )
        for match in SPACES.finditer(passage.text):
            yield WritingFinding(
                check="doubled-space",
                message=gettext("There are several spaces in a row after “%(word)s”.")
                % {"word": _quote(match.group(1))},
                passage=passage.key,
                start=match.start(2),
                end=match.end(2),
                replacements=(" ",),
            )


# ------------------------------------------------------------------------- sentences


def sentences(document):
    """A sentence of more than `LONG_SENTENCE` words in the body."""
    body = _body(document)
    if body is None or _without_spaces(document):
        return
    text, begun = body.text, 0
    ends = [*SENTENCE_END.finditer(body.text), None]
    for end in ends:
        stop = end.end() if end is not None else len(text)
        piece = text[begun:stop]
        count = _words(piece)
        if count > LONG_SENTENCE:
            lead = len(piece) - len(piece.lstrip())
            trail = len(piece.rstrip())
            yield WritingFinding(
                check="long-sentence",
                message=ngettext(
                    "The sentence beginning “%(start)s” is %(count)d word long; a shorter one "
                    "is easier to read.",
                    "The sentence beginning “%(start)s” is %(count)d words long; a shorter "
                    "one is easier to read.",
                    count,
                )
                % {"start": _quote(" ".join(piece.split()[:6])), "count": count},
                passage=body.key,
                start=begun + lead,
                end=begun + trail,
            )
        begun = stop
