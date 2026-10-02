"""What a value has to look like, for a scheme an instance defines for itself (#311).

A scheme Postulo ships carries a regular expression somebody reviewed. A scheme an
administrator types into *Server settings* cannot: its pattern is run against every value
everybody types, in their request, and Python's ``re`` has no time limit -- one repetition
inside another, or three that overlap, and a crafted value keeps a worker for minutes. So an
administrator's pattern is **not** a regular expression. It is written in the small language
below, which looks like the part of one that an identifier needs, and Postulo translates it
into a compiled ``re`` pattern that cannot go back over what it has read.

**The language.** A pattern is one or more *alternatives* separated by ``|``, and a value
has to be the whole of one of them. An alternative is a row of *pieces*, each of them:

- a **character**, standing for itself: ``A``, ``-``, a space, ``é``. One of
  ``\\ ^ $ . | ? * + ( ) [ ] { }`` is written with a backslash before it;
- a **class**, standing for one character out of several: ``[A-Z]``, ``[0-9a-f]``,
  ``[A-Za-zÀ-ÿ]``, ``[^ /]`` for any character but those, and the two with names, ``\\d``
  for a digit ``0`` to ``9`` and ``\\w`` for a letter ``A`` to ``Z`` or ``a`` to ``z``, a
  digit or an underscore. Neither name reaches beyond ASCII, and no class, however it is
  written, takes a control character;
- either of those with a **count** after it: ``{4}`` for exactly four, ``{2,5}`` for two
  to five, ``?`` for one or none. A count is a hundred at most, which is as long as an
  identifier can be;
- a **fixed separator** in round brackets, optional when a ``?`` follows: ``( - )?``. The
  brackets hold characters and nothing else.

``^`` at the very start and ``$`` at the very end are allowed and change nothing, because
that is how the patterns people copy are written.

**What it has not got, and why.** No ``*``, ``+`` or ``{2,}``: a count with no largest
number is the repetition that has no bound. No group but the fixed separator, so nothing
that holds a count or a choice can itself be counted, which is the shape -- ``(a+)+`` -- that
takes time doubling with every character. No ``|`` inside brackets, no reference back to
what was read (``\\1``), no looking ahead or behind (``(?=…)``), no ``.``: a full stop means
*any character* in a regular expression and itself in an identifier, and a language that
picked one would surprise half the people who typed it. ``[^]``, which is the same thing
written as a class that leaves nothing out, is refused with it.

**One reading, and so one pass.** Leaving those out is not enough by itself. ``[a-z]{0,50}``
written five times in a row has no repetition inside a repetition, and a backtracking
matcher still tries every way of sharing a run of letters between the five. So a pattern is
refused unless each piece with a choice to make -- a count that is a range, an optional
separator -- can make it by looking at one character: nothing that may follow it can begin
with a character it could take itself. ``[A-Z]{1,3}-\\d{4}`` passes, because a hyphen is not
a letter; ``[A-Z]{1,3}[A-Z0-9]{4}`` does not, because where the first piece stops is a
matter of opinion. For a pattern that passes there is at most one way to read any value, so
taking as much as each piece may and never giving any back is not an approximation of the
pattern: it is the pattern. That is how it is compiled -- every count *possessive*, which
``re`` has had since Python 3.11 -- and a matcher that never gives a character back reads a
value once: a step per character and per piece, for each alternative, and there are twelve
of those at most. The analysis is what makes the possessive reading correct; the possessive
reading is what makes the time linear even if the analysis were wrong.

**And a pattern is quick to prepare, which is a different thing.** Reading a value is one
pass; turning a pattern into something that reads is done once for each scheme whenever the
text is read, and ``re`` does it for a class by walking the characters the class is written
with, one at a time. So a class is written for ``re`` in whichever of two ways is the
shorter -- the characters it takes, or, for one that takes most of them, the characters it
leaves out, which is the same class -- and the classes of one pattern have a budget between
them (`MAX_CLASS_WEIGHT`). Neither changes what a pattern means or how a value is read.

Whatever is refused is refused with the reason and the character it was met at, when the
definitions are saved. Nothing here is asked again when a value is checked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from django.utils.translation import gettext_lazy as _

#: How long a pattern may be, in characters. Generous for an identifier, and what bounds the
#: pieces a value is read against.
MAX_PATTERN_LENGTH = 300

#: The largest count a piece may carry: as long as an identifier can be, which is what the
#: column holds (`core.identifiers.MAX_VALUE_LENGTH`).
MAX_COUNT = 100

#: How many alternatives a pattern may offer. Each is read once, so this is the factor.
MAX_ALTERNATIVES = 12

#: What the classes of one pattern may cost between them to prepare (`_weight`).
#:
#: Reading a value is linear whatever the pattern; *preparing* a pattern is not free, and it
#: is done for every scheme of a text when the text is saved and again by each process the
#: first time a page asks, inside somebody's request. ``re`` prepares a class by walking the
#: characters it is written with one at a time, in Python, as far as U+FFFF: about 40
#: nanoseconds a character, so 2.4 ms for a class written as nearly the whole plane, and 75
#: of those in one pattern was a quarter of a second -- 18 to 31 seconds for a text of a
#: hundred such patterns, every one of them inside the bounds on its length. The length of
#: a text bounds nothing here; this does.
#:
#: One plane's worth of walking to a pattern: about 3 ms. That is room for every ideograph
#: three times over in one pattern, every Hangul syllable five times, a class such as
#: ``[^ ]`` twelve times, and far more classes of letters and digits than a pattern has room
#: to write. A hundred schemes, each with the costliest pattern this lets through and the
#: rest of its 300 characters in narrow classes, were read in 0.33 to 0.66 seconds of
#: work on the machine this was written on, and the patterns it refuses in under 0.15;
#: `tests/test_identifier_schemes.py` builds those texts and holds the reading to a stated
#: time, so the bound is measured and not supposed. At twice this budget the same texts
#: took 0.7 to 1.4 seconds, which is too long to spend inside a request.
MAX_CLASS_WEIGHT = 0x10000

#: What a class costs beyond the characters it is written with. One that reaches past
#: U+00FF has ``re`` lay out the whole plane to mark it on, some 500 characters' worth of
#: walking; one of more than two runs is then kept as a map of that plane, about a tenth of
#: a millisecond, which is 2,500 more. Every class written as what it leaves out is both.
_BEYOND_LATIN_1 = 500
_A_MAP_OF_THE_PLANE = 2_500

#: One character out of several, as the code points it may be: sorted, apart, and whole.
Ranges = tuple[tuple[int, int], ...]

_EVERYTHING: Ranges = ((0x00, 0x10FFFF),)

#: The last character of the Basic Multilingual Plane, which is as far as ``re`` walks a
#: class one character at a time. What lies beyond is kept as the ranges it was written as.
_PLANE_ENDS = 0xFFFF

#: What no class takes, however it is written: the C0 and C1 controls and DEL, the two
#: characters that end a line where a new line does not, and the surrogates, which are not
#: characters. ``[^ ]`` reads as *anything but a space* and still means *anything a person
#: can type on one line*.
_NEVER: Ranges = ((0x00, 0x1F), (0x7F, 0x9F), (0x2028, 0x2029), (0xD800, 0xDFFF))

#: The two classes with names. ASCII, both: a digit in another script passes ``int()`` and
#: builds a link that goes nowhere (#638).
_NAMED: dict[str, Ranges] = {
    "d": ((0x30, 0x39),),
    "w": ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)),
}

#: The characters the language itself is written with. One of these stands for itself only
#: with a backslash before it.
_MARKS = frozenset("\\^$.|?*+()[]{}")

_COUNT = re.compile(r"([0-9]{1,4})(,)?([0-9]{1,4})?")


class Refused(ValueError):
    """A pattern that cannot be used: why, and the character it was met at."""

    def __init__(self, reason, at: int) -> None:
        super().__init__(str(reason))
        #: The sentence, for whoever typed the pattern.
        self.reason = reason
        #: Which character of the pattern, counted from one.
        self.at = at


@dataclass(frozen=True)
class Piece:
    """One piece of an alternative: a character or a class with its count, or a separator."""

    #: Where it begins in the pattern, counted from one.
    at: int
    #: What one character of it may be. For a separator, what its first character is: the
    #: only one that decides whether it is there.
    chars: Ranges
    least: int = 1
    most: int = 1
    #: A separator's characters, as they are; empty for a character or a class.
    text: str = ""

    @property
    def settled(self) -> bool:
        """Whether there is nothing for it to decide: it is there a fixed number of times."""
        return self.least == self.most

    @property
    def needs(self) -> int:
        """How many characters of a value it takes at the least."""
        return self.least * (len(self.text) or 1)


# ------------------------------------------------------------------- sets of characters


def _tidied(ranges) -> Ranges:
    """The same characters as ranges in order, with those that touch joined."""
    joined: list[tuple[int, int]] = []
    for low, high in sorted(ranges):
        if joined and low <= joined[-1][1] + 1:
            joined[-1] = (joined[-1][0], max(joined[-1][1], high))
        else:
            joined.append((low, high))
    return tuple(joined)


def _without(ranges: Ranges, taken: Ranges) -> Ranges:
    """What is left of ``ranges`` once ``taken`` is removed. Both tidied."""
    left: list[tuple[int, int]] = []
    for low, high in ranges:
        for cut_low, cut_high in taken:
            if cut_high < low or cut_low > high:
                continue
            if cut_low > low:
                left.append((low, cut_low - 1))
            low = cut_high + 1
            if low > high:
                break
        if low <= high:
            left.append((low, high))
    return tuple(left)


def _meet(one: Ranges, other: Ranges) -> bool:
    """Whether any character is in both."""
    return any(low <= top and bottom <= high for low, high in one for bottom, top in other)


def _just(character: str) -> Ranges:
    return ((ord(character), ord(character)),)


def _cost(ranges: Ranges) -> int:
    """What a class written as these ranges costs ``re`` to prepare, in characters walked:
    each one it names as far as U+FFFF, and what the plane costs to lay out and to keep."""
    in_the_plane = [(low, min(high, _PLANE_ENDS)) for low, high in ranges if low <= _PLANE_ENDS]
    cost = sum(high - low + 1 for low, high in in_the_plane)
    if ranges[-1][1] > 0xFF:
        cost += _BEYOND_LATIN_1
        if len(in_the_plane) > 2:
            cost += _A_MAP_OF_THE_PLANE
    return cost


@lru_cache(maxsize=256)
def _as_written(chars: Ranges) -> tuple[bool, Ranges]:
    """How a class is spelt for ``re``: whether as what it leaves out, and the ranges.

    A class that takes most characters -- ``[^ ]``, or one range across most of the plane
    -- is written as the ones it does not take, which is the same class and a fraction of
    the walking. What it leaves out always holds what no class takes (`_NEVER`), because
    what it takes never does: so ``[^…]`` spelt from this still refuses a line break and
    half of a character, and needs nothing beside it to say so.
    """
    left_out = _without(_EVERYTHING, chars)
    if _cost(left_out) < _cost(chars):
        return True, left_out
    return False, chars


def _weight(chars: Ranges) -> int:
    """What a class costs ``re`` to prepare, as it will be written (`MAX_CLASS_WEIGHT`).

    One character is no class at all to ``re``, and costs nothing.
    """
    if len(chars) == 1 and chars[0][0] == chars[0][1]:
        return 0
    return _cost(_as_written(chars)[1])


# ------------------------------------------------------------------------- the reading


class _Reader:
    """A pattern read from its first character to its last, once."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.start = 1 if source.startswith("^") else 0
        self.end = len(source)
        if source.endswith("$"):
            # A `$` that a backslash makes a character is not the end mark.
            before = len(source) - 1
            slashes = before - len(source[:before].rstrip("\\"))
            if slashes % 2 == 0:
                self.end -= 1

    def refuse(self, reason, index: int):
        raise Refused(reason, index + 1)

    def alternatives(self) -> tuple[tuple[Piece, ...], ...]:
        source, index = self.source, self.start
        found: list[tuple[Piece, ...]] = []
        pieces: list[Piece] = []
        if index >= self.end:
            self.refuse(_("A pattern cannot be empty."), 0)
        while index < self.end:
            character = source[index]
            if character == "|":
                if not pieces:
                    self.refuse(_("One side of this “|” is empty."), index)
                found.append(tuple(pieces))
                pieces = []
                index += 1
                if len(found) >= MAX_ALTERNATIVES:
                    self.refuse(
                        _("A pattern offers %(most)d alternatives at most.")
                        % {"most": MAX_ALTERNATIVES},
                        index - 1,
                    )
                continue
            began = index
            if character == "(":
                piece, index = self.separator(index)
                pieces.append(piece)
                continue
            if character == "[":
                chars, index = self.a_class(index)
            elif character == "\\":
                chars, index = self.escaped(index)
            elif character in "*+?{":
                self.refuse(_("There is nothing before this for it to count."), index)
            elif character in ")]}":
                self.refuse(_("This closes a bracket that was never opened."), index)
            elif character == ".":
                self.refuse(
                    _(
                        "A full stop means any character in a regular expression, and that "
                        "is not part of this language. Write “\\.” for a full stop, or a "
                        "class such as [^ ] for any character but a space."
                    ),
                    index,
                )
            elif character in "^$":
                self.refuse(
                    _(
                        "“^” and “$” are allowed only at the two ends of a pattern, where "
                        "they change nothing: a pattern always describes the whole value."
                    ),
                    index,
                )
            else:
                chars, index = _just(character), index + 1
            least, most, index = self.count(index)
            pieces.append(Piece(began + 1, chars, least, most))
        if not pieces:
            self.refuse(_("One side of this “|” is empty."), self.end - 1)
        found.append(tuple(pieces))
        return tuple(found)

    def count(self, index: int) -> tuple[int, int, int]:
        """The count written at ``index``, if one is: least, most, and where it ends."""
        source = self.source
        if index >= self.end:
            return 1, 1, index
        character = source[index]
        if character in "*+":
            self.refuse(
                _(
                    "“*” and “+” repeat without a limit, and a pattern has to say how many "
                    "times at the most: write {0,20} or {1,20}."
                ),
                index,
            )
        if character == "?":
            least, most, after = 0, 1, index + 1
        elif character == "{":
            close = source.find("}", index, self.end)
            if close < 0:
                self.refuse(_("This bracket is never closed."), index)
            written = _COUNT.fullmatch(source[index + 1 : close])
            if written is None:
                self.refuse(
                    _("A count is written {3}, or {2,5} with the smaller number first."), index
                )
            if written[2] and written[3] is None:
                self.refuse(
                    _(
                        "A count needs a largest number: {2,} repeats without a limit. "
                        "Write {2,20}."
                    ),
                    index,
                )
            least = int(written[1])
            most = int(written[3]) if written[2] else least
            if most > MAX_COUNT:
                self.refuse(
                    _(
                        "Nothing can be repeated more than %(most)d times: no identifier "
                        "is longer than that."
                    )
                    % {"most": MAX_COUNT},
                    index,
                )
            if least > most or most < 1:
                self.refuse(
                    _("A count is written {3}, or {2,5} with the smaller number first."), index
                )
            after = close + 1
        else:
            return 1, 1, index
        if after < self.end and source[after] in "*+?{":
            self.refuse(
                _(
                    "Only one count may follow a character or a class: “{2}?”, “??” and "
                    "“{2}+” are not part of this language."
                ),
                after,
            )
        return least, most, after

    def escaped(self, index: int) -> tuple[Ranges, int]:
        """What the backslash at ``index`` and the character after it stand for."""
        if index + 1 >= self.end:
            self.refuse(_("A pattern cannot end in a backslash."), index)
        character = self.source[index + 1]
        if character in _NAMED:
            return _NAMED[character], index + 2
        if character.isascii() and character.isdigit():
            self.refuse(
                _(
                    "A backslash and a number refer back to what was read earlier, and a "
                    "pattern cannot do that."
                ),
                index,
            )
        if character.isascii() and character.isalpha():
            self.refuse(
                _(
                    "“\\%(letter)s” is not part of this language. The classes with a name "
                    "are \\d, a digit, and \\w, a letter, a digit or an underscore; a "
                    "backslash before a punctuation mark stands for the mark itself."
                )
                % {"letter": character},
                index,
            )
        return _just(character), index + 2

    def a_class(self, start: int) -> tuple[Ranges, int]:
        """The class opening at ``start``: what it takes, and where it ends."""
        source, index = self.source, start + 1
        negated = source[index : index + 1] == "^" and index < self.end
        if negated:
            index += 1
        members: list[tuple[int, int]] = []
        while True:
            if index >= self.end:
                self.refuse(_("This bracket is never closed."), start)
            character = source[index]
            if character == "]":
                break
            member_at = index
            if character == "\\":
                first, index = self.escaped(index)
            else:
                first, index = _just(character), index + 1
            one = len(first) == 1 and first[0][0] == first[0][1]
            if one and index + 1 < self.end and source[index] == "-" and source[index + 1] != "]":
                index += 1
                if source[index] == "\\":
                    last, index = self.escaped(index)
                else:
                    last, index = _just(source[index]), index + 1
                if len(last) != 1 or last[0][0] != last[0][1] or last[0][0] < first[0][0]:
                    self.refuse(
                        _(
                            "A range runs from one character to a later one: “%(range)s” "
                            "is the wrong way round, or does not end in a character."
                        )
                        % {"range": source[member_at:index]},
                        member_at,
                    )
                members.append((first[0][0], last[0][0]))
            else:
                members.extend(first)
        chars = _tidied(members)
        if negated and not chars:
            # What a full stop means in a regular expression, written another way, and
            # refused for the reason a full stop is.
            self.refuse(
                _(
                    "“[^]” leaves nothing out, so it means any character, and that is not "
                    "part of this language. Write a class such as [^ ] for any character "
                    "but a space."
                ),
                start,
            )
        if negated:
            chars = _without(_EVERYTHING, chars)
        chars = _without(chars, _NEVER)
        if not chars:
            self.refuse(_("This class takes no character at all."), start)
        return chars, index + 1

    def separator(self, start: int) -> tuple[Piece, int]:
        """The fixed separator opening at ``start``, and where it ends."""
        source, index = self.source, start + 1
        if source[index : index + 1] == "?" and index < self.end:
            self.refuse(
                _(
                    "“(?” begins a group this language does not have: nothing looks ahead "
                    "or behind, and no group has a name."
                ),
                start,
            )
        only = _(
            "Round brackets hold a fixed separator, characters to be found as they are, "
            "and nothing else: no class, no count and no “|”."
        )
        text: list[str] = []
        while True:
            if index >= self.end:
                self.refuse(_("This bracket is never closed."), start)
            character = source[index]
            if character == ")":
                break
            if character == "\\":
                chars, after = self.escaped(index)
                if len(chars) != 1 or chars[0][0] != chars[0][1]:
                    self.refuse(only, index)
                text.append(chr(chars[0][0]))
                index = after
            elif character in _MARKS:
                self.refuse(only, index)
            else:
                text.append(character)
                index += 1
        if not text:
            self.refuse(only, start)
        index += 1
        optional = index < self.end and source[index] == "?"
        if optional:
            index += 1
        if index < self.end and source[index] in "*+?{":
            self.refuse(_("A group cannot repeat: “?” may follow it, and nothing else may."), index)
        return Piece(start + 1, _just(text[0]), 0 if optional else 1, 1, "".join(text)), index


