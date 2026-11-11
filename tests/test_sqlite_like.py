"""Plain text takes a shorter way through SQLite's ``LIKE``, and gets the same answer (#1134).

`core.sqlite_like` answers every ``LIKE`` on SQLite with a regular expression that folds the
case of every script (#505). For ASCII text asked one of the four plain questions the lookups
ask, it now answers with `str` methods instead. The regular expression is still there, as
`like_by_expression`, and is what every answer here is held to.
"""

from __future__ import annotations

import itertools
import random

import pytest
from django.db import connection

from postulo.core import sqlite_like
from postulo.core.sqlite_like import like, like_by_expression

#: Letters outside ASCII whose case folds onto an ASCII letter, or nearly.
KELVIN = "\N{KELVIN SIGN}"  # which is a k to the regular expression
LONG_S = "\N{LATIN SMALL LETTER LONG S}"  # which is an s to it
DOTTED_I = "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}"  # two code points in lower case
DOTLESS_I = "\N{LATIN SMALL LETTER DOTLESS I}"
SHARP_S = "\N{LATIN SMALL LETTER SHARP S}"

PATTERNS = (
    # The four shapes, in both cases, and the empty ones.
    "%zurich%",
    "zurich%",
    "%zurich",
    "zurich",
    "%ZURICH%",
    "Zurich%",
    "%AG",
    "",
    "%",
    "%%",
    "%%zurich%%",
    # Wildcards that are not at an end.
    "_",
    "%z_rich%",
    "_urich",
    "%zu%rich%",
    "z%h",
    # The escape character: before a wildcard, before a letter, doubled, and last.
    "%50\\%%",
    "%a\\_b%",
    "50\\%",
    "\\%zurich",
    "%\\zurich%",
    "%\\\\%",
    "zurich\\",
    "%50!%%",
    "%a!_b%",
    "zurich!",
    # What a regular expression would read as syntax, and white space.
    "%.*%",
    "%[a]%",
    "%(%",
    "%a b%",
    "%line\nbreak%",
    # Letters whose folding touches the ones outside ASCII.
    "%k%",
    "%s%",
    "%i%",
    "%I%",
    "%ss%",
    "%strasse%",
    f"%{KELVIN}%",
    f"%{LONG_S}%",
    f"%{DOTTED_I}%",
    f"%{DOTLESS_I}%",
    f"%{SHARP_S}%",
    # The examples of #505.
    "%école%",
    "%ÉCOLE%",
    "école%",
    "%αθηναϊκή%",
    "%škoda",
    # Not text at all.
    None,
    5,
    50.5,
    b"zurich",
    "%5%",
    "5",
    "%b'zurich'%",
)

VALUES = (
    "",
    "zurich",
    "Zurich",
    "ZURICH AG",
    "Head office in Zurich",
    "Zurich Insurance Group",
    "Zürich",
    "zu rich",
    "zh",
    "a",
    "50% off",
    "500 off",
    "50%",
    "a_b",
    "axb",
    "%zurich",
    "\\zurich",
    "zurich\\",
    "zurich!",
    "a\\b",
    ".*",
    "[a]",
    "(",
    "a b",
    "one line\nbreak here",
    "k",
    "K",
    KELVIN,
    f"12 {KELVIN}",
    "s",
    "ss",
    "SS",
    LONG_S,
    f"mi{LONG_S}t",
    "Strasse",
    f"Stra{SHARP_S}e",
    SHARP_S,
    "i",
    "I",
    DOTTED_I,
    f"{DOTTED_I}stanbul",
    DOTLESS_I,
    "École Polytechnique",
    "ecole",
    "Αθηναϊκή Εταιρεία",
    "Škoda",
    None,
    5,
    1050,
    50.5,
    b"zurich",
    b"\xff\x00",
    "5",
    "b'zurich'",
)

ESCAPES = (None, "", "\\", "!")


