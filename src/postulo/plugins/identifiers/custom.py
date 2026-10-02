"""The schemes an instance defines for itself, read from the JSON an administrator typed (#311).

The schemes in `schemes.py` are Python: somebody wrote each one, and it was reviewed. An
instance has identifiers nobody here will ever write a scheme for -- a staff number, the
register of a country Postulo has no scheme for, a membership number of a professional
body -- and an administrator should not have to write a plugin to record one. So they
write this, on *Server settings → Plugins*:

.. code-block:: json

    [
      {
        "key": "staff-number",
        "label": {"en-GB": "Staff number", "pt-PT": "Número de funcionário"},
        "subjects": ["person"],
        "pattern": "[A-Z]{2}-\\\\d{6}",
        "example": "AB-123456",
        "upper": true,
        "link": "https://intranet.example.org/staff/{value}"
      }
    ]

**What a scheme can say** is the ten names in `NAMES` and nothing else; another name is
refused by name, because a misspelt ``patern`` that was quietly ignored would be a scheme
that accepts anything.

- ``key``: what a row stores. Lower-case letters, digits and hyphens, and never one of
  Postulo's own or ``other``: the schemes Postulo ships are added to and never changed.
- ``label``: a name, or one name per language as an object of BCP 47 tags in their
  canonical form (``pt-BR``). A reader is shown the closest to their own language, else the
  closest to British English, else the first written (`core.identifiers.in_languages`).
- ``subjects``: ``"person"``, ``"company"`` or both; at least one.
- ``pattern``: what a value looks like, in the language `patterns` describes. **Not a
  regular expression**: it is run on whatever anybody types, and has to read it once.
- ``link`` and ``person_link``: where a value leads, with ``{value}`` standing for it;
  http or https, and the value is percent-encoded into it (`core.identifiers.link_template`).
- ``example``: shown where the kinds are listed, and in the sentence that refuses a value.
  It has to be a value the scheme itself accepts, which is the one check of a pattern an
  administrator gets before somebody else meets it.
- ``upper`` and ``lower``: fold what was typed before it is checked.
- ``checksum``: the *name* of one of the two algorithms already written, in `CHECKSUMS`.
  A checksum is code, and JSON supplies none.

**Every problem carries the line it is on**, and the scheme's key where it has one, because
the text sits in a box with no line numbers and "something is wrong" sends somebody
through all of it. The standard library's parser reads the text; a second walk over the
same text, using the same decoder for every value, finds where each scheme and each of its
names begins.

**Read the same way every time.** `read` is what the page calls when the text is saved and
what the registry calls when it is used, so a text that reached the policy row some other
way -- a restored backup, a row written by hand -- is held to exactly these rules: the
schemes with nothing wrong are used, the rest are left out, and a text that is not JSON at
all defines nothing.

**Whatever a scheme says is text that can be drawn.** A name is in a menu on every page
that offers the scheme, so half of a character -- which JSON lets a text hold as an
escape, and which nothing can encode -- is refused wherever it is written, with its line;
and a name has to be seen and to read as it is written, so one made of characters that
take no room, or holding one that turns the text around, is refused too. The sentences
that refuse them quote nothing that could not itself be drawn.

A plugin holds no rows. The text is Postulo's to keep, on the policy row, and is handed in.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy

from postulo.core.identifiers import (
    DEFINED_HERE,
    MAX_KEY_LENGTH,
    MAX_VALUE_LENGTH,
    OTHER,
    SUBJECTS,
    Reading,
    Scheme,
    in_languages,
    link_template,
)
from postulo.plugins.api import is_language_tag, language_tag

from . import patterns
from .schemes import lei_checks_out, orcid_checks_out

#: How long the whole text may be, in characters. A hundred schemes of five hundred
#: characters, which is far more than a page of them. It bounds what there is to parse and
#: to store. It does **not** bound how long the reading takes, which was the claim made
#: here and was wrong: a pattern of 300 characters could cost a quarter of a second to
#: prepare, and a text well inside this bound 18 seconds. What bounds the reading is the
#: budget a pattern's classes have (`patterns.MAX_CLASS_WEIGHT`), the number of schemes,
#: and the ten of a kind that are said about any one scheme (`SHOWN_ALIKE`); the test of
#: the costliest text they allow between them is where the time is measured.
MAX_CHARACTERS = 50_000

#: How many schemes an instance may define. Each is offered in a menu.
MAX_SCHEMES = 100

#: How long a scheme's name may be, in any one language.
MAX_NAME_LENGTH = 60

#: Everything a scheme can say, in the order the page lists them.
NAMES = (
    "key",
    "label",
    "subjects",
    "pattern",
    "link",
    "person_link",
    "example",
    "upper",
    "lower",
    "checksum",
)

#: What a scheme has to say.
REQUIRED = ("key", "label", "subjects", "pattern")

#: How many problems of one kind are spelt out for one scheme before the rest are counted:
#: names a scheme cannot say, and names in languages that cannot be used. A scheme of seven
#: thousand misspelt names is one mistake, and seven thousand sentences about it were a
#: second of somebody's request and seven thousand lines of the log.
SHOWN_ALIKE = 10

_KEY = re.compile(r"[a-z0-9][a-z0-9-]*")
#: What text on one line cannot hold: the control characters, the two characters that end
#: a line where a new line does not, and the surrogates. Written by number, so that the
#: file holds no character an editor does not show.
_CONTROL = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f"
    "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}" + chr(0xD800) + "-" + chr(0xDFFF) + "]"
)
#: Half of a character. JSON writes a character beyond U+FFFF as two escapes, and one of
#: the two by itself -- ``\\ud83d`` -- is read by the parser without a word and is not text:
#: nothing can encode it, and the page that drew it would fail for everybody (#311).
_HALF = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
#: What a name cannot hold, because a name is drawn in every menu: the characters that
#: turn the direction of the text around (the embeddings, the overrides and the isolates),
#: and the three that take no room. The two joiners (U+200C, U+200D) are not among them:
#: Persian, Hindi and others are spelt with them.
_NOT_IN_A_NAME = frozenset({*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200B, 0x2060, 0xFEFF})
_SPACE = re.compile(r"[ \t\n\r]*")
_decoder = json.JSONDecoder()


# ----------------------------------------------------------------------- the checksums
#
# By name. The arithmetic is the two functions `schemes.py` has always had; what is added
# here is only that a value may hold whatever an administrator's pattern let through, so
# everything that is not part of the number is dropped before it is counted, and a value
# with nothing to count fails instead of raising.


def _mod_11_2(value: str) -> bool:
    """ISO 7064 MOD 11-2, as an ORCID and an ISNI end: digits, and a last digit or ``X``."""
    kept = "".join(character for character in value.upper() if character in "0123456789X")
    return len(kept) >= 2 and "X" not in kept[:-1] and orcid_checks_out(kept)


def _mod_97_10(value: str) -> bool:
    """ISO 7064 MOD 97-10, as a legal-entity identifier and an IBAN end: letters count as
    two digits each, and the whole is 1 modulo 97."""
    kept = "".join(
        character
        for character in value.upper()
        if character in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    return len(kept) >= 3 and lei_checks_out(kept)


#: The algorithms a scheme can name, and no others.
CHECKSUMS = {
    "iso7064-mod11-2": _mod_11_2,
    "iso7064-mod97-10": _mod_97_10,
}


# ------------------------------------------------------------------------ what is wrong


@dataclass(frozen=True)
class Problem:
    """One thing wrong with the definitions: where, and a sentence saying what."""

    reason: object
    #: The line of the text it is on, counted from one.
    line: int = 1
    #: The column, for text that is not JSON; nothing otherwise.
    column: int = 0
    #: The key of the scheme it is in, where the scheme has one that can be quoted.
    key: str = ""
    #: Which scheme of the list it is in, counted from one, where it has no such key.
    number: int = 0

    def __str__(self) -> str:
        said = {
            "line": self.line,
            "column": self.column,
            "key": self.key,
            "number": self.number,
            "reason": self.reason,
        }
        if self.column:
            return _("Line %(line)d, column %(column)d: %(reason)s") % said
        if self.key:
            return _("Line %(line)d, scheme “%(key)s”: %(reason)s") % said
        if self.number:
            return _("Line %(line)d, scheme number %(number)d: %(reason)s") % said
        return _("Line %(line)d: %(reason)s") % said


#: What the standard library's parser says, by how its message begins, in words for
#: somebody who writes JSON twice a year. The message itself is English and names tokens.
_NOT_JSON = (
    (
        "Expecting value",
        _(
            "a value should begin here: text in double quotes, a list, an object, true or "
            "false. A comma after the last item is not allowed."
        ),
    ),
    (
        "Expecting ',' delimiter",
        _("a comma is missing before this, or a bracket further up was never closed."),
    ),
    ("Expecting ':' delimiter", _("a colon is missing between a name and what it says.")),
    (
        "Expecting property name",
        _("a name in double quotes should begin here. A comma after the last item is not allowed."),
    ),
    ("Illegal trailing comma", _("a comma follows the last item. Take it out.")),
    (
        "Unterminated string",
        _("the text that opens with a double quote here is never closed."),
    ),
    (
        "Invalid control character",
        _(
            "a text in double quotes cannot run over a line break or hold a tab, so a "
            "closing quote may be missing."
        ),
    ),
    (
        "Invalid \\",
        _(
            "JSON does not know what follows this backslash. A backslash in a pattern is "
            "written twice: \\\\d for a digit."
        ),
    ),
    ("Extra data", _("the list has ended, and something follows it.")),
)


def _not_json(message: str):
    for beginning, words in _NOT_JSON:
        if message.startswith(beginning):
            return words
    return _("this is not JSON.")


# -------------------------------------------------------------------- where things are


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _past_space(text: str, index: int) -> int:
    return _SPACE.match(text, index).end()


def _items(text: str) -> list[tuple[int, object]]:
    """Each item of the list the text is, with where it begins. The text is JSON already."""
    found = []
    index = _past_space(text, 0) + 1
    while True:
        index = _past_space(text, index)
        if text[index] == "]":
            return found
        value, after = _decoder.raw_decode(text, index)
        found.append((index, value))
        index = _past_space(text, after)
        if text[index] == ",":
            index += 1


def _members(text: str, start: int) -> list[tuple[str, object, int, int]]:
    """Each name of the object beginning at ``start``: the name, what it says, where the
    name is, and where what it says begins."""
    found = []
    index = start + 1
    while True:
        index = _past_space(text, index)
        if text[index] == "}":
            return found
        at = index
        name, after = _decoder.raw_decode(text, index)
        index = _past_space(text, _past_space(text, after) + 1)
        value, after = _decoder.raw_decode(text, index)
        found.append((name, value, at, index))
        index = _past_space(text, after)
        if text[index] == ",":
            index += 1


# ---------------------------------------------------------------------------- reading


def _text(value, most: int) -> str | None:
    """``value`` if it is text on one line, with something in it and no longer than ``most``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > most or _CONTROL.search(value):
        return None
    return value