def read(source: str) -> tuple[tuple[Piece, ...], ...]:
    """A pattern as its alternatives, each a row of pieces, or `Refused`.

    Everything the module's first paragraphs promise is checked here: the language, the
    bounds, and that every piece with something to decide can decide it on one character.
    """
    if not isinstance(source, str):
        raise Refused(_("A pattern cannot be empty."), 1)
    if len(source) > MAX_PATTERN_LENGTH:
        raise Refused(
            _("A pattern is %(most)d characters long at most.") % {"most": MAX_PATTERN_LENGTH},
            MAX_PATTERN_LENGTH + 1,
        )
    for index, character in enumerate(source):
        if _meet(_just(character), _NEVER):
            raise Refused(
                _("A pattern cannot hold a line break, a tab or any other control character."),
                index + 1,
            )
    alternatives = _Reader(source).alternatives()
    spent = 0
    for pieces in alternatives:
        for piece in pieces:
            spent += 0 if piece.text else _weight(piece.chars)
            if spent > MAX_CLASS_WEIGHT:
                raise Refused(
                    _(
                        "The classes of this pattern take too long to prepare between "
                        "them. A class written with “^”, or one that takes thousands of "
                        "characters such as [一-鿿], can stand in one pattern only so "
                        "many times: use fewer of them, or narrower ones."
                    ),
                    piece.at,
                )
    for pieces in alternatives:
        if sum(piece.needs for piece in pieces) > MAX_COUNT:
            raise Refused(
                _(
                    "This asks for more than %(most)d characters, and no identifier is "
                    "longer than that."
                )
                % {"most": MAX_COUNT},
                pieces[0].at,
            )
        for position, piece in enumerate(pieces):
            if piece.settled:
                continue
            # What the value may go on with once this piece has stopped: the pieces after
            # it, as far as the first one that has to be there.
            follows: list[tuple[int, int]] = []
            for later in pieces[position + 1 :]:
                follows.extend(later.chars)
                if later.least:
                    break
            if _meet(piece.chars, tuple(follows)):
                raise Refused(
                    _(
                        "What comes after this can begin with a character this could take "
                        "as well, so a value could be read in two ways. Give this an exact "
                        "count, or put a separator after it that it cannot take."
                    ),
                    piece.at,
                )
    return alternatives