def both(pattern, value, escape):
    """The answer, once it is seen to be the regular expression's: same value, same type."""
    shorter = like(pattern, value, escape)
    expected = like_by_expression(pattern, value, escape)
    assert shorter == expected, (pattern, value, escape)
    assert type(shorter) is type(expected), (pattern, value, escape)
    return shorter


@pytest.fixture
def reached(monkeypatch):
    """Every call `like` answered by the regular expression: the pattern and the text it read.

    `like` looks a pattern up once and is handed both ways to answer it; the expression's
    is wrapped here on its way out, so the lookup's own memory holds nothing of the test's.
    """
    calls = []
    read = sqlite_like._read

    def counted(pattern, escape):
        question, literal, fullmatch = read(pattern, escape)

        def by_expression(text):
            calls.append((pattern, text))
            return fullmatch(text)

        return question, literal, by_expression

    monkeypatch.setattr(sqlite_like, "_read", counted)
    return calls


def test_plain_text_does_not_reach_the_regular_expression(reached):
    """Every row and column searched cost a Unicode regular expression, ASCII or not (#1134)."""
    for pattern in ("%zurich%", "zurich%", "%zurich", "zurich", "%50\\%%"):
        for value in ("Zurich Insurance Group", "zurich", "Geneva", "50% off", ""):
            like(pattern, value, "\\")
    assert reached == []

    # Everything else goes the way it always went: a letter outside ASCII on either side,
    # a wildcard that is not at an end, and a value that is not text, read as `str` has it.
    others = (
        ("%zurich%", "Zürich"),
        ("%zürich%", "Zurich"),
        ("%z_rich%", "Zurich"),
        ("%zu%rich%", "Zurich"),
        ("%5%", 1050),
        ("%zurich%", b"zurich"),
    )
    for pattern, value in others:
        like(pattern, value, "\\")
    assert reached == [(pattern, str(value)) for pattern, value in others]


@pytest.mark.parametrize(
    ("pattern", "value", "escape", "answer"),
    [
        ("%zurich%", "Head office in ZURICH", "\\", 1),
        ("%zurich%", "Geneva", "\\", 0),
        ("%ZURICH%", "zurich", "\\", 1),
        ("zurich%", "Zurich AG", "\\", 1),
        ("zurich%", "In Zurich", "\\", 0),
        ("%ag", "Zurich AG", "\\", 1),
        ("%ag", "Agfa", "\\", 0),
        ("zurich", "ZURICH", "\\", 1),
        ("zurich", "Zurich AG", "\\", 0),
        ("", "", None, 1),
        ("", "a", None, 0),
        ("%", "", None, 1),
        ("%", "anything\nat all", None, 1),
        # An escaped wildcard is the character itself; unescaped it is a wildcard still.
        ("%50\\%%", "50% off", "\\", 1),
        ("%50\\%%", "500 off", "\\", 0),
        ("%50!%%", "50% off", "!", 1),
        ("%a\\_b%", "a_b", "\\", 1),
        ("%a\\_b%", "axb", "\\", 0),
        ("%a_b%", "axb", "\\", 1),
        # Without an escape character a backslash is text.
        ("%a\\_b%", "a\\xb", None, 1),
        ("zurich\\", "zurich\\", "\\", 1),
        # The letters of #505, and the ones whose folding crosses into ASCII.
        ("%école%", "École Polytechnique", "\\", 1),
        ("%ÉCOLE%", "École Polytechnique", "\\", 1),
        ("%αθηναϊκή%", "Αθηναϊκή Εταιρεία", "\\", 1),
        ("%k%", KELVIN, "\\", 1),
        ("%s%", LONG_S, "\\", 1),
        ("%i%", DOTTED_I, "\\", 1),
        ("%strasse%", f"Stra{SHARP_S}e", "\\", 0),
        # Nothing to compare, and what is not text.
        ("%zurich%", None, "\\", None),
        (None, "zurich", "\\", None),
        ("%5%", 1050, "\\", 1),
        (5, "5", None, 1),
    ],
)
def test_the_answers_a_search_depends_on(pattern, value, escape, answer):
    """What `LIKE` says, by either way: the fold of #505 is kept and plain text reads the same."""
    assert both(pattern, value, escape) == answer