def _half_in(value) -> str:
    """The first half of a character anywhere in what a name says, as JSON writes it
    (``\\ud83d``); empty where everything in it is text. A list and an object are looked
    through: a label's languages and their names, the subjects."""
    if isinstance(value, str):
        found = _HALF.search(value)
        return f"\\u{ord(found[0]):04x}" if found else ""
    if isinstance(value, dict):
        return next((half for pair in value.items() for half in map(_half_in, pair) if half), "")
    if isinstance(value, list):
        return next((half for half in map(_half_in, value) if half), "")
    return ""


def _shown(text: str, most: int = 40) -> str:
    """Something the text says, quoted back in a sentence: cut short, and with half of a
    character written as JSON writes it, so that the sentence itself can be drawn."""
    return _HALF.sub(lambda half: f"\\u{ord(half[0]):04x}", text[:most])


def _unseen(name: str):
    """Why a name that is text on one line still cannot be a name; nothing where it can.

    It is drawn in a menu on every page that offers the scheme, so it has to be something
    a person can see, and it has to read the way it is written.
    """
    for character in name:
        if ord(character) in _NOT_IN_A_NAME:
            return _(
                "a name cannot hold U+%(code)s, a character that takes no room or turns "
                "the text around: the name would not read as it is written."
            ) % {"code": f"{ord(character):04X}"}
    if all(c.isspace() or unicodedata.category(c) == "Cf" for c in name):
        return _(
            "a name has to be something that can be seen: this one holds only characters "
            "that are not drawn."
        )
    return None