# ------------------------------------------------------------------- and the compiling


def _written(code: int) -> str:
    """One code point as ``re`` reads it anywhere: by number, so that nothing is a mark."""
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def _one_of(chars: Ranges) -> str:
    """A class as ``re`` reads it: the characters it takes, or the ones it leaves out,
    whichever is the fewer to walk (`_as_written`). The same class either way."""
    negated, ranges = _as_written(chars)
    return (
        ("[^" if negated else "[")
        + "".join(
            _written(low) if low == high else f"{_written(low)}-{_written(high)}"
            for low, high in ranges
        )
        + "]"
    )


def _rendered(piece: Piece) -> str:
    """A piece as ``re`` runs it: possessive wherever it has a choice, so nothing it took
    is ever given back."""
    if piece.text:
        fixed = "(?:" + "".join(_written(ord(character)) for character in piece.text) + ")"
        return fixed if piece.least else fixed + "?+"
    if piece.settled:
        return _one_of(piece.chars) + ("" if piece.most == 1 else f"{{{piece.most}}}")
    return f"{_one_of(piece.chars)}{{{piece.least},{piece.most}}}+"


def compiled(source: str) -> re.Pattern[str]:
    """The pattern as a compiled ``re`` one that reads a value once, or `Refused`.

    Anchored at both ends, so ``match`` and ``fullmatch`` say the same thing about a value.
    """
    alternatives = read(source)
    return re.compile(
        r"\A(?:"
        + "|".join("".join(_rendered(piece) for piece in pieces) for pieces in alternatives)
        + r")\Z"
    )


# ------------------------------------------------ what is left of a pattern after a fold


def _left_after(piece: Piece, fold: Callable[[str], str]) -> bool:
    """Whether the piece can still be met by a value ``fold`` has been through: whether one
    character it takes, or its separator, is something the fold leaves as it is."""
    if piece.text:
        return fold(piece.text) == piece.text
    # Every character the fold changes is a letter of the other case, and there are a few
    # thousand of those in all: a range that long has one it leaves alone, so this is short.
    return any(
        fold(chr(code)) == chr(code) for low, high in piece.chars for code in range(low, high + 1)
    )


def accepts_something(source: str, fold: Callable[[str], str]) -> bool:
    """Whether the pattern accepts any value at all once ``fold`` has been applied to it.

    A scheme may fold what was typed to capitals, or to small letters, before the pattern
    is asked. A pattern of small letters under a fold to capitals then accepts nothing,
    whatever is typed, and looks like a scheme. One alternative is enough: each piece that
    has to be there needs one character the fold would have left alone.
    """
    return any(
        all(_left_after(piece, fold) for piece in pieces if piece.least) for pieces in read(source)
    )