def test_the_shorter_way_answers_as_the_regular_expression_does(reached):
    """Every pattern against every value under every escape character: no answer differs.

    The table holds the four shapes and what is near them, the escape character wherever it
    can stand, empty strings, `NULL`, numbers and blobs, and the letters outside ASCII that a
    case-insensitive expression takes for an ASCII one -- which is why a value that holds
    one never takes the shorter way.
    """
    cases = list(itertools.product(PATTERNS, VALUES, ESCAPES))
    answers = [both(pattern, value, escape) for pattern, value, escape in cases]

    # `reached` holds the calls `like` passed on; the rest it answered the shorter way.
    assert len(cases) - len(reached) > 2000, "plain text, which is what is being proved"
    assert len(reached) > 2000, "and the rest still goes to the expression"
    assert answers.count(1) > 500 and answers.count(0) > 500 and answers.count(None) > 100


def test_a_seeded_sweep_finds_no_pair_the_two_ways_disagree_on(reached):
    """Twenty thousand made-up patterns and values, the same ones every run (#1134).

    Drawn from few letters, so that many match, with the wildcards, both escape characters
    and now and then a letter outside ASCII among them.
    """
    draw = random.Random(1134)  # noqa: S311 - draws test values, guards nothing
    letters = "abAB zZ"
    marks = "%_\\!"
    beyond = "éÉ" + LONG_S + KELVIN + DOTTED_I + SHARP_S

    def text(longest: int, odd: float) -> str:
        out = []
        for _ in range(draw.randint(0, longest)):
            chance = draw.random()
            if chance < odd:
                out.append(draw.choice(beyond))
            elif chance < odd + 0.15:
                out.append(draw.choice(marks))
            else:
                out.append(draw.choice(letters))
        return "".join(out)

    answers = []
    for _ in range(20_000):
        pattern = text(4, 0.03)
        if draw.random() < 0.7:
            pattern = draw.choice(("%", "")) + pattern + draw.choice(("%", ""))
        answers.append(both(pattern, text(6, 0.03), draw.choice(ESCAPES)))

    assert 20_000 - len(reached) > 8000, "worth nothing if it rarely takes the shorter way"
    assert len(reached) > 2000
    assert answers.count(1) > 2000 and answers.count(0) > 2000


@pytest.mark.django_db
def test_a_query_asks_the_regular_expression_for_the_rows_that_need_it(user, reached):
    """Through the database: the function a connection is given is the one with both ways."""
    from postulo.jobs.models import Company

    if connection.vendor != "sqlite":
        pytest.skip("PostgreSQL folds case itself and is given no function")
    for name in ("Zurich Insurance", "Zürich Versicherung", "École Polytechnique", "Geneva SA"):
        Company.objects.create(owner=user, name=name)
    companies = Company.objects.filter(owner=user)
    reached.clear()

    def names(**lookup) -> list[str]:
        return sorted(companies.filter(**lookup).values_list("name", flat=True))

    assert names(name__icontains="zurich") == ["Zurich Insurance"]
    assert names(name__istartswith="GENEVA") == ["Geneva SA"]
    assert names(name__iendswith="insurance") == ["Zurich Insurance"]
    assert names(name__iexact="geneva sa") == ["Geneva SA"]
    assert {text for _pattern, text in reached} == {
        "Zürich Versicherung",
        "École Polytechnique",
    }, "the two names with a letter outside ASCII, and neither of the plain ones"

    assert names(name__icontains="école") == ["École Polytechnique"]
    assert names(name__icontains="ZÜRICH") == ["Zürich Versicherung"]