class _One:
    """One scheme being read: what it says, and what is wrong with it."""

    def __init__(self, text: str, start: int, number: int) -> None:
        self.text = text
        self.start = _line(text, start)
        self.number = number
        self.key = ""
        self.problems: list[Problem] = []
        self.said: dict[str, object] = {}
        self.lines: dict[str, int] = {}
        #: Where what each name says begins in the text. A label in several languages is
        #: walked again from there, because the parser kept one name for each language.
        self.begins: dict[str, int] = {}
        #: The names that say something which is not text. Said once, and nothing more is
        #: asked of them: they are neither missing nor read.
        self.not_text: set[str] = set()
        passed_over, last = 0, start
        for name, value, at, begins in _members(text, start):
            if name in self.said or name not in NAMES:
                # Spelt out ten times, and counted after that.
                if len(self.problems) >= SHOWN_ALIKE:
                    passed_over, last = passed_over + 1, at
                elif name in self.said:
                    self.wrong(
                        _("“%(name)s” is written twice.") % {"name": name}, line=_line(text, at)
                    )
                else:
                    self.wrong(
                        _(
                            "“%(name)s” is not something a scheme can say. A scheme can "
                            "say: %(names)s."
                        )
                        % {"name": _shown(name), "names": ", ".join(NAMES)},
                        line=_line(text, at),
                    )
                continue
            self.said[name] = value
            self.lines[name] = _line(text, at)
            self.begins[name] = begins
        if passed_over:
            self.and_more(passed_over, line=_line(text, last))
        key = self.said.get("key")
        if isinstance(key, str) and _KEY.fullmatch(key) and len(key) <= MAX_KEY_LENGTH:
            self.key = key
            # Said before the key was known: the unknown names and the repeats.
            self.problems = [
                Problem(problem.reason, line=problem.line, key=key) for problem in self.problems
            ]
        for name, value in self.said.items():
            half = _half_in(value)
            if half:
                self.not_text.add(name)
                self.wrong(
                    _(
                        "“%(name)s” holds %(half)s, which is half of a character and not "
                        "text by itself. Write the character it was meant to be, or both "
                        "of its halves."
                    )
                    % {"name": name, "half": half},
                    name,
                )

    def wrong(self, reason, name: str = "", line: int = 0) -> None:
        self.problems.append(
            Problem(
                reason,
                line=line or self.lines.get(name, self.start),
                key=self.key,
                number=0 if self.key else self.number,
            )
        )

    def and_more(self, more: int, name: str = "", line: int = 0) -> None:
        """Say how many problems like the ones just spelt out were not spelt out."""
        self.wrong(
            ngettext_lazy(
                "And %(more)d more problem of the same kind in this scheme.",
                "And %(more)d more problems of the same kind in this scheme.",
                "more",
            )
            % {"more": more},
            name,
            line,
        )

    def says(self, name: str) -> bool:
        """Whether the scheme says this, in text: something that can be read."""
        return name in self.said and name not in self.not_text

    def scheme(self, taken: set[str]) -> Scheme | None:
        """The scheme, or nothing where anything at all is wrong with it."""
        said, says = self.said, self.says
        if says("key"):
            self.the_key(taken)
        for name in REQUIRED:
            if name not in said:
                self.wrong(_("“%(name)s” is missing.") % {"name": name})
        label = self.the_label() if says("label") else None
        subjects = self.the_subjects() if says("subjects") else None
        pattern = self.the_pattern() if says("pattern") else None
        links = {name: self.a_link(name) for name in ("link", "person_link") if says(name)}
        folds = {name: self.a_switch(name) for name in ("upper", "lower") if says(name)}
        if folds.get("upper") and folds.get("lower"):
            self.wrong(_("“upper” and “lower” cannot both be true."), "lower")
        elif pattern is not None:
            self.left_after_folding(folds)
        checksum = self.the_checksum() if says("checksum") else None
        example = ""
        if says("example"):
            example = _text(said["example"], MAX_VALUE_LENGTH)
            if example is None:
                self.one_line("example", MAX_VALUE_LENGTH)
        if self.problems:
            return None
        scheme = Scheme(
            self.key,
            label,
            pattern,
            subjects=subjects,
            link=links.get("link") or "",
            person_link=links.get("person_link") or "",
            example=example or "",
            upper=bool(folds.get("upper")),
            lower=bool(folds.get("lower")),
            checksum=checksum,
            checksum_message=_(
                "The check characters of that identifier do not match the rest of it, so "
                "one of them is a typo."
            ),
            provider=DEFINED_HERE,
            quoted=True,
            max_length=MAX_VALUE_LENGTH,
        )
        if example:
            written = scheme.normalise(example)
            if len(written) > MAX_VALUE_LENGTH or not pattern.match(written):
                self.wrong(_("the example is not a value its own pattern accepts."), "example")
            elif checksum is not None and not checksum(written):
                self.wrong(_("the example does not pass the checksum the scheme names."), "example")
        return None if self.problems else scheme

    def one_line(self, name: str, most: int) -> None:
        self.wrong(
            _(
                "“%(name)s” is text in double quotes, on one line, with something in it and "
                "%(most)d characters at most."
            )
            % {"name": name, "most": most},
            name,
        )

    def the_key(self, taken: set[str]) -> None:
        key = self.said["key"]
        if not self.key:
            self.wrong(
                _(
                    "a key is lower-case letters, digits and hyphens, in double quotes, "
                    "beginning with a letter or a digit, and %(most)d characters at most."
                )
                % {"most": MAX_KEY_LENGTH},
                "key",
            )
        elif key == OTHER or key in taken:
            self.wrong(
                _(
                    "this key is one of Postulo's own schemes, which are added to and "
                    "never changed. Choose another."
                ),
                "key",
            )

    def the_label(self):
        label = self.said["label"]
        if isinstance(label, dict) and label:
            return self.in_each_language()
        name = _text(label, MAX_NAME_LENGTH)
        if name is None:
            self.wrong(
                _(
                    "a label is a name in double quotes, %(most)d characters at most, or "
                    'one name for each language: {"en-GB": "Staff number", "pt-PT": '
                    '"Número de funcionário"}.'
                )
                % {"most": MAX_NAME_LENGTH},
                "label",
            )
        elif _unseen(name) is not None:
            self.wrong(_unseen(name), "label")
            return None
        return name

    def in_each_language(self):
        """A label written as one name for each language, as one that reads in the reader's.

        The parser hands over one name for each language and keeps the last where a
        language is written twice, which is the silence a name written twice in a scheme
        is refused for. So the object is walked again where it stands in the text, and a
        language that comes round a second time is a problem, as a name is.
        """
        names: dict[str, str] = {}
        met: set[str] = set()
        told, passed_over = len(self.problems), 0
        for tag, name, _at, _begins in _members(self.text, self.begins["label"]):
            if tag in met:
                reason = _("the language “%(tag)s” is written twice in “label”.") % {
                    "tag": _shown(tag)
                }
            elif met.add(tag) or not is_language_tag(tag):
                reason = _(
                    "“%(tag)s” is not a language code. One looks like en-GB, fr-FR or pt-BR."
                ) % {"tag": _shown(tag)}
            elif language_tag(tag) != tag:
                reason = _("the language code “%(tag)s” is written “%(written)s”.") % {
                    "tag": tag,
                    "written": language_tag(tag),
                }
            elif _text(name, MAX_NAME_LENGTH) is None:
                reason = _(
                    "the name in “%(tag)s” is text in double quotes, on one line, with "
                    "something in it and %(most)d characters at most."
                ) % {"tag": tag, "most": MAX_NAME_LENGTH}
            elif _unseen(name.strip()) is not None:
                reason = _("in “%(tag)s”, %(reason)s") % {
                    "tag": tag,
                    "reason": _unseen(name.strip()),
                }
            else:
                names[tag] = name.strip()
                continue
            # Spelt out ten times, and counted after that, as the names of a scheme are.
            if len(self.problems) - told >= SHOWN_ALIKE:
                passed_over += 1
            else:
                self.wrong(reason, "label")
        if passed_over:
            self.and_more(passed_over, "label")
        if len(self.problems) > told or not names:
            return None
        try:
            return in_languages(names)
        except ValueError:
            # Nothing gets this far that is not text; said all the same, and not raised.
            self.one_line("label", MAX_NAME_LENGTH)
            return None

    def left_after_folding(self, folds: dict[str, bool]) -> None:
        """Refuse a pattern that accepts nothing once what was typed has been folded.

        ``"upper": true`` over ``[a-z]{3}`` is a scheme that looks like one and refuses
        every value: what reaches the pattern is in capitals, and the pattern takes none.
        An example would have shown it, and an example is not required.
        """
        source = self.said["pattern"]
        if folds.get("upper") and not patterns.accepts_something(source, str.upper):
            self.wrong(
                _(
                    "“upper” puts what was typed into capitals before the pattern is "
                    "asked, and this pattern takes nothing written in capitals: the "
                    "scheme would accept no value at all. Write the pattern in capitals, "
                    "or take “upper” out."
                ),
                "upper",
            )
        if folds.get("lower") and not patterns.accepts_something(source, str.lower):
            self.wrong(
                _(
                    "“lower” puts what was typed into small letters before the pattern "
                    "is asked, and this pattern takes nothing written in small letters: "
                    "the scheme would accept no value at all. Write the pattern in small "
                    "letters, or take “lower” out."
                ),
                "lower",
            )

    def the_subjects(self) -> frozenset[str] | None:
        subjects = self.said["subjects"]
        if (
            not isinstance(subjects, list)
            or not subjects
            or any(not isinstance(subject, str) or subject not in SUBJECTS for subject in subjects)
        ):
            self.wrong(
                _(
                    '“subjects” lists what the scheme identifies: ["person"], '
                    '["company"] or ["person", "company"].'
                ),
                "subjects",
            )
            return None
        return frozenset(subjects)

    def the_pattern(self):
        source = self.said["pattern"]
        if not isinstance(source, str):
            self.wrong(_("“pattern” is text in double quotes."), "pattern")
            return None
        try:
            return patterns.compiled(source)
        except patterns.Refused as refused:
            self.wrong(
                _("the pattern cannot be used, at character %(at)d. %(reason)s")
                % {"at": refused.at, "reason": refused.reason},
                "pattern",
            )
            return None

    def a_link(self, name: str) -> str:
        try:
            if not isinstance(self.said[name], str):
                raise ValueError(str(_("A link is text in double quotes.")))
            return link_template(self.said[name])
        except ValueError as refused:
            self.wrong(
                _("“%(name)s” cannot be used. %(reason)s") % {"name": name, "reason": refused},
                name,
            )
            return ""

    def a_switch(self, name: str) -> bool:
        if self.said[name] is True or self.said[name] is False:
            return self.said[name]
        self.wrong(_("“%(name)s” is true or false, without quotes.") % {"name": name}, name)
        return False

    def the_checksum(self):
        named = self.said["checksum"]
        if isinstance(named, str) and named in CHECKSUMS:
            return CHECKSUMS[named]
        self.wrong(
            _(
                "“checksum” names one of the two Postulo knows: iso7064-mod11-2, which an "
                "ORCID ends in, or iso7064-mod97-10, which a legal-entity identifier ends "
                "in."
            ),
            "checksum",
        )
        return None


