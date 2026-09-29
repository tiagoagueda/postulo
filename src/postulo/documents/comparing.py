"""Two versions of one document, line against line (#236).

The model has said since it was written that the text is kept because "a PDF is awkward to
search and impossible to diff", and nothing compared anything: two versions of a CV could be
downloaded and held up to the light. This is the comparison, and it is deliberately the
plain one.

**Lines, from `difflib`, and nothing drawn by it.** `difflib.HtmlDiff` writes a table with
its colours in a `<style>` element and `nowrap` on every cell: the content security policy
refuses the first, a phone cannot read the second, and red against green is the whole of
what it says. So `SequenceMatcher` decides what changed and a template says it -- with a
sign, a border and a word a screen reader is given, none of which is a colour.

**Blank lines are not compared.** They are where a section ends on the page and say nothing
an employer read; left in, a section added shows as its heading, its lines and two changes
of nothing.

**A little of what surrounds a change, and a count of the rest.** A CV is sixty lines and a
change is usually one of them. Showing all sixty makes somebody using a screen reader listen
to fifty-nine to find it, so what is unchanged is left out beyond a few lines either side,
and the gap says how many lines it is.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

ADDED = "added"
REMOVED = "removed"
SAME = "same"

#: How many unchanged lines are kept either side of a change, so it can be placed.
CONTEXT = 2

#: What a list item is drawn with on the page. The text file writes a hyphen, and on a page
#: where a minus in front of a line means *removed*, a hyphen in front of an added one reads
#: as a contradiction.
ITEM = "• "


@dataclass(frozen=True)
class Line:
    """One line of either version, and which of them it is in."""

    change: str
    text: str


@dataclass(frozen=True)
class Passage:
    """A run of changes with a little of what is around them.

    ``skipped`` is how many unchanged lines were left out above it: what stands between
    this passage and the one before, or the top of the document.
    """

    lines: tuple[Line, ...]
    skipped: int = 0


@dataclass(frozen=True)
class Comparison:
    passages: tuple[Passage, ...]
    added: int = 0
    removed: int = 0
    #: Unchanged lines left out below the last passage.
    skipped_after: int = 0

    @property
    def differs(self) -> bool:
        return bool(self.added or self.removed)


def lines_of(text: str, *, bullet: str = "") -> list[str]:
    """The lines that say something, without the space either side of them.

    ``bullet`` is what the text writes in front of a list item, where it is known to: those
    lines are given `ITEM` instead. Only where it is known -- in a letter a line beginning
    with a hyphen is what somebody typed, and is left as they typed it.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not bullet:
        return lines
    return [ITEM + line.removeprefix(bullet) if line.startswith(bullet) else line for line in lines]


def compare(earlier: str, later: str, *, context: int = CONTEXT, bullet: str = "") -> Comparison:
    """What changed between two texts, as passages of lines.

    ``autojunk`` is off: its heuristic treats a line that recurs often as noise, and on a CV
    the line that recurs is an employer's name or *present*, which is exactly what somebody
    is looking for.
    """
    before = lines_of(earlier, bullet=bullet)
    after = lines_of(later, bullet=bullet)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)

    passages: list[Passage] = []
    added = removed = 0
    reached = 0  # how far into the earlier version the passages so far have got
    for group in matcher.get_grouped_opcodes(context):
        lines: list[Line] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(Line(SAME, text) for text in before[i1:i2])
                continue
            # What went, then what came: the order somebody reads a correction in.
            if tag in ("replace", "delete"):
                lines.extend(Line(REMOVED, text) for text in before[i1:i2])
                removed += i2 - i1
            if tag in ("replace", "insert"):
                lines.extend(Line(ADDED, text) for text in after[j1:j2])
                added += j2 - j1
        first, last = group[0], group[-1]
        passages.append(Passage(lines=tuple(lines), skipped=first[1] - reached))
        reached = last[2]

    return Comparison(
        passages=tuple(passages),
        added=added,
        removed=removed,
        skipped_after=(len(before) - reached) if passages else 0,
    )