def read(text: str, *, taken: Iterable[str] = ()) -> Reading:
    """Read the definitions: the schemes with nothing wrong, and everything that is wrong.

    ``taken`` is the keys that are not an instance's to define: the ones Postulo ships.
    Text that is empty defines nothing and has nothing wrong with it.
    """
    text = text if isinstance(text, str) else ""
    if not text.strip():
        return Reading()
    if len(text) > MAX_CHARACTERS:
        return Reading(
            problems=(
                Problem(
                    _("the schemes can be %(most)d characters long at most.")
                    % {"most": MAX_CHARACTERS}
                ),
            )
        )
    try:
        whole = json.loads(text)
    except json.JSONDecodeError as error:
        return Reading(
            problems=(Problem(_not_json(error.msg), line=error.lineno, column=error.colno),)
        )
    except (RecursionError, ValueError):
        # Brackets a thousand deep, or a number of five thousand digits: neither is a list
        # of schemes, and neither may be more than a refusal.
        return Reading(problems=(Problem(_("this is not JSON.")),))
    first = _past_space(text, 0)
    if not isinstance(whole, list):
        return Reading(
            problems=(
                Problem(
                    _("the schemes are written as a list, between [ and ]."),
                    line=_line(text, first),
                ),
            )
        )
    if len(whole) > MAX_SCHEMES:
        return Reading(
            problems=(
                Problem(
                    _("%(most)d schemes can be defined here at most.") % {"most": MAX_SCHEMES},
                    line=_line(text, first),
                ),
            )
        )

    taken = set(taken)
    met: set[str] = set()
    schemes: list[Scheme] = []
    problems: list[Problem] = []
    for number, (start, item) in enumerate(_items(text), start=1):
        if not isinstance(item, dict):
            problems.append(
                Problem(
                    _("a scheme is written between { and }."),
                    line=_line(text, start),
                    number=number,
                )
            )
            continue
        one = _One(text, start, number)
        if one.key in met:
            one.wrong(_("this key is used by a scheme further up."), "key")
        elif one.key:
            met.add(one.key)
        scheme = one.scheme(taken)
        problems.extend(one.problems)
        if scheme is not None:
            schemes.append(scheme)
    problems.sort(key=lambda problem: problem.line)
    return Reading(schemes=tuple(schemes), problems=tuple(problems))
