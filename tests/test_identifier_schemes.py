"""The identifier schemes an instance defines for itself, in JSON (#311).

An administrator writes them on *Server settings → Plugins*; they join the registry after
the ones Postulo ships. Everything the issue settled is held here:

- what the JSON can say, and that anything else is refused **by name and by line**;
- that a pattern is not a regular expression, and that every construct the pattern
  language accepts reads a value once -- timed, on values built to be awkward;
- that Postulo's own schemes are added to and never changed;
- that deleting a scheme loses no identifier, and defining it again brings them back;
- where the definitions are kept, that changing them costs no query of its own and is in
  force on the next request, and that a row which did not come through the page is held to
  the page's rules;
- that the label, the example and the link, which are drawn for everybody, cannot carry
  markup, leave their host, or run a script;
- who may write them, and what the page says when the text is refused;
- and what the review of all that found: that half of a character is not text, that a text
  inside every bound could take half a minute to read, that changing a scheme refused the
  rows stored under it, that the task worker never saw a change, and the rest, each held
  below under its own heading.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import translation

from postulo.accounts import identifiers as person_identifiers
from postulo.accounts.models import PersonIdentifier
from postulo.core import identifiers, memo, site
from postulo.core.identifiers import PERSON
from postulo.core.models import SiteSettings
from postulo.jobs import identifiers as company_identifiers
from postulo.jobs.models import Company, CompanyIdentifier
from postulo.plugins.identifiers import custom, patterns
from postulo.plugins.identifiers.schemes import SCHEMES

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

#: Two schemes of an instance's own: one a person holds, with everything a scheme can say,
#: and one a company holds, with the least.
STAFF = {
    "key": "staff-number",
    "label": {"en-GB": "Staff number", "pt-PT": "Número de funcionário"},
    "subjects": ["person"],
    "pattern": r"[A-Z]{2}-\d{6}",
    "example": "AB-123456",
    "upper": True,
    "link": "https://intranet.example.org/staff/{value}",
}
SIREN = {"key": "siren", "label": "SIREN", "subjects": ["company"], "pattern": r"\d{9}|\d{14}"}


def written(*schemes: dict) -> str:
    """The schemes as an administrator would type them: one name to a line."""
    return json.dumps(list(schemes), indent=2, ensure_ascii=False)


def define(*schemes: dict) -> None:
    """Put the schemes on the policy row, as saving the page does."""
    keep(written(*schemes))


def keep(text: str) -> None:
    row = SiteSettings.get()
    row.identifier_schemes = text
    row.save()


def one(**changes) -> dict:
    """A small valid scheme with something changed, or taken out where the change is None."""
    scheme = {"key": "badge", "label": "Badge", "subjects": ["person"], "pattern": r"\d{4}"}
    scheme.update(changes)
    return {name: value for name, value in scheme.items() if value is not None}


def problems(*schemes: dict) -> list[str]:
    return [str(problem) for problem in custom.read(written(*schemes), taken=SCHEMES).problems]


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org",
        password=PASSWORD,
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


# ------------------------------------------------------------------ the pattern language
#
# Every construct the language has, in a pattern of its own and in company.

ACCEPTED = {
    "a character": r"X",
    "characters": r"AB-12",
    "an escaped mark": r"a\.b\$c\(d\)",
    "a class": r"[A-Z]",
    "a class of ranges": r"[0-9a-fA-F]{8}",
    "a class with a hyphen": r"[a-z-]{3}",
    "a class beyond ASCII": r"[A-Za-zÀ-ÿ]{1,30}",
    "a negated class": r"[^ /]{1,50}",
    "a digit": r"\d{9}",
    "a word character": r"\w{1,40}",
    "an exact count": r"[A-Z]{2}\d{6}",
    "a range of counts": r"\d{2,5}",
    "an optional character": r"\d{4}-?\d{4}",
    "an optional class": r"\d{8}[A-Z]?",
    "the largest count": r"[a-z]{0,100}",
    "a fixed separator": r"\d{3}( - )\d{3}",
    "an optional separator": r"[A-Z]{2}( - )?\d{3}",
    "alternatives": r"\d{9}|\d{14}",
    "twelve alternatives": "|".join(rf"{letter}\d{{1,8}}" for letter in "ABCDEFGHIJKL"),
    "the two ends": r"^[A-Z]{1,3}-\d{4}$",
    "counts with separators between": r"[a-z]{1,20}-[a-z]{1,20}-[a-z]{1,20}-[a-z]{1,20}",
    "counts of different classes": r"[a-z]{0,30}\d{0,30}[A-Z]{0,30}",
    "many optional pieces": r"a?b?c?d?e?f?g?h?i?j?k?l?m?n?o?p?",
}


@pytest.mark.parametrize("source", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_what_the_pattern_language_has(source):
    assert isinstance(patterns.compiled(source), re.Pattern)


@pytest.mark.parametrize(
    ("source", "value", "accepted"),
    [
        (r"[A-Z]{2}-\d{6}", "AB-123456", True),
        (r"[A-Z]{2}-\d{6}", "AB-12345", False),
        (r"[A-Z]{2}-\d{6}", "AB-1234567", False),
        (r"[A-Z]{2}-\d{6}", "ab-123456", False),
        # The whole value, with or without the marks for its two ends.
        (r"^\d{4}$", "1234", True),
        (r"\d{4}", "x1234", False),
        (r"\d{4}", "1234\n", False),
        # A digit is 0 to 9 and nothing else: not another script's, which `int()` reads (#638).
        (r"\d{4}", "１２３４", False),
        (r"\d{4}", "١٢٣٤", False),
        (r"\w{3}", "aé1", False),
        (r"[A-Za-zÀ-ÿ]{3}", "aé1"[:2] + "Z", True),
        (r"\d{9}|\d{14}", "1" * 9, True),
        (r"\d{9}|\d{14}", "1" * 14, True),
        (r"\d{9}|\d{14}", "1" * 10, False),
        (r"[A-Z]{2}( - )?\d{3}", "AB - 123", True),
        (r"[A-Z]{2}( - )?\d{3}", "AB123", True),
        (r"[A-Z]{2}( - )?\d{3}", "AB -123", False),
        (r"\d{8}[A-Z]?", "12345678", True),
        (r"\d{8}[A-Z]?", "12345678Z", True),
        (r"a\.b", "a.b", True),
        (r"a\.b", "axb", False),
        # Whatever a class says, a control character is nobody's.
        (r"[^ ]{1,9}", "a\x00b", False),
        (r"[^ ]{1,9}", "a\tb", False),
        (r"[^ ]{1,9}", "a\u2028b", False),
        (r"[^ ]{1,9}", "a/b?c", True),
    ],
)
def test_a_pattern_means_what_it_says(source, value, accepted):
    compiled = patterns.compiled(source)
    assert bool(compiled.match(value)) is accepted
    assert bool(compiled.fullmatch(value)) is accepted, "match and fullmatch agree: it is anchored"


#: What the language has not got: the source, the character it is refused at, and a word
#: of the reason. The reason is a sentence for whoever typed the pattern.
REFUSED = {
    "a star": (r"\d*", 3, "without a limit"),
    "a plus": (r"[a-z]+", 6, "without a limit"),
    "a count with no largest number": (r"\d{2,}", 3, "largest number"),
    "a count too large": (r"\d{101}", 3, "more than 100 times"),
    "a count of none": (r"\d{0}", 3, "A count is written"),
    "a count the wrong way round": (r"\d{5,2}", 3, "A count is written"),
    "a count that is not one": (r"\d{a}", 3, "A count is written"),
    "a count of a count": (r"\d{2}{3}", 6, "Only one count"),
    "a lazy count": (r"\d{2,5}?", 8, "Only one count"),
    "a possessive count": (r"\d?+", 4, "Only one count"),
    "a count of nothing": (r"{3}", 1, "nothing before this"),
    "a group that repeats": (r"(ab){2}", 5, "A group cannot repeat"),
    "a group repeating without a limit": (r"(ab)+", 5, "A group cannot repeat"),
    "a repetition inside a repetition": (r"(a+)+", 3, "fixed separator"),
    "a choice inside a group": (r"(a|b)", 3, "fixed separator"),
    "a class inside a group": (r"([a-z])", 2, "fixed separator"),
    "a group inside a group": (r"((a))", 2, "fixed separator"),
    "an empty group": (r"()", 1, "fixed separator"),
    "a look-ahead": (r"(?=a)b", 1, "looks ahead"),
    "a look-behind": (r"a(?<!b)", 2, "looks ahead"),
    "a named group": (r"(?P<n>a)", 1, "no group has a name"),
    "a reference back": (r"(a)\1", 4, "refer back"),
    "a full stop": (r"a.b", 2, "any character"),
    "a class with a name it has not got": (r"\s", 1, "not part of this language"),
    "a word boundary": (r"\bword", 1, "not part of this language"),
    "an anchor in the middle": (r"a$b", 2, "only at the two ends"),
    "a bracket never closed": (r"[a-z", 1, "never closed"),
    "a group never closed": (r"(ab", 1, "never closed"),
    "a count never closed": (r"a{2", 2, "never closed"),
    "a bracket never opened": (r"ab]", 3, "never opened"),
    "a range the wrong way round": (r"[z-a]", 2, "wrong way round"),
    "an empty class": (r"[]", 1, "no character at all"),
    "an empty alternative": (r"a||b", 3, "is empty"),
    "an alternative at the end": (r"a|", 2, "is empty"),
    "thirteen alternatives": ("|".join("abcdefghijklm"), 24, "12 alternatives"),
    "nothing": ("", 1, "cannot be empty"),
    "a trailing backslash": ("ab\\", 3, "end in a backslash"),
    "a line break": ("a\nb", 2, "control character"),
    "more than an identifier holds": (r"\d{60}[A-Z]{60}", 1, "more than 100 characters"),
    "more than a pattern holds": ("a" * 301, 301, "300 characters long"),
    # One reading: what follows a piece with a choice cannot begin with what it takes.
    "a count beside its own class": (r"[a-z]{1,5}[a-z]", 1, "read in two ways"),
    "two counts of one class": (r"\d{0,50}\d{0,50}", 1, "read in two ways"),
    "a count beside a class that overlaps": (r"[A-Z]{1,3}[A-Z0-9]{4}", 1, "read in two ways"),
    "an optional character twice": (r"a?a", 1, "read in two ways"),
    "a count beside a negated class": (r"\d{1,5}[^ ]", 1, "read in two ways"),
    "an overlap past an optional piece": (r"\d{1,5}-?\d", 1, "read in two ways"),
    "an optional separator beside its first character": (r"(ab)?a", 1, "read in two ways"),
    "the overlap in a later alternative": (r"\d{4}|x[a-z]{1,5}[a-z]", 8, "read in two ways"),
}


@pytest.mark.parametrize(("source", "at", "said"), REFUSED.values(), ids=REFUSED.keys())
def test_what_the_pattern_language_has_not_got_is_refused_with_where(source, at, said):
    with pytest.raises(patterns.Refused) as refused:
        patterns.compiled(source)
    assert said in str(refused.value.reason)
    assert refused.value.at == at


# ----------------------------------------------- a pattern reads a value once: timed
#
# A pattern an administrator wrote is run on whatever anybody types, and nothing times it
# out. What holds it is the language: every piece that has a choice is compiled possessive,
# so nothing that was read is read again. This is the proof by measurement, in the manner of
# `first_slow_path` in `tests/test_link_services.py`: every accepted construct, over values
# built to be awkward -- a long run of what the pattern repeats, then one character that is
# nobody's -- at lengths up to the longest value there can be and far past it.

#: How long one pattern may take over one value, in seconds. A pattern that reads its input
#: once takes microseconds; this is thousands of times that, so a busy machine does not
#: fail it and a pattern that goes back over what it read still does.
PATIENCE = 0.1

#: The lengths tried, shortest first. A pattern whose time grows steeply is caught on the
#: way up, at a length that costs a second, and not at the top, which would never return.
LENGTHS = (*range(8, 33, 2), 64, identifiers.MAX_VALUE_LENGTH, 1_000, 20_000)


def awkward_values(length: int):
    """Values of ``length`` characters that almost match something: a run, and a spoiler."""
    for unit in ("a", "A", "1", "-", " ", "a1", "a-", "1-", "A1-", " - ", "aA1_", "é", "/"):
        run = (unit * length)[: length - 1]
        for last in ("!", "a", "1", "\n", ""):
            yield run + last


def seconds(pattern: re.Pattern, value: str) -> float:
    """How long the pattern takes over the value: the fastest of three where the first was
    slow, so that a machine busy with something else is not taken for a slow pattern."""
    fastest = float("inf")
    for _attempt in range(3):
        began = time.perf_counter()
        pattern.match(value)
        fastest = min(fastest, time.perf_counter() - began)
        if fastest <= PATIENCE:
            break
    return fastest


def first_slow_value(pattern: re.Pattern) -> str:
    """The first awkward value the pattern is slow over, described; empty where none is."""
    for length in LENGTHS:
        for value in awkward_values(length):
            took = seconds(pattern, value)
            if took > PATIENCE:
                return f"{took:.2f}s over {length} characters: {value[:40]!r}"
    return ""


def test_the_timing_catches_a_pattern_that_goes_back_over_what_it_read():
    """The guard guarded. Both shapes are refused by the language; written as the regular
    expressions they look like, each is slow, and the measurement says so."""
    assert first_slow_value(re.compile(r"(a+)+!"))
    overlapping = r"[a-z]{0,30}[a-z]{0,30}[a-z]{0,30}[a-z]{0,30}[a-z]{0,30}[a-z]{0,30}!!"
    assert first_slow_value(re.compile(overlapping))
    for source in (r"(a+)+", overlapping.rstrip("!")):
        with pytest.raises(patterns.Refused):
            patterns.compiled(source)


@pytest.mark.parametrize("source", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_every_accepted_construct_reads_a_value_once(source):
    assert not first_slow_value(patterns.compiled(source))


def test_every_piece_with_a_choice_is_compiled_so_that_it_gives_nothing_back():
    """What makes the time linear whatever the analysis decided: each count that is a range
    and each optional piece is possessive, and nothing in a compiled pattern is not."""
    for source in ACCEPTED.values():
        rendered = patterns.compiled(source).pattern
        # Every `{m,n}` and every `?` that is a count carries the `+` that makes it so,
        # and a `+` is nowhere else: nothing repeats without a limit.
        assert not re.search(r"\{\d+,\d+\}(?!\+)", rendered), source
        assert not re.search(r"(?<!\()\?(?![+:])", rendered), source
        assert not re.search(r"(?<![}?])\+", rendered), source
        assert "*" not in rendered, source


def near_values(source: str):
    """Values a pattern accepts and values one slip away from one: every piece there as
    often as it may be, then one piece once too few or once too many, a character dropped,
    a character put in."""
    import random

    # Seeded, so the same values every run: this draws test values, and guards nothing.
    chance = random.Random(311)  # noqa: S311
    for pieces in patterns.read(source):
        for _attempt in range(120):
            counts = [
                chance.choice((p.least, p.most, chance.randint(p.least, p.most))) for p in pieces
            ]
            if chance.random() < 0.4:
                slipped = chance.randrange(len(pieces))
                counts[slipped] = chance.choice(
                    (max(0, pieces[slipped].least - 1), pieces[slipped].most + 1)
                )
            parts = []
            for piece, count in zip(pieces, counts, strict=True):
                if piece.text:
                    parts.append(piece.text * count)
                else:
                    low, high = chance.choice(piece.chars)
                    parts.append("".join(chr(chance.choice((low, high))) for _ in range(count)))
            value = "".join(parts)
            yield value
            if value:
                at = chance.randrange(len(value))
                yield value[:at] + value[at + 1 :]
                yield value[:at] + chance.choice("aA1- ") + value[at:]


@pytest.mark.parametrize("source", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_taking_all_a_piece_may_is_what_the_pattern_means(source):
    """The other half of *reads a value once*. A possessive count gives nothing back, which
    is only right if giving something back could never have helped; that is what refusing a
    pattern with two readings is for. So the compiled pattern is set beside the same one
    with its counts left ordinary, over values that match and values that nearly do, and
    the two never disagree."""
    possessive = patterns.compiled(source)
    ordinary = re.compile(re.sub(r"([}?])\+", r"\1", possessive.pattern))
    accepted = 0
    for value in near_values(source):
        took = bool(possessive.match(value))
        assert took is bool(ordinary.match(value)), value
        accepted += took
    assert accepted, "something tried was accepted, or the comparison says nothing"


def test_a_pattern_with_two_readings_would_have_been_read_wrongly():
    """And why the refusal is there. Each of these is refused; compiled possessive
    regardless, it refuses a value the pattern as written accepts."""
    for written, possessive, value in (
        (r"[a-z]{1,5}[a-z]", r"\A[a-z]{1,5}+[a-z]\Z", "ab"),
        (r"a?a", r"\Aa?+a\Z", "a"),
        (r"\d{1,5}-?\d", r"\A\d{1,5}+-?+\d\Z", "12"),
    ):
        with pytest.raises(patterns.Refused):
            patterns.compiled(written)
        assert re.compile(r"\A" + written + r"\Z").match(value)
        assert not re.compile(possessive).match(value)


def test_a_value_longer_than_an_identifier_never_reaches_the_pattern():
    """The cap: the column's own length. The pattern is asked about nothing longer."""
    define(one(pattern="[a-z]{0,100}", key="word"))
    scheme = identifiers.find("word")
    assert scheme.max_length == identifiers.MAX_VALUE_LENGTH == 100
    assert PersonIdentifier._meta.get_field("value").max_length == identifiers.MAX_VALUE_LENGTH
    assert CompanyIdentifier._meta.get_field("value").max_length == identifiers.MAX_VALUE_LENGTH

    class Counting:
        asked = 0

        def match(self, value):
            type(self).asked += 1
            return scheme.pattern.match(value)

    counted = identifiers.Scheme("word", "Word", Counting(), max_length=scheme.max_length)
    identifiers.refuse_malformed(counted, "a" * 100)
    assert Counting.asked == 1
    with pytest.raises(ValidationError):
        identifiers.refuse_malformed(counted, "a" * 101)
    with pytest.raises(ValidationError):
        identifiers.refuse_malformed(counted, "a" * 5_000_000)
    assert Counting.asked == 1, "neither long value was handed to the pattern"


# ------------------------------------------------------------------- what JSON can say


def test_a_scheme_says_all_ten_things_and_becomes_one():
    full = {**STAFF, "person_link": "https://people.example.org/{value}", "lower": False}
    full["checksum"] = "iso7064-mod97-10"
    full["pattern"] = r"[A-Z0-9]{18}\d{2}"
    full["example"] = "HWUPKR0MPOU8FGXBT394"
    reading = custom.read(written(full), taken=SCHEMES)

    assert not reading.problems
    (scheme,) = reading.schemes
    assert set(full) == set(custom.NAMES), "the test says everything a scheme can say"
    assert scheme.key == "staff-number"
    assert str(scheme.label) == "Staff number"
    assert scheme.subjects == frozenset({PERSON})
    assert scheme.example == "HWUPKR0MPOU8FGXBT394"
    assert (scheme.upper, scheme.lower) == (True, False)
    assert scheme.link == "https://intranet.example.org/staff/{value}"
    assert scheme.person_link == "https://people.example.org/{value}"
    assert scheme.provider == identifiers.DEFINED_HERE
    assert scheme.quoted and scheme.max_length == 100
    assert scheme.checksum("HWUPKR0MPOU8FGXBT394")
    assert not scheme.checksum("HWUPKR0MPOU8FGXBT395")


def test_nothing_typed_defines_nothing_and_has_nothing_wrong_with_it():
    for text in ("", "   \n\n  ", "[]", " [ ] "):
        reading = custom.read(text)
        assert reading.schemes == () and reading.problems == ()


def test_a_name_a_scheme_cannot_say_is_refused_by_name_and_not_passed_over():
    """A misspelt `patern` that was ignored would be a scheme accepting anything."""
    said = problems(one(patern=r"\d", url_paths=["/"], tidy="x"))

    assert len(said) == 3
    assert "Line 9, scheme “badge”: “patern” is not something a scheme can say" in said[0]
    assert "key, label, subjects, pattern, link, person_link, example" in said[0]
    assert "“url_paths”" in said[1] and "“tidy”" in said[2]
    assert not custom.read(written(one(patern=r"\d")), taken=SCHEMES).schemes


@pytest.mark.parametrize("missing", custom.REQUIRED)
def test_a_scheme_has_to_say_four_things(missing):
    (said,) = problems(one(**{missing: None}))
    assert f"“{missing}” is missing." in said
    assert said.startswith("Line 2, ")


@pytest.mark.parametrize("key", SCHEMES)
def test_a_key_of_postulos_own_is_refused(key):
    """Built-ins are added to, never changed -- `other` among them."""
    assert "other" in SCHEMES, "one of Postulo's own, and so run once with the rest"
    (said,) = problems(one(key=key))
    assert f"Line 3, scheme “{key}”: this key is one of Postulo's own schemes" in said


@pytest.mark.parametrize(
    "key", ["Staff", "staff number", "staff_number", "-staff", "", "a" * 21, 7, None, ["a"], "é"]
)
def test_a_key_is_lower_case_letters_digits_and_hyphens(key):
    scheme = one()
    scheme["key"] = key
    (said,) = problems(scheme)
    assert "Line 3, scheme number 1: a key is lower-case letters, digits and hyphens" in said
    assert "20 characters at most" in said


def test_a_key_used_twice_is_refused_where_it_is_used_again():
    (said,) = problems(one(), SIREN, one(label="Another"))
    assert said == "Line 19, scheme “badge”: this key is used by a scheme further up."


def test_a_name_written_twice_in_one_scheme_is_refused():
    """The standard parser keeps the last and says nothing; the first would be a pattern
    somebody believes is in force."""
    text = "\n".join(
        [
            "[",
            ' {"key": "badge", "label": "B", "subjects": ["person"],',
            '  "pattern": "\\\\d",',
            '  "pattern": "[a-z]{1,99}"}',
            "]",
        ]
    )
    (problem,) = custom.read(text).problems
    assert str(problem) == "Line 4, scheme “badge”: “pattern” is written twice."


@pytest.mark.parametrize(
    ("label", "said"),
    [
        ("", "a label is a name in double quotes"),
        ("   ", "a label is a name in double quotes"),
        (7, "a label is a name in double quotes"),
        ({}, "a label is a name in double quotes"),
        ("x" * 61, "60 characters at most"),
        ("two\nlines", "a label is a name in double quotes"),
        ({"english": "Badge"}, "“english” is not a language code"),
        ({"pt-br": "Crachá"}, "the language code “pt-br” is written “pt-BR”"),
        ({"pt_BR": "Crachá"}, "the language code “pt_BR” is written “pt-BR”"),
        ({"en-GB": ""}, "the name in “en-GB” is text in double quotes"),
        ({"en-GB": 3}, "the name in “en-GB” is text in double quotes"),
    ],
)
def test_a_label_is_a_name_or_a_name_for_each_language(label, said):
    found = problems(one(label=label))
    assert len(found) == 1 and said in found[0]
    assert found[0].startswith("Line 4, scheme “badge”: ")


@pytest.mark.parametrize(
    ("reading_in", "shown"),
    [
        ("pt-PT", "Número"),
        # The closest: Brazilian Portuguese takes the European before anything else.
        ("pt-BR", "Número"),
        ("fr-FR", "Numéro"),
        # Nothing close: British English, which is what Postulo itself is written in.
        ("de", "Staff number"),
        ("en-GB", "Staff number"),
    ],
)
def test_a_label_reads_in_the_readers_language(reading_in, shown):
    labels = {"fr-FR": "Numéro", "en-GB": "Staff number", "pt-PT": "Número"}
    define(one(label=labels))
    with translation.override(reading_in):
        assert str(identifiers.find("badge").label) == shown
        assert identifiers.label_for("badge") == shown


def test_a_label_with_nothing_close_and_no_english_is_the_first_written():
    define(one(label={"fr-FR": "Numéro", "pt-PT": "Número"}))
    with translation.override("de"):
        assert identifiers.label_for("badge") == "Numéro"
    with translation.override("en-GB"):
        assert identifiers.label_for("badge") == "Numéro"


@pytest.mark.parametrize(
    "subjects", [[], "person", ["robot"], ["person", "robot"], [1], {"person": 1}, True]
)
def test_subjects_are_a_person_a_company_or_both_and_at_least_one(subjects):
    (said,) = problems(one(subjects=subjects))
    assert "Line 5, scheme “badge”: “subjects” lists what the scheme identifies" in said


def test_a_scheme_can_identify_both():
    define(one(subjects=["person", "company"]))
    assert "badge" in person_identifiers.schemes()
    assert "badge" in company_identifiers.schemes()


def test_a_pattern_that_is_refused_says_why_and_where_on_its_own_line():
    (said,) = problems(one(pattern=r"\d+"))
    assert said.startswith(
        "Line 8, scheme “badge”: the pattern cannot be used, at character 3. “*” and “+” repeat"
    )
    (said,) = problems(one(pattern=7))
    assert said == "Line 8, scheme “badge”: “pattern” is text in double quotes."


@pytest.mark.parametrize("name", ["upper", "lower"])
@pytest.mark.parametrize("value", ["true", 1, 0, "yes", []])
def test_a_fold_is_true_or_false(name, value):
    (said,) = problems(one(**{name: value}))
    assert f"“{name}” is true or false, without quotes." in said


def test_a_value_cannot_be_folded_both_ways():
    (said,) = problems(one(upper=True, lower=True))
    assert "“upper” and “lower” cannot both be true." in said


def test_a_checksum_is_named_and_cannot_be_supplied():
    assert set(custom.CHECKSUMS) == {"iso7064-mod11-2", "iso7064-mod97-10"}
    for given in ("luhn", "", 11, "lambda value: True", "postulo.plugins.x.y", ["iso7064-mod11-2"]):
        (said,) = problems(one(checksum=given))
        assert "“checksum” names one of the two Postulo knows" in said


def test_the_two_checksums_are_the_ones_an_orcid_and_an_lei_end_in():
    eleven, ninety_seven = custom.CHECKSUMS["iso7064-mod11-2"], custom.CHECKSUMS["iso7064-mod97-10"]

    assert eleven("0000-0002-1825-0097") and eleven("0000 0001 2281 955X")
    assert eleven("000000012281955x"), "however it is separated, in either case"
    assert not eleven("0000-0002-1825-0098")
    assert ninety_seven("HWUPKR0MPOU8FGXBT394") and ninety_seven("hwup kr0m pou8 fgxb t394")
    assert not ninety_seven("HWUPKR0MPOU8FGXBT395")
    # A pattern of somebody's own lets anything through, and neither may raise over it.
    for nothing_to_count in ("", "-", "X", "é", "X0", "١٢٣", "a" * 100, "１２３４"):
        assert eleven(nothing_to_count) is False
    for nothing_to_count in ("", "-", "é", "!!", "١٢٣"):
        assert ninety_seven(nothing_to_count) is False


def test_a_named_checksum_refuses_a_typo_in_the_schemes_own_words():
    define(one(pattern=r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", checksum="iso7064-mod11-2", key="card"))
    assert person_identifiers.clean("card", "0000-0002-1825-0097") == "0000-0002-1825-0097"
    with pytest.raises(ValidationError) as refused:
        person_identifiers.clean("card", "0000-0002-1825-0096")
    assert refused.value.code == "checksum"
    assert "one of them is a typo" in refused.value.messages[0]


def test_an_example_has_to_be_a_value_the_scheme_itself_accepts():
    """The one check of a pattern an administrator gets before somebody else meets it."""
    (said,) = problems(one(example="12345"))
    assert said == "Line 9, scheme “badge”: the example is not a value its own pattern accepts."
    (said,) = problems(
        one(pattern=r"\d{15}[\dX]", checksum="iso7064-mod11-2", example="0000000218250098")
    )
    assert "the example does not pass the checksum the scheme names." in said
    assert not problems(one(pattern="[A-Z]{2}", upper=True, example="ab")), "folded first"
    for example in ("", 5, "x" * 101, "two\nlines"):
        (said,) = problems(one(example=example))
        assert "“example” is text in double quotes, on one line" in said


@pytest.mark.parametrize(
    ("text", "line", "column", "said"),
    [
        ('[\n  {"key": "a"\n   "label": "A"}\n]', 3, 4, "a comma is missing before this"),
        ('[\n  {"key": "a", "label": }\n]', 2, 25, "a value should begin here"),
        ('[\n  {"key" "a"}\n]', 2, 10, "a colon is missing"),
        ('[\n  {key: "a"}\n]', 2, 4, "a name in double quotes should begin here"),
        ('[\n  {"key": "a\n  }\n]', 2, 13, "a closing quote may be missing"),
        ('[\n  {"key": "a', 2, 11, "is never closed"),
        ('[\n  {"pattern": "\\d{4}"}\n]', 2, 16, "A backslash in a pattern is written twice"),
        ('[{"key": "a"}]\n\n]', 3, 1, "the list has ended, and something follows it"),
        ("[\n\n\n   nonsense", 4, 4, "a value should begin here"),
    ],
)
def test_text_that_is_not_json_is_refused_with_the_line_and_the_column(text, line, column, said):
    reading = custom.read(text)
    (problem,) = reading.problems

    assert reading.schemes == ()
    assert (problem.line, problem.column) == (line, column)
    assert str(problem).startswith(f"Line {line}, column {column}: ")
    assert said in str(problem)
    assert "Expecting" not in str(problem), "the parser's own words name tokens, not mistakes"


def test_a_comma_after_the_last_item_is_refused_in_words():
    """Said in two ways, by two versions of the parser; either way it is a sentence."""
    for text in ('[\n  {"key": "a",}\n]', '[\n  {"key": "a"},\n]'):
        (problem,) = custom.read(text).problems
        assert problem.line in (2, 3) and problem.column
        assert "comma" in str(problem)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ('{"key": "a"}', "Line 1: the schemes are written as a list, between [ and ]."),
        ('\n\n"staff"', "Line 3: the schemes are written as a list, between [ and ]."),
        ("7", "Line 1: the schemes are written as a list, between [ and ]."),
        ('[\n  "staff",\n  7\n]', "Line 2, scheme number 1: a scheme is written between { and }."),
    ],
)
def test_the_schemes_are_a_list_of_objects(text, said):
    assert str(custom.read(text).problems[0]) == said


def test_the_text_is_bounded_and_so_is_the_number_of_schemes():
    """50 000 characters and a hundred schemes; past either, nothing is read at all."""
    assert (custom.MAX_CHARACTERS, custom.MAX_SCHEMES) == (50_000, 100)

    padded = written(one()) + " " * custom.MAX_CHARACTERS
    reading = custom.read(padded)
    assert reading.schemes == ()
    assert str(reading.problems[0]) == "Line 1: the schemes can be 50000 characters long at most."

    many = [one(key=f"k{number}") for number in range(101)]
    reading = custom.read(json.dumps(many))
    assert reading.schemes == ()
    assert "100 schemes can be defined here at most." in str(reading.problems[0])
    assert len(custom.read(json.dumps(many[:100])).schemes) == 100


@pytest.mark.parametrize(
    "text",
    ["[" * 30_000, "[" + "1" * 6_000 + "]", '[{"a": ' * 5_000],
    ids=["brackets never closed", "a number of six thousand digits", "objects never closed"],
)
def test_text_built_to_exhaust_the_parser_is_only_refused(text):
    reading = custom.read(text)
    assert reading.schemes == () and len(reading.problems) == 1


def test_every_problem_is_reported_in_the_order_of_the_text():
    broken = one(label=7, subjects=[], pattern="a+", upper="yes")
    lines = [problem.line for problem in custom.read(written(SIREN, broken)).problems]
    assert lines == sorted(lines) and len(lines) == 4
    # The scheme with nothing wrong is still read: this is what a stored text is used by.
    assert [s.key for s in custom.read(written(SIREN, broken), taken=SCHEMES).schemes] == ["siren"]


# --------------------------------------------------------------------------- the links


@pytest.mark.parametrize(
    "template",
    [
        "https://intranet.example.org/staff/{value}",
        "http://example.org/?id={value}&view=full",
        "https://example.org/#/record/{value}",
        "https://example.org/{value}/profile/{value}",
        "HTTPS://Example.org/x/{value}",
    ],
)
def test_a_link_is_a_web_address_with_the_value_after_the_host(template):
    assert identifiers.link_template(template) == template
    assert not problems(one(link=template, person_link=template))


@pytest.mark.parametrize(
    ("template", "said"),
    [
        ("javascript:alert({value})", "begins with http:// or https://"),
        ("JaVaScRiPt:alert('{value}')", "begins with http:// or https://"),
        ("data:text/html,{value}", "begins with http:// or https://"),
        ("ftp://example.org/{value}", "begins with http:// or https://"),
        ("//example.org/{value}", "begins with http:// or https://"),
        ("/staff/{value}", "begins with http:// or https://"),
        ("{value}", "begins with http:// or https://"),
        ("https:///{value}", "begins with http:// or https://"),
        ("https://example.org/ {value}", "begins with http:// or https://"),
        ("https://user:secret@example.org/{value}", "begins with http:// or https://"),
        # The value must never choose the host.
        ("https://{value}/staff", "{value} goes after the host"),
        ("https://{value}.example.org/", "{value} goes after the host"),
        ("https://example.org{value}", "{value} goes after the host"),
        ("https://example.org.{value}/x", "{value} goes after the host"),
        ("https://example.org/staff", "A link holds {value}"),
        ("https://example.org/{value}/{other}", "cannot hold a curly bracket other than {value}"),
        ("https://example.org/{value.__class__}/{value}", "cannot hold a curly bracket"),
        ("https://example.org/{value}/{0}", "cannot hold a curly bracket"),
        ("https://example.org\\@evil.example/{value}", "a backslash"),
        ("https://example.org/{value}\nSet-Cookie: x", "a line break"),
        ("https://example.org/" + "a" * 500 + "{value}", "500 characters long at most"),
    ],
)
def test_a_link_that_could_lead_anywhere_else_is_refused(template, said):
    with pytest.raises(ValueError, match=re.escape(said)):
        identifiers.link_template(template)
    for name in ("link", "person_link"):
        (problem,) = problems(one(**{name: template}))
        assert f"Line 9, scheme “badge”: “{name}” cannot be used. " in problem
        assert said in problem


def test_a_link_that_is_not_text_is_refused():
    (said,) = problems(one(link=7))
    assert "“link” cannot be used. A link is text in double quotes." in said


@pytest.mark.parametrize(
    "value",
    [
        "a/b",
        "../../etc",
        "a?b=c",
        "a#b",
        "a@evil.example",
        "a\\b",
        "a\x00b",
        "//evil.example",
        "é ü",
    ],
)
def test_a_value_is_percent_encoded_into_its_link_and_cannot_leave_where_it_was_put(value):
    """A pattern of somebody's own may let anything through. The link stays the link."""
    from urllib.parse import parse_qs, unquote, urlsplit

    define(one(pattern="[^é]{1,40}|é ü", link="https://example.org/staff/{value}/card?id={value}"))
    url = identifiers.find("badge").url_for(value, PERSON)
    parts = urlsplit(url)

    assert (parts.scheme, parts.netloc) == ("https", "example.org")
    assert parts.username is None and parts.fragment == ""
    path = parts.path.split("/")
    assert len(path) == 4 and path[1] == "staff" and path[3] == "card", "no segment was added"
    assert unquote(path[2]) == value
    assert parse_qs(parts.query, keep_blank_values=True) == {"id": [value]}
    for mark in ("\\", "\x00", " ", "@", "#"):
        assert mark not in url


def test_a_person_and_a_company_can_be_sent_to_different_places():
    define(
        one(
            subjects=["person", "company"],
            link="https://example.org/org/{value}",
            person_link="https://example.org/people/{value}",
        )
    )
    assert person_identifiers.url_for("badge", "1234") == "https://example.org/people/1234"
    assert company_identifiers.url_for("badge", "1234") == "https://example.org/org/1234"


def test_the_schemes_postulo_ships_build_their_links_as_they_did():
    """`quoted` is for a scheme whose pattern is somebody's own, and for no other."""
    assert not any(scheme.quoted or scheme.max_length for scheme in SCHEMES.values())
    assert (
        SCHEMES["opencorporates"].url_for("gb/01234567")
        == "https://opencorporates.com/companies/gb/01234567"
    )


# ------------------------------------------------------------------------- the registry


def test_a_scheme_of_the_instances_own_comes_after_postulos():
    shipped = list(identifiers.registry())
    define(STAFF, SIREN)
    assert list(identifiers.registry()) == [*shipped, "staff-number", "siren"]
    assert list(identifiers.shipped()) == shipped, "and Postulo's own are as they were"
    assert list(person_identifiers.schemes())[-1] == "staff-number"
    assert list(company_identifiers.schemes())[-1] == "siren"
    assert "siren" not in person_identifiers.schemes()
    assert "staff-number" not in company_identifiers.schemes()


def test_postulos_own_schemes_are_never_changed_whatever_the_row_holds():
    """Written past the page, as a restored backup or a hand on the database would."""
    hostile = one(key="orcid", label="Not ORCID", pattern="[a-z]{1,9}", subjects=["company"])
    keep(written(hostile, one(key="other", label="Mine"), SIREN))

    registry = identifiers.registry()
    assert registry["orcid"] is SCHEMES["orcid"] and registry["other"] is SCHEMES["other"]
    assert registry["orcid"].label == "ORCID"
    assert person_identifiers.clean("orcid", "0000-0002-1825-0097")
    assert company_identifiers.scheme_for("orcid") is None
    assert "siren" in registry, "and the scheme beside them that is in order is still read"


def test_a_stored_text_is_held_to_the_rules_the_page_applies(caplog):
    """An archive is not a way past the pattern rules: the registry is built from what the
    reading accepts, wherever the text came from."""
    slow = one(key="slow", pattern="(a+)+")
    unbounded = one(key="unbounded", pattern="[a-z]*")
    overlapping = one(key="overlapping", pattern="[a-z]{0,50}[a-z]{0,50}")
    script = one(key="script", link="javascript:alert({value})")
    with caplog.at_level("WARNING", logger="postulo.core.identifiers"):
        keep(written(slow, unbounded, overlapping, script, SIREN))
        found = identifiers.defined_here()

    assert [scheme.key for scheme in found] == ["siren"]
    for key in ("slow", "unbounded", "overlapping", "script"):
        assert identifiers.find(key) is None
        assert f"scheme “{key}”" in caplog.text


@pytest.mark.parametrize("text", ["not json at all", '{"key": "a"}', "[" * 5000, "[1, 2, 3]"])
def test_a_stored_text_that_cannot_be_read_defines_nothing_and_breaks_nothing(text):
    shipped = list(identifiers.shipped())
    keep(text)
    assert list(identifiers.registry()) == shipped


def test_the_definitions_cost_no_query_of_their_own(django_assert_num_queries):
    """The registry is asked on most pages, several times a row. The policy row is read
    once a request whatever happens; the definitions are read from that."""
    define(STAFF, SIREN)
    site.forget_current()  # a request begins

    with django_assert_num_queries(1):  # the policy row, which every request reads
        site.current()
    with django_assert_num_queries(0):
        for _asked in range(50):
            assert "staff-number" in identifiers.registry()
            assert identifiers.label_for("siren") == "SIREN"
            assert person_identifiers.choices()


def test_the_definitions_are_read_once_for_each_text(monkeypatch):
    """Not once a request: the reading compiles patterns, and a text that has not changed
    comes to what it came to."""
    define(STAFF)
    readings = []
    read = identifiers.read_definitions
    monkeypatch.setattr(
        identifiers, "read_definitions", lambda text: readings.append(text) or read(text)
    )
    monkeypatch.setattr(identifiers, "_read", None)

    for _request in range(5):
        site.forget_current()
        for _asked in range(3):
            assert identifiers.find("staff-number") is not None
    assert len(readings) == 1

    define(STAFF, SIREN)
    for _request in range(5):
        site.forget_current()
        assert identifiers.find("siren") is not None
    assert len(readings) == 2


def test_a_change_made_by_another_worker_is_in_force_on_the_next_request():
    """Nothing is cleared and nothing is told: the text is the key, and every request
    reads the row. A request already in flight keeps the row it read."""
    define(STAFF)
    site.forget_current()
    assert identifiers.find("staff-number") is not None

    # Another process saves the page: this one's memo of the row is not dropped.
    SiteSettings.objects.filter(pk=1).update(identifier_schemes=written(SIREN))
    assert identifiers.find("staff-number") is not None, "the request in flight"
    assert identifiers.find("siren") is None

    memo.forget_current()  # which is what the next request begins with
    assert identifiers.find("staff-number") is None
    assert identifiers.find("siren") is not None

    SiteSettings.objects.filter(pk=1).update(identifier_schemes="")
    memo.forget_current()
    assert list(identifiers.registry()) == list(identifiers.shipped())


def test_inside_a_migration_the_registry_is_postulos_own_and_no_row_is_read(
    django_assert_num_queries,
):
    define(STAFF)
    site.forget_current()
    with identifiers.shipped_only(), django_assert_num_queries(0):
        assert identifiers.find("staff-number") is None
        assert list(identifiers.registry()) == list(identifiers.shipped())
    assert identifiers.find("staff-number") is not None, "and afterwards it is whole again"


def test_the_migration_that_folds_identifiers_asks_for_no_row(user, company, monkeypatch):
    """It may run before the column the definitions are kept in exists."""
    import importlib

    from django.apps import apps

    define(SIREN)
    row = CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="siren", value="123456789"
    )
    asked = []
    monkeypatch.setattr(identifiers, "_policy_row", lambda: asked.append(1) or site.current())
    module = importlib.import_module(
        "postulo.jobs.migrations."
        "0014_remove_companyidentifier_one_company_per_identifier_per_owner_and_more"
    )

    module.fold_and_report(apps, None)

    assert not asked
    row.refresh_from_db()
    assert (row.scheme, row.value) == ("siren", "123456789"), "and its row is left as it is"


def test_the_plugin_holds_no_row_and_is_handed_the_text():
    """The storage is Postulo's. The registry plugin reads what it is given."""
    import ast
    from pathlib import Path

    import postulo.plugins.identifiers as plugin

    for path in Path(plugin.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any("models" in name or name.endswith("core.site") for name in imported), path
        assert "django.db" not in imported, path
    reading = plugin.Identifiers().defined(written(STAFF), taken=SCHEMES)
    assert [scheme.key for scheme in reading.schemes] == ["staff-number"]


# --------------------------------------------------------------------- where it shows


def test_a_persons_scheme_is_offered_on_your_details_after_postulos_own(client, user):
    define(STAFF, SIREN)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    select = html[html.index('name="identifiers-0-scheme"') :]
    select = select[: select.index("</select>")]
    offered = re.findall(r'<option value="([^"]*)"', select)

    assert offered[-2:] == ["other", "staff-number"]
    assert "siren" not in offered
    assert ">Staff number</option>" in select
    # And listed with its example where the kinds are explained.
    assert '<dt class="text-ink-500 dark:text-ink-400">Staff number</dt>' in html
    assert "AB-123456" in html


def test_a_companys_scheme_is_offered_on_a_companys_form(client, user, company):
    define(STAFF, SIREN)
    client.force_login(user)
    for url in (reverse("jobs:company_create"), reverse("jobs:company_update", args=[company.pk])):
        html = client.get(url).content.decode()
        select = html[html.index('name="identifiers-0-scheme"') :]
        select = select[: select.index("</select>")]
        assert re.findall(r'<option value="([^"]*)"', select)[-1] == "siren"
        assert "staff-number" not in select


def post_details(client, user, **rows) -> object:
    data = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "headline": "",
        "phone_0": "",
        "phone_1": "",
        "location": "",
        "website": "",
        "linkedin_url": "",
        "source_repo_url": "",
        "identifiers-TOTAL_FORMS": "1",
        "identifiers-INITIAL_FORMS": "0",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-scheme": "",
        "identifiers-0-value": "",
        "identifiers-0-label": "",
    }
    data.update(rows)
    return client.post(reverse("accounts:profile"), data)


def test_a_value_is_folded_checked_and_linked_on_your_details(client, user):
    define(STAFF)
    client.force_login(user)

    response = post_details(
        client,
        user,
        **{"identifiers-0-scheme": "staff-number", "identifiers-0-value": " ab-123456 "},
    )

    assert response.status_code == 302, response.context and response.context["identifiers"].errors
    row = PersonIdentifier.objects.get(profile=user.profile)
    assert (row.scheme, row.value) == ("staff-number", "AB-123456")
    assert row.scheme_label == "Staff number"
    assert row.url == "https://intranet.example.org/staff/AB-123456"


def test_a_value_of_another_shape_is_refused_with_the_schemes_example(client, user):
    define(STAFF)
    client.force_login(user)

    response = post_details(
        client, user, **{"identifiers-0-scheme": "staff-number", "identifiers-0-value": "12"}
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "That does not look like a Staff number identifier (for example AB-123456)." in html
    assert not PersonIdentifier.objects.exists()


def test_a_scheme_with_no_example_refuses_without_an_empty_bracket(user):
    define(one())
    with pytest.raises(ValidationError) as refused:
        person_identifiers.clean("badge", "x")
    assert refused.value.messages == ["That does not look like a Badge identifier."]
    assert refused.value.code == "format"


def test_a_scheme_is_refused_for_what_it_does_not_identify(user, company):
    define(STAFF, SIREN)
    with pytest.raises(ValidationError):
        company_identifiers.clean("staff-number", "AB-123456")
    with pytest.raises(ValidationError):
        PersonIdentifier.objects.create(profile=user.profile, scheme="siren", value="123456789")
    with pytest.raises(ValidationError):
        CompanyIdentifier.objects.create(
            owner=user, company=company, scheme="staff-number", value="AB-123456"
        )


def test_one_of_each_kind_holds_for_a_scheme_of_the_instances_own(user):
    from django.db import IntegrityError, transaction

    define(STAFF)
    PersonIdentifier.objects.create(profile=user.profile, scheme="staff-number", value="AB-123456")
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonIdentifier.objects.create(
            profile=user.profile, scheme="staff-number", value="AB-654321"
        )


# ------------------------------------------------------------------------------ the API


def bearer(user, *scopes: str) -> dict:
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(user, "test", scopes=scopes or ("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_takes_a_scheme_of_the_instances_own_as_it_takes_postulos(client, user):
    define(SIREN | {"link": "https://annuaire.example.org/{value}"})
    headers = bearer(user)

    made = client.post(
        "/api/v1/companies",
        data=json.dumps(
            {"name": "Aperture", "identifiers": [{"scheme": "siren", "value": "552081317"}]}
        ),
        content_type="application/json",
        **headers,
    )
    assert made.status_code in (200, 201), made.content
    (identifier,) = made.json()["identifiers"]
    assert identifier == {
        "scheme": "siren",
        "value": "552081317",
        "label": "",
        "url": "https://annuaire.example.org/552081317",
    }

    refused = client.post(
        "/api/v1/companies",
        data=json.dumps({"name": "Black Mesa", "identifiers": [{"scheme": "siren", "value": "x"}]}),
        content_type="application/json",
        **headers,
    )
    assert refused.status_code == 422
    assert "does not look like a SIREN identifier" in refused.content.decode()


def test_the_apis_description_says_an_instance_may_define_schemes_of_its_own():
    from postulo.api.api import api

    schema = api.get_openapi_schema()
    described = schema["components"]["schemas"]["IdentifierOut"]["properties"]["scheme"]
    assert "wikidata" in described["description"]
    assert "this instance defines for itself" in described["description"]
    taken = schema["components"]["schemas"]["IdentifierIn"]["properties"]["scheme"]
    assert "this instance defines for itself" in taken["description"]


# ------------------------------------------- deleting a scheme loses no identifier


@pytest.fixture
def orphans(user, company):
    """A person's and a company's identifier, under schemes that are then deleted."""
    define(STAFF, SIREN)
    person = PersonIdentifier.objects.create(
        profile=user.profile, scheme="staff-number", value="AB-123456"
    )
    firm = CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="siren", value="552081317"
    )
    keep("")
    return PersonIdentifier.objects.get(pk=person.pk), CompanyIdentifier.objects.get(pk=firm.pk)


def test_a_row_under_a_deleted_scheme_is_shown_as_it_was_stored(orphans, client, user, company):
    person, firm = orphans

    assert (person.scheme, person.value) == ("staff-number", "AB-123456")
    assert person.scheme_label == person.display_label == "staff-number", "its key, for a name"
    assert person.url == "" and firm.url == "", "no link, as Other has none"
    assert firm.scheme_label == "siren"

    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    shown = html[html.index("data-identifiers") :]
    assert "siren" in shown and "552081317" in shown
    html = client.get(reverse("accounts:profile")).content.decode()
    assert '<option value="staff-number" selected>staff-number</option>' in html
    assert 'value="AB-123456"' in html


def test_the_page_a_kept_row_sits_on_can_still_be_saved(orphans, client, user):
    """Or deleting a scheme would cost the identifier after all, the first time anything
    else on the page was changed."""
    person, _firm = orphans
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alexandra",
            "last_name": "Morgan",
            "headline": "",
            "phone_0": "",
            "phone_1": "",
            "location": "",
            "website": "",
            "linkedin_url": "",
            "source_repo_url": "",
            "identifiers-TOTAL_FORMS": "2",
            "identifiers-INITIAL_FORMS": "1",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-id": str(person.pk),
            "identifiers-0-profile": str(user.profile.pk),
            "identifiers-0-scheme": "staff-number",
            "identifiers-0-value": "AB-123456",
            "identifiers-0-label": "",
            "identifiers-1-scheme": "orcid",
            "identifiers-1-value": "0000-0002-1825-0097",
            "identifiers-1-label": "",
        },
    )

    assert response.status_code == 302, response.context["identifiers"].errors
    user.refresh_from_db()
    assert user.first_name == "Alexandra"
    kept = PersonIdentifier.objects.get(pk=person.pk)
    assert (kept.scheme, kept.value) == ("staff-number", "AB-123456"), "nothing is rewritten"
    assert PersonIdentifier.objects.filter(profile=user.profile, scheme="orcid").exists()


def test_a_companys_form_with_a_kept_row_can_still_be_saved(orphans, client, user, company):
    _person, firm = orphans
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {
            "name": "Aperture Science",
            "kind": company.kind,
            "identifiers-TOTAL_FORMS": "1",
            "identifiers-INITIAL_FORMS": "1",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-id": str(firm.pk),
            "identifiers-0-company": str(company.pk),
            "identifiers-0-scheme": "siren",
            "identifiers-0-value": "552081317",
            "identifiers-0-label": "",
        },
    )

    assert response.status_code == 302, (
        response.context["form"].errors,
        response.context["identifiers"].errors,
    )
    company.refresh_from_db()
    assert company.name == "Aperture Science"
    kept = CompanyIdentifier.objects.get(pk=firm.pk)
    assert (kept.scheme, kept.value) == ("siren", "552081317")


def test_a_kept_row_comes_back_to_life_when_the_scheme_is_defined_again(orphans):
    person, firm = orphans
    define(STAFF, SIREN | {"link": "https://annuaire.example.org/{value}"})

    person = PersonIdentifier.objects.get(pk=person.pk)
    firm = CompanyIdentifier.objects.get(pk=firm.pk)
    assert person.scheme_label == "Staff number"
    assert person.url == "https://intranet.example.org/staff/AB-123456"
    assert firm.scheme_label == "SIREN"
    assert firm.url == "https://annuaire.example.org/552081317"
    person.full_clean()
    firm.full_clean()


def test_only_a_stored_row_keeps_a_key_nobody_defines(orphans, user, company):
    """Kept, and only kept: no new row takes such a key, and no stored row moves to one."""
    person, firm = orphans

    with pytest.raises(ValidationError):
        PersonIdentifier.objects.create(profile=user.profile, scheme="badge-x", value="1")
    with pytest.raises(ValidationError):
        PersonIdentifier(profile=user.profile, scheme="staff-number", value="ZZ-1").full_clean()
    with pytest.raises(ValidationError):
        CompanyIdentifier.objects.create(owner=user, company=company, scheme="nope", value="1")
    person.scheme = "another-gone"
    with pytest.raises(ValidationError):
        person.save()
    with pytest.raises(ValidationError):
        person.full_clean()
    # A company's scheme on a person is still refused, kept row or not (#109).
    person.scheme = "lei"
    with pytest.raises(ValidationError):
        person.save()

    # Left as it is, it is saved by whatever saves it, with no shape to hold its value to.
    firm.value = "  as it was typed  "
    firm.full_clean()
    firm.save()
    assert CompanyIdentifier.objects.get(pk=firm.pk).value == "as it was typed"


def test_a_kept_row_made_and_saved_again_without_being_read_is_still_kept(user):
    define(STAFF)
    row = PersonIdentifier.objects.create(
        profile=user.profile, scheme="staff-number", value="AB-123456"
    )
    keep("")
    row.label = ""
    row.save()
    deferred = PersonIdentifier.objects.only("value").get(pk=row.pk)
    deferred.value = "AB-000000"
    deferred.save()
    assert PersonIdentifier.objects.get(pk=row.pk).scheme == "staff-number"


def test_a_scheme_that_no_longer_identifies_a_person_keeps_the_rows_it_had(user):
    """Taking a subject out of a scheme is deleting it for that subject."""
    define(one(subjects=["person", "company"]))
    row = PersonIdentifier.objects.create(profile=user.profile, scheme="badge", value="1234")
    define(one(subjects=["company"]))

    row = PersonIdentifier.objects.get(pk=row.pk)
    assert row.scheme_label == "badge" and row.url == ""
    row.full_clean()
    row.save()


def test_the_api_keeps_a_row_it_is_sent_back_and_refuses_a_new_one(orphans, client, user, company):
    headers = bearer(user)
    listed = client.get(f"/api/v1/companies/{company.pk}", **headers).json()["identifiers"]
    assert listed == [{"scheme": "siren", "value": "552081317", "label": "", "url": ""}]

    # The list as it was given, with one added: the whole list is replaced.
    sent = [*listed, {"scheme": "wikidata", "value": "Q95"}]
    response = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps({"identifiers": sent}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    assert {row.scheme for row in company.identifiers.all()} == {"siren", "wikidata"}

    response = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps({"identifiers": [{"scheme": "siren", "value": "999999999"}]}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 422, "a value it does not hold, under a key nobody defines"


def test_merging_two_companies_moves_a_kept_row(orphans, user, company):
    from postulo.jobs import merging

    _person, firm = orphans
    other = Company.objects.create(owner=user, name="Aperture Laboratories")

    merging.merge_companies(other, company)

    assert CompanyIdentifier.objects.get(pk=firm.pk).company_id == other.pk


# ------------------------------------------------------- an archive from another instance


def archive_of(user) -> zipfile.ZipFile:
    from postulo.core.export import write_archive

    return zipfile.ZipFile(io.BytesIO(write_archive(user).getvalue()))


def test_an_archive_carries_the_identifiers_and_not_the_definitions(user, company):
    """The definitions are the instance's, and travel in its backup: they are a column of
    the policy row. A person's archive is theirs, and its shape has not changed."""
    from postulo.core.export import MANIFEST_NAME

    define(STAFF, SIREN)
    PersonIdentifier.objects.create(profile=user.profile, scheme="staff-number", value="AB-123456")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="siren", value="552081317")

    document = json.loads(archive_of(user).read(MANIFEST_NAME))

    assert document["account"]["identifiers"] == [
        {"scheme": "staff-number", "value": "AB-123456", "label": ""}
    ]
    assert document["companies"][0]["identifiers"] == [
        {"scheme": "siren", "value": "552081317", "label": ""}
    ]
    assert "[A-Z]{2}" not in json.dumps(document), "no pattern, no definition"
    assert SiteSettings._meta.get_field("identifier_schemes").get_internal_type() == "TextField"


def test_an_archive_restores_where_the_schemes_are_defined(user, other_user, company):
    from postulo.core.importer import load

    define(STAFF, SIREN)
    PersonIdentifier.objects.create(profile=user.profile, scheme="staff-number", value="AB-123456")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="siren", value="552081317")

    load(other_user, archive_of(user))

    restored = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (restored.scheme, restored.value) == ("staff-number", "AB-123456")
    assert CompanyIdentifier.objects.for_user(other_user).get().scheme == "siren"


def test_an_archive_restores_where_they_are_not_as_other_named_by_the_key(
    user, other_user, company
):
    """On an instance that never defined them -- or an older archive's reader -- the value
    is kept and says what it was, and nothing is stored under a key nobody here defines."""
    from postulo.core.importer import load
    from postulo.documents.models import CV, Prints

    define(STAFF, SIREN)
    pinned = PersonIdentifier.objects.create(
        profile=user.profile, scheme="staff-number", value="AB-123456"
    )
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="siren", value="552081317")
    cv = CV.objects.create(owner=user, name="Main", identifiers_choice=Prints.CHOSEN)
    cv.pinned_identifiers.set([pinned])
    archive = archive_of(user)
    keep("")  # the other instance

    load(other_user, archive)

    restored = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (restored.scheme, restored.label, restored.value) == (
        "other",
        "staff-number",
        "AB-123456",
    )
    firm = CompanyIdentifier.objects.for_user(other_user).get()
    assert (firm.scheme, firm.label, firm.value) == ("other", "siren", "552081317")
    # And the CV that printed it still prints it.
    theirs = CV.objects.for_user(other_user).get(name="Main")
    assert list(theirs.pinned_identifiers.all()) == [restored]


def test_a_key_this_instance_gave_to_something_else_restores_as_other_too(user, other_user):
    """Two instances are free to give one key to different things. An archive from the one
    where `member` is a person's must not end the import on the one where it is a
    company's, and must not be stored under a key that means something else here."""
    from postulo.core.importer import load

    define(one(key="member", label="Member", subjects=["person"]))
    PersonIdentifier.objects.create(profile=user.profile, scheme="member", value="1234")
    archive = archive_of(user)
    define(one(key="member", label="Member firm", subjects=["company"]))  # the other instance

    load(other_user, archive)

    restored = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (restored.scheme, restored.label, restored.value) == ("other", "member", "1234")


def test_a_key_of_postulos_own_in_an_archive_is_left_to_be_judged_as_it_always_was():
    """Only a kind this instance lacks becomes *Other*. An LEI on a person is not one."""
    assert identifiers.as_restored("orcid", "", PERSON) == ("orcid", "")
    assert identifiers.as_restored("lei", "", PERSON) == ("lei", "")
    assert identifiers.as_restored("other", "Library card", PERSON) == ("other", "Library card")
    assert identifiers.as_restored("gone", "", PERSON) == ("other", "gone")
    assert identifiers.as_restored("gone", "Its name", PERSON) == ("other", "Its name")
    assert identifiers.as_restored("x" * 80, "", PERSON) == ("other", "x" * 60)
    # What is not a key at all is handed back for the import to pass over, as it did.
    assert identifiers.as_restored("", "x", PERSON) == ("", "x")
    assert identifiers.as_restored(7, "", PERSON) == (7, "")


# ------------------------------------------------- what is drawn for everybody is escaped

MARKUP = '<img src=x onerror="alert(1)">'


def test_markup_in_a_label_an_example_and_a_link_is_never_markup_on_a_page(client, user, company):
    """The three things an administrator writes that every person is shown."""
    hostile = {
        "key": "hostile",
        "label": {"en-GB": MARKUP},
        "subjects": ["person", "company"],
        "pattern": "[^ ]{1,60}",
        "example": '"><script>alert(2)</script>',
        "link": 'https://example.org/x/{value}?a=<b>&c="d"\'',
    }
    define(hostile)
    assert identifiers.find("hostile") is not None, "it is a scheme: the page must cope"
    value = '"><svg/onload=alert(3)>'
    PersonIdentifier.objects.create(profile=user.profile, scheme="hostile", value=value)
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="hostile", value=value)
    client.force_login(user)

    pages = [
        reverse("accounts:profile"),
        reverse("jobs:company_detail", args=[company.pk]),
        reverse("jobs:company_update", args=[company.pk]),
        reverse("jobs:company_create"),
    ]
    for url in pages:
        html = client.get(url).content.decode()
        assert "&lt;img src=x onerror=" in html, url
        for raw in ("<img src=x", "<script>alert(2)", "<svg/onload", 'onerror="alert'):
            assert raw not in html, (url, raw)

    html = client.get(pages[1]).content.decode()
    link = re.search(r'<a href="(https://example\.org/x/[^"]*)"', html)
    assert link, "the link is drawn, inside its attribute"
    assert "%22%3E%3Csvg%2Fonload%3Dalert%283%29%3E" in link[1], "the value, percent-encoded"
    assert "&lt;b&gt;" in link[1] and "<b>" not in link[1], "and the template, escaped"

    # The sentence that refuses a value quotes the label and the example.
    response = post_details(
        client, user, **{"identifiers-0-scheme": "hostile", "identifiers-0-value": "a b"}
    )
    html = response.content.decode()
    assert "does not look like a &lt;img src=x" in html
    assert "<script>alert(2)" not in html and "<img src=x" not in html


def test_markup_in_the_definitions_is_escaped_where_the_administrator_reads_it_back(client, admin):
    text = written(one(label=MARKUP, patern="</textarea><script>alert(1)</script>"))
    client.force_login(admin)

    response = client.post(reverse("server:identifier_schemes"), {"identifier_schemes": text})

    html = response.content.decode()
    assert response.status_code == 200
    assert "</textarea><script>" not in html and "<img src=x" not in html
    assert "&lt;/textarea&gt;&lt;script&gt;" in html, "the box keeps what was typed, escaped"


# ---------------------------------------------------------------------------- the page


def test_the_page_and_the_dialog_are_for_administrators_only(client, user):
    """Whoever may change Server settings, and nobody else: asked or posted to."""
    url = reverse("server:identifier_schemes")
    text = written(STAFF)

    assert client.get(url).status_code == 302, "anonymous: to the sign-in page"
    assert client.post(url, {"identifier_schemes": text}).status_code == 302
    client.force_login(user)
    assert client.get(url).status_code == 403
    assert client.post(url, {"identifier_schemes": text}).status_code == 403
    assert client.post(url, {"identifier_schemes": text}, HTTP_HX_REQUEST="true").status_code == 403

    assert not SiteSettings.objects.exclude(identifier_schemes="").exists()
    assert identifiers.find("staff-number") is None


def test_a_forged_post_is_refused(admin):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(reverse("server:identifier_schemes"), {"identifier_schemes": "[]"})
    assert response.status_code == 403


def test_the_registry_has_a_button_on_plugins_that_opens_its_settings(client, admin):
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    row = html[html.index('data-policy="identifiers"') :]
    row = row[: row.index("</li>")]
    url = reverse("server:identifier_schemes")

    # A link to the page, which is the whole of it with scripts off, naming the dialog a
    # script opens instead.
    assert f'<a href="{url}"' in row
    assert 'data-opens-dialog="settings-identifiers"' in row
    assert "Settings" in row and "External identifiers" in row
    # No other plugin has one.
    assert html.count("data-opens-dialog=") == 1

    dialog = html[html.index('<dialog popover id="settings-identifiers"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    assert 'class="dialog"' in dialog and 'role="alertdialog"' not in dialog
    assert f'<form method="post" action="{url}"' in dialog and f'hx-post="{url}"' in dialog
    assert 'name="csrfmiddlewaretoken"' in dialog
    assert '<textarea name="identifier_schemes"' in dialog
    assert 'aria-labelledby="settings-identifiers-title"' in dialog
    # What a refusal replaces is named on the dialog, for the form inside it to inherit:
    # what is marked busy while the request runs is then the element the answer replaces.
    assert 'hx-target="#identifier-schemes-fields"' in dialog[: dialog.index("<form")]
    assert 'id="identifier-schemes-fields"' in dialog
    # Not inside the form the switches are in: a form cannot hold another.
    policy = html[html.index(f'action="{reverse("server:plugin_policy")}"') :]
    assert "settings-identifiers" not in policy[: policy.index("</form>")].replace(
        'data-opens-dialog="settings-identifiers"', ""
    )


def test_one_persons_plugins_page_has_no_button(client, admin, user):
    client.force_login(admin)
    html = client.get(reverse("server:person_plugins", args=[user.pk])).content.decode()
    assert "data-opens-dialog" not in html


def test_the_page_draws_the_box_with_what_is_kept_and_sits_under_plugins(client, admin):
    define(STAFF)
    client.force_login(admin)
    html = client.get(reverse("server:identifier_schemes")).content.decode()

    assert "<h1" in html and "Identifiers of your own" in html
    assert "&quot;key&quot;: &quot;staff-number&quot;" in html, "the text as it was typed"
    box = re.search(r"<textarea[^>]*>", html)[0]
    assert 'aria-describedby="id_identifier_schemes_helptext"' in box
    assert "aria-invalid" not in box and "autofocus" not in box
    assert 'id="id_identifier_schemes_helptext"' in html
    assert "What a scheme can say" in html
    assert html.count('aria-current="page"') == 1
    current = html[html.index('aria-current="page"') - 200 : html.index('aria-current="page"')]
    assert reverse("server:plugins") in current, "the sidebar marks Plugins"


def test_saving_the_page_defines_the_schemes_and_goes_back_to_plugins(client, admin, user):
    client.force_login(admin)
    text = written(STAFF, SIREN)

    response = client.post(
        reverse("server:identifier_schemes"), {"identifier_schemes": text}, follow=True
    )

    assert response.redirect_chain == [(reverse("server:plugins"), 302)]
    assert "Saved." in response.content.decode()
    row = SiteSettings.objects.get(pk=1)
    assert row.identifier_schemes == text, "kept as it was typed, layout and all"
    assert row.updated_by == admin
    assert {"staff-number", "siren"} <= set(identifiers.registry())


def test_the_line_breaks_a_browser_sends_are_made_one_kind(client, admin):
    """A browser posts a text box with CR LF. A line number has to mean the same thing to
    the server, the box and whoever reads the sentence."""
    client.force_login(admin)
    text = written(one(pattern="a+"))

    response = client.post(
        reverse("server:identifier_schemes"),
        {"identifier_schemes": "\ufeff" + text.replace("\n", "\r\n")},
    )
    assert "Line 8, scheme “badge”" in response.content.decode()

    client.post(
        reverse("server:identifier_schemes"),
        {"identifier_schemes": written(STAFF).replace("\n", "\r\n")},
    )
    assert SiteSettings.objects.get(pk=1).identifier_schemes == written(STAFF)


def test_emptying_the_box_defines_none(client, admin):
    define(STAFF)
    client.force_login(admin)
    response = client.post(reverse("server:identifier_schemes"), {"identifier_schemes": "  \n "})
    assert response.status_code == 302
    assert SiteSettings.objects.get(pk=1).identifier_schemes == ""
    assert identifiers.find("staff-number") is None


def test_a_refused_text_is_said_at_its_line_and_the_box_keeps_what_was_typed(client, admin):
    define(SIREN)
    client.force_login(admin)
    text = written(STAFF, one(pattern=r"\d+", label=7))

    response = client.post(reverse("server:identifier_schemes"), {"identifier_schemes": text})

    assert response.status_code == 200
    html = response.content.decode()
    # The line number and the scheme's key, in the sentence.
    assert "Line 18, scheme “badge”: a label is a name in double quotes" in html
    assert "Line 22, scheme “badge”: the pattern cannot be used, at character 3." in html
    # The box keeps what was typed, is marked invalid, is described by the errors, and
    # takes the focus, with the first problem's line for the caret.
    box = re.search(r"<textarea[^>]*>", html)[0]
    assert 'aria-invalid="true"' in box
    assert "id_identifier_schemes_error" in re.search(r'aria-describedby="([^"]*)"', box)[1]
    assert "autofocus" in box
    assert 'data-caret-line="18"' in box and "data-caret-column" not in box
    assert "&quot;key&quot;: &quot;staff-number&quot;" in html
    assert "&quot;pattern&quot;: &quot;\\\\d+&quot;" in html
    errors = html[html.index('id="id_identifier_schemes_error"') :]
    assert 'role="alert"' in errors[:80]
    # Nothing was saved, and what was in force still is.
    assert SiteSettings.objects.get(pk=1).identifier_schemes == written(SIREN)
    assert identifiers.find("badge") is None and identifiers.find("siren") is not None


def test_text_that_is_not_json_names_the_line_and_the_column_on_the_box(client, admin):
    client.force_login(admin)
    response = client.post(
        reverse("server:identifier_schemes"),
        {"identifier_schemes": '[\n  {"key": "a",\n   oops}\n]'},
    )
    html = response.content.decode()
    assert "Line 3, column 4: a name in double quotes should begin here." in html
    box = re.search(r"<textarea[^>]*>", html)[0]
    assert 'data-caret-line="3"' in box and 'data-caret-column="4"' in box


def test_a_text_with_many_problems_spells_out_ten_and_counts_the_rest(client, admin):
    client.force_login(admin)
    broken = [one(key=f"k{number}", pattern="a+") for number in range(13)]

    html = client.post(
        reverse("server:identifier_schemes"), {"identifier_schemes": written(*broken)}
    ).content.decode()

    assert html.count("the pattern cannot be used") == 10
    assert "And 3 more problems further down." in html


def test_the_dialog_is_answered_with_the_fields_alone_when_the_text_is_refused(client, admin):
    """Sent by htmx, a refusal goes where the dialog's fields are and the dialog stays
    open over what was typed."""
    client.force_login(admin)
    text = written(one(pattern="a+"))

    response = client.post(
        reverse("server:identifier_schemes"), {"identifier_schemes": text}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert "HX-Retarget" not in response.headers, "the dialog names the target itself"
    html = response.content.decode()
    assert html.lstrip().startswith('<div id="identifier-schemes-fields" ')
    assert "<html" not in html and "<h1" not in html, "the fields, and no page around them"
    assert "Line 8, scheme “badge”: the pattern cannot be used" in html
    assert "autofocus" in html and 'data-caret-line="8"' in html
    assert not SiteSettings.objects.exclude(identifier_schemes="").exists()


def test_the_dialog_is_sent_to_plugins_when_the_text_is_saved(client, admin):
    client.force_login(admin)

    response = client.post(
        reverse("server:identifier_schemes"),
        {"identifier_schemes": written(STAFF)},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == reverse("server:plugins")
    assert identifiers.find("staff-number") is not None
    assert "Saved." in client.get(reverse("server:plugins")).content.decode()


def test_the_form_in_the_dialog_asks_for_the_focus_and_the_page_does_not(client, admin):
    """Opened as a modal, a dialog gives focus to the first thing that asks for it: the box
    it was opened for, which stands before *Cancel*."""
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    dialog = html[html.index('<dialog popover id="settings-identifiers"') :]
    box = re.search(r"<textarea[^>]*>", dialog)[0]
    assert "autofocus" in box
    assert dialog.index("<textarea") < dialog.index("data-dialog-close")


# ------------------------------------- reading the definitions takes a bounded time (#311)
#
# A text is read when it is saved and again by each process the first time a page asks, in
# somebody's request. Its size is bounded, and that turned out to bound nothing: `re`
# prepares a class by walking the characters it is written with, one by one, in Python,
# and a pattern of 75 classes that each took nearly all of Unicode took a quarter of a
# second -- a text of a hundred such patterns, 18 seconds and more, inside every bound. So
# a class that takes most characters is written as the ones it leaves out, a pattern's
# classes have a budget between them (`patterns.MAX_CLASS_WEIGHT`), and the reading of the
# costliest text the bounds allow is **measured** here and not assumed.

#: How long reading any text the bounds allow may take, in seconds of this process's own
#: work. The costliest of the texts below took 0.33 to 0.66 seconds on the machine this was
#: written on, and this is more than four times that, for a slower machine. Before the
#: budget the same kinds of text took 4 to 31 seconds.
READING_PATIENCE = 3.0


def longest_accepted(classes) -> list[int]:
    """As many of these classes in a row as the language accepts in one pattern, as which
    of them they are: each is added if the pattern is still accepted with it, and passed
    over if it is not."""
    source, kept = "", []
    for at, written in enumerate(classes):
        if len(source) + len(written) > patterns.MAX_PATTERN_LENGTH:
            continue
        try:
            patterns.read(source + written)
        except patterns.Refused:
            continue
        source += written
        kept.append(at)
    return kept


def _negated(seed: int, at: int) -> str:
    return f"[^{chr(0x4E00 + seed * 100 + at)}]"


def _half_the_plane(seed: int, at: int) -> str:
    return f"[{chr(0x100 + seed + at)}-{chr(0x7FFF - at)}]"


def _scattered(seed: int, at: int) -> str:
    return "[" + "".join(chr(0x391 + seed + at * 7 + step) for step in (0, 2, 4)) + "]"


def _narrow(seed: int, at: int) -> str:
    """Two Greek letters side by side: little in the budget, and still a class for `re` to
    lay the plane out for."""
    first = 0x391 + (seed * 3 + at * 5) % 400
    return f"[{chr(first)}{chr(first + 1)}]"


def _plain(seed: int, at: int) -> str:
    """Two ASCII letters side by side: next to nothing in the budget, and a class all the
    same. What the rest of a pattern is filled with once the budget is as good as spent."""
    first = ord("a") + (seed + at) % 25
    return f"[{chr(first)}{chr(first + 1)}]"


#: The costliest constructs the language accepts, as the classes of one pattern, each
#: different from the last so that nothing is prepared once and used twice. ``seed`` tells
#: one scheme's pattern from another's.
COSTLY = {
    # Nearly everything: one character left out. What the review measured.
    "negated classes": lambda seed: (_negated(seed, at) for at in range(75)),
    # Half of the characters `re` walks, which neither way of writing makes fewer.
    "classes half the plane wide": lambda seed: (_half_the_plane(seed, at) for at in range(60)),
    # Three characters apart, beyond Latin-1: narrow, and kept as a map of the whole plane.
    "scattered classes beyond Latin-1": lambda seed: (_scattered(seed, at) for at in range(60)),
    "classes of ideographs, counted": lambda seed: (
        f"[{chr(0x4E00 + seed + at)}-鿿]{{2}}" for at in range(40)
    ),
    "the three together": lambda seed: (
        written(seed, at) for at in range(25) for written in (_negated, _half_the_plane, _scattered)
    ),
}


def seconds_to_read(text: str) -> tuple[float, object]:
    """How long the text takes to read with nothing prepared beforehand, and what it came
    to. The time this process spent working, which is what the reading costs: a machine
    busy with something else makes a request wait longer and does not make a reading
    slower. The faster of two, all the same."""
    fastest, reading = float("inf"), None
    for _attempt in range(2):
        re.purge()
        began = time.process_time()
        reading = custom.read(text, taken=SCHEMES)
        fastest = min(fastest, time.process_time() - began)
        if fastest <= READING_PATIENCE:
            break
    return fastest, reading


@pytest.mark.parametrize("kind", COSTLY)
def test_the_costliest_text_the_bounds_allow_is_read_in_a_bounded_time(kind):
    """A hundred schemes, each with a pattern of the costliest classes the language will
    take: as many of them as it accepts, then narrow ones with what is left of the budget,
    and then classes of two ASCII letters, which cost it next to nothing, for as long as
    there is room. That is the worst an administrator -- or a text that reached the row
    some other way -- can ask of the reading."""

    def classes(seed: int) -> list[str]:
        return [
            *COSTLY[kind](seed),
            *(_narrow(seed, at) for at in range(75)),
            *(_plain(seed, at) for at in range(75)),
        ]

    # Which of them fit is worked out once: every scheme's classes cost what the first's do.
    kept = longest_accepted(classes(0))
    schemes = []
    for seed in range(custom.MAX_SCHEMES):
        written = classes(seed)
        schemes.append(one(key=f"k{seed}", pattern="".join(written[at] for at in kept)))
    text = json.dumps(schemes, ensure_ascii=False)
    assert len(text) <= custom.MAX_CHARACTERS

    took, reading = seconds_to_read(text)

    assert not reading.problems and len(reading.schemes) == custom.MAX_SCHEMES, "it is accepted"
    assert took < READING_PATIENCE, f"{took:.1f} s to read {len(text)} characters of {kind}"


@pytest.mark.parametrize("kind", COSTLY)
def test_a_pattern_past_the_budget_is_refused_before_anything_is_prepared(kind):
    """And the patterns the budget refuses cost nothing to refuse: the classes are counted
    as they are read, before `re` is handed any of them."""
    schemes = []
    for seed in range(custom.MAX_SCHEMES):
        source = "".join(COSTLY[kind](seed))[: patterns.MAX_PATTERN_LENGTH]
        # Cut at 300 characters, which may be in the middle of a class: end on a whole one.
        source = source[: max(source.rfind("]"), source.rfind("}")) + 1]
        schemes.append(one(key=f"k{seed}", pattern=source))
    text = json.dumps(schemes, ensure_ascii=False)

    took, reading = seconds_to_read(text)

    assert took < READING_PATIENCE, f"{took:.1f} s to read {len(text)} characters of {kind}"
    assert len(reading.problems) == custom.MAX_SCHEMES and not reading.schemes
    for problem in reading.problems:
        assert "between them" in str(problem), str(problem)


def test_the_classes_of_one_pattern_have_a_budget_and_the_refusal_says_where():
    """What a class costs is the characters `re` walks to prepare it -- the fewer of those
    it takes and those it leaves out, below U+10000 -- and something for a class that
    reaches past Latin-1. A pattern's classes may come to `MAX_CLASS_WEIGHT` between them."""
    assert patterns.MAX_CLASS_WEIGHT == 65_536
    # What a person writes costs next to nothing, however often it is written.
    for source in (
        r"[A-Z]{2}\d{6}",
        r"[A-Za-zÀ-ÿ]{1,30}-[A-Za-zÀ-ÿ]{1,30}",
        "".join(r"[A-Z0-9]" for _ in range(37)),
        r"\w{1,40}",
        "".join(r"[α-ωά-ώ]" for _ in range(37)),
        # Every ideograph, three times; every Hangul syllable, five times; any character
        # but a space, in each of twelve alternatives.
        r"[一-鿿]{1,8}-[一-鿿]{1,8}-[一-鿿]{1,8}",
        r"[가-힣]{2}-[가-힣]{2}-[가-힣]{2}-[가-힣]{2}-[가-힣]{2}",
        "|".join(rf"{letter}[^ ]{{1,8}}" for letter in "ABCDEFGHIJKL"),
    ):
        assert patterns.compiled(source), source

    wide = [_half_the_plane(0, at) for at in range(5)]
    assert patterns.compiled("AB-" + wide[0])
    with pytest.raises(patterns.Refused) as refused:
        patterns.compiled("AB-" + "".join(wide))
    # At the class that goes past it, counted from one: the second, of five characters each.
    assert refused.value.at == 9
    assert "between them" in str(refused.value.reason)
    (said,) = problems(one(pattern="AB-" + "".join(wide)))
    assert said.startswith("Line 8, scheme “badge”: the pattern cannot be used, at character 9.")
    # A class written with “^” is written for `re` with everything no class takes beside
    # what it leaves out, and kept as a map of the plane: twelve to a pattern, and no more.
    assert patterns.compiled("-".join("[^ ]" for _ in range(12)))
    with pytest.raises(patterns.Refused) as refused:
        patterns.compiled("-".join("[^ ]" for _ in range(13)))
    assert refused.value.at == 61


def members_of(chars, code: int) -> bool:
    return any(low <= code <= high for low, high in chars)


#: A class written to take everything: the printable ASCII, then from the no-break space to
#: the last character before the surrogates, then from the first after them to the end.
EVERY_CHARACTER = f"[ -~{chr(0xA0)}-{chr(0xD7FF)}{chr(0xE000)}-{chr(0x10FFFF)}]"


@pytest.mark.parametrize(
    "source",
    [
        r"[^ /]",
        r"[^一]",
        r"[^a-zA-Z0-9_]",
        r"[^À-ÿ\d]",
        r"[^😀-🙏]",
        r"[Ā-翿]",
        r"[Ā-￿]",
        r"[ -￿]",
        r"[一-鿿]",
        r"[A-Za-zÀ-ÿ]",
        r"[😀-🙏a]",
        r"[^\]\\^-]",
        # Every character there is; what is left without most of the plane; the far end.
        EVERY_CHARACTER,
        f"[^A-Za-z{chr(0xA0)}-{chr(0xD7FF)}]",
        f"[{chr(0xE000)}-{chr(0x10FFFF)}]",
    ],
)
def test_a_class_takes_the_same_characters_however_it_is_written_for_re(source):
    """A class that takes most characters is handed to `re` as the ones it leaves out. That
    is a change of spelling and of nothing else: at every edge of every range, of what it
    takes and of what no class takes, the compiled pattern and the class as it was read
    agree about the character."""
    ((piece,),) = patterns.read(source)
    compiled = patterns.compiled(source)
    edges = {0, 0x7F, 0xFF, 0x100, 0xFFFF, 0x10000, 0x10FFFF, 0xD7FF, 0xD800, 0xDFFF, 0xE000}
    for low, high in (*piece.chars, *patterns._NEVER):
        edges |= {max(low - 1, 0), low, high, min(high + 1, 0x10FFFF)}
    edges |= set(range(0x00, 0x180)) | set(range(0x2020, 0x2030))
    for code in sorted(edges):
        assert bool(compiled.match(chr(code))) is members_of(piece.chars, code), hex(code)
    # Nobody's, whatever the class says: a control character, a line end, half a character.
    for never in ("\x00", "\n", "\x7f", "\x85", chr(0x2028), chr(0x2029), chr(0xD83D), chr(0xDFFF)):
        assert not compiled.match(never), repr(never)


def test_most_of_unicode_is_written_as_what_it_leaves_out():
    """What made the reading slow, held where it was fixed: a class that takes nearly every
    character is not spelt out range by range for `re` to walk."""
    assert patterns.compiled(r"[^ /]{1,50}").pattern.startswith(r"\A(?:[^")
    assert patterns.compiled(r"[A-Z]{2}").pattern == r"\A(?:[\u0041-\u005a]{2})\Z"
    assert patterns.compiled(r"[一-鿿]").pattern == r"\A(?:[\u4e00-\u9fff])\Z"
    # Possessive still, and one reading still: only the spelling of the class has changed.
    assert patterns.compiled(r"[^ /]{1,50}").pattern.endswith(r"]{1,50}+)\Z")


# -------------------------------------------- small things the reader used to let through


def test_a_language_written_twice_in_a_label_is_refused_as_a_name_written_twice_is():
    """The parser keeps the last and says nothing, inside a label as anywhere else."""
    text = "\n".join(
        [
            "[",
            ' {"key": "badge", "subjects": ["person"], "pattern": "a",',
            '  "label": {"en-GB": "First",',
            '            "pt-PT": "Primeiro",',
            '            "en-GB": "Second"}}',
            "]",
        ]
    )
    reading = custom.read(text, taken=SCHEMES)

    assert reading.schemes == ()
    assert [str(problem) for problem in reading.problems] == [
        "Line 3, scheme “badge”: the language “en-GB” is written twice in “label”."
    ]


@pytest.mark.parametrize(("source", "at"), [(r"[^]", 1), (r"AB-[^]{2}", 4), (r"\d{2}|[^]", 7)])
def test_a_class_that_leaves_nothing_out_is_refused_as_a_full_stop_is(source, at):
    """`[^]` is *any character* written as a class, and any character is what the language
    has not got: it says so, and says what to write, as it does for a full stop."""
    with pytest.raises(patterns.Refused) as refused:
        patterns.compiled(source)
    assert refused.value.at == at
    assert "means any character, and that is not part of this language" in str(refused.value.reason)
    assert "[^ ]" in str(refused.value.reason)
    with pytest.raises(patterns.Refused) as full_stop:
        patterns.compiled("a.b")
    assert "any character" in str(full_stop.value.reason)


@pytest.mark.parametrize(
    ("fold", "pattern", "said"),
    [
        ("upper", r"[a-z]{3}", "takes nothing written in capitals"),
        ("upper", r"ab-\d{4}", "takes nothing written in capitals"),
        ("upper", r"\d{2}(x)\d{2}", "takes nothing written in capitals"),
        ("upper", r"[a-f]{8}|[a-f]{4}-\d{4}", "takes nothing written in capitals"),
        ("upper", "ß{2}", "takes nothing written in capitals"),
        ("lower", r"[A-Z]{2}-\d{6}", "takes nothing written in small letters"),
        ("lower", r"ID\d{4}", "takes nothing written in small letters"),
        ("lower", r"[A-F0-9]{4}[A-F]", "takes nothing written in small letters"),
    ],
)
def test_a_pattern_that_accepts_nothing_once_the_value_is_folded_is_refused(fold, pattern, said):
    """`"upper": true` over `[a-z]{3}` looks like a scheme and refuses every value there
    is: what reaches the pattern is in capitals. An example would have shown it, and an
    example is not required. The sentence says why, on the line the fold is on."""
    (problem,) = problems(one(pattern=pattern, **{fold: True}))
    assert problem.startswith(f"Line 9, scheme “badge”: “{fold}” puts what was typed into ")
    assert said in problem and "would accept no value at all" in problem


@pytest.mark.parametrize(
    ("fold", "pattern", "value"),
    [
        ("upper", r"[A-Z]{2}-\d{6}", "ab-123456"),
        ("upper", r"[A-Za-z]{3}", "abc"),
        ("upper", r"\d{4}", "1234"),
        # A piece that need not be there can be of the other case: it is never met.
        ("upper", r"[a-z]?\d{3}", "123"),
        # One alternative is enough.
        ("upper", r"[a-z]{2}|[A-Z]{3}", "abc"),
        ("upper", r"[^ ]{4}", "ab-1"),
        ("lower", r"[a-z0-9]{1,12}", "AbC1"),
        ("lower", r"[^ ]{4}", "AB-1"),
        ("lower", r"[A-Za-zÀ-ÿ]{3}", "ÀBC"),
    ],
)
def test_a_pattern_that_still_accepts_something_folded_is_left_alone(fold, pattern, value):
    define(one(pattern=pattern, **{fold: True}))
    assert person_identifiers.clean("badge", value) == getattr(value, fold)()


#: Characters that take no room, and characters that turn the text around.
ZERO_WIDTH_SPACE, WORD_JOINER, NO_BREAK_NOTHING = chr(0x200B), chr(0x2060), chr(0xFEFF)
OVERRIDE, EMBEDDING, ISOLATE, POP = chr(0x202E), chr(0x202A), chr(0x2067), chr(0x202C)
NON_JOINER, JOINER, LEFT_TO_RIGHT_MARK = chr(0x200C), chr(0x200D), chr(0x200E)


@pytest.mark.parametrize(
    ("label", "said"),
    [
        (ZERO_WIDTH_SPACE, "a name cannot hold U+200B"),
        (NO_BREAK_NOTHING, "a name cannot hold U+FEFF"),
        (f"Sta{WORD_JOINER}ff", "a name cannot hold U+2060"),
        (f"{OVERRIDE}evil", "a name cannot hold U+202E"),
        (f"{EMBEDDING}Staff{POP}", "a name cannot hold U+202A"),
        (f"Staff {ISOLATE}number", "a name cannot hold U+2067"),
        # Seen by nobody: what is left once what is not drawn is taken out is nothing.
        (JOINER, "a name has to be something that can be seen"),
        (f"{LEFT_TO_RIGHT_MARK} {NON_JOINER}", "a name has to be something that can be seen"),
        (chr(0xAD), "a name has to be something that can be seen"),
    ],
)
def test_a_name_has_to_be_seen_and_to_read_as_it_is_written(label, said):
    """A label is drawn in a menu on every page that offers the scheme. One that takes no
    room is a blank line in that menu, and one that turns the text around reads as another
    word; neither is a name."""
    (problem,) = problems(one(label=label))
    assert problem.startswith("Line 4, scheme “badge”: ") and said in problem

    (problem,) = problems(one(label={"en-GB": "Badge", "pt-PT": label}))
    assert problem.startswith("Line 4, scheme “badge”: in “pt-PT”, ") and said in problem


def test_a_name_may_hold_the_joiners_a_language_is_spelt_with():
    """Persian writes *staff number* with a zero-width non-joiner in it, and Hindi joins
    with the other. They shape the letters beside them and hide nothing."""
    persian = f"شماره{NON_JOINER}ی کارمندی"
    define(one(label={"en-GB": "Staff number", "fa": persian, "hi": f"क्{JOINER}ष"}))
    with translation.override("en-GB"):
        assert identifiers.label_for("badge") == "Staff number"
    assert not problems(one(label=persian))


def test_what_one_scheme_gets_wrong_ten_times_is_counted_after_that(caplog):
    """A scheme of thousands of names it cannot say is one mistake. Ten are spelt out and
    the rest counted, where the text is read and so in the log: it used to be a sentence
    and a line of the log for each."""
    misspelt = {f"n{number}": 0 for number in range(4000)}
    text = json.dumps([one(**misspelt), SIREN])
    assert len(text) <= custom.MAX_CHARACTERS

    reading = custom.read(text, taken=SCHEMES)

    said = [str(problem) for problem in reading.problems]
    assert len(said) == 11
    assert all("is not something a scheme can say" in sentence for sentence in said[:10])
    assert said[10] == (
        "Line 1, scheme “badge”: And 3990 more problems of the same kind in this scheme."
    )
    assert [scheme.key for scheme in reading.schemes] == ["siren"]

    with caplog.at_level("WARNING", logger="postulo.core.identifiers"):
        keep(text)
        assert [scheme.key for scheme in identifiers.defined_here()] == ["siren"]
    assert len([record for record in caplog.records if "left out" in record.message]) == 11

    # A name written twice, and a label in languages that are none, are counted likewise.
    twice = '[{"key": "badge", ' + ", ".join('"key": "badge"' for _ in range(30)) + "}]"
    said = [str(problem) for problem in custom.read(twice).problems]
    assert said[9] == "Line 1, scheme “badge”: “key” is written twice."
    assert said[10].endswith("And 20 more problems of the same kind in this scheme.")
    languages = {f"not a language {number}": "Badge" for number in range(25)}
    said = problems(one(label=languages))
    assert len(said) == 11
    assert said[10].endswith("And 15 more problems of the same kind in this scheme.")


# --------------------------------------- half of a character is not text (#311, the review)
#
# JSON writes a character beyond U+FFFF as two escapes, and the parser reads one of the two
# by itself without a word. It is not text: nothing can encode it, the database cannot
# hold it, and a page that came to draw it answered 500 -- for every person on the
# instance, on *Your details* and on every company, while the page the administrator saved
# it from went on working. The escape is ASCII, so the text that holds it stores.

#: Half of a character, as an administrator would type it: six ASCII characters.
HALF = "\\ud83d"


def with_half(**changes) -> str:
    """A scheme with ``HALF`` wherever a change says ``<half>``, as the text of the box."""
    return written(one(**changes)).replace("<half>", HALF)


@pytest.mark.parametrize(
    ("changes", "line", "name"),
    [
        ({"label": "Badge <half>"}, 4, "label"),
        ({"label": {"en-GB": "Badge", "pt-PT": "Crachá <half>"}}, 4, "label"),
        ({"label": {"en-GB": "Badge", "<half>": "Crachá"}}, 4, "label"),
        ({"example": "12<half>"}, 9, "example"),
        ({"link": "https://example.org/<half>/{value}"}, 9, "link"),
        ({"person_link": "https://example.org/{value}#<half>"}, 9, "person_link"),
        ({"pattern": "[^ ]{4}<half>"}, 8, "pattern"),
        ({"checksum": "iso7064-<half>"}, 9, "checksum"),
        ({"subjects": ["person", "<half>"]}, 5, "subjects"),
        ({"key": "badge<half>"}, 3, "key"),
    ],
)
def test_half_of_a_character_is_refused_wherever_it_is_written_with_its_line(changes, line, name):
    reading = custom.read(with_half(**changes), taken=SCHEMES)

    assert reading.schemes == ()
    (problem,) = reading.problems
    where = "scheme number 1" if name == "key" else "scheme “badge”"
    assert str(problem) == (
        f"Line {line}, {where}: “{name}” holds {HALF}, which is half of a character and "
        "not text by itself. Write the character it was meant to be, or both of its halves."
    )
    str(problem).encode("utf-8")


def test_both_halves_of_a_character_are_the_character():
    """Written as JSON writes it, a character beyond U+FFFF is two escapes and one name."""
    text = written(one(label="Badge <both>")).replace("<both>", "\\ud83d\\ude00")
    (scheme,) = custom.read(text, taken=SCHEMES).schemes
    assert scheme.label == "Badge " + chr(0x1F600)


def test_a_name_a_scheme_cannot_say_is_quoted_back_as_text_whatever_it_holds():
    """The sentence that refuses a name quotes it, and is drawn on the administrator's
    page: half of a character in the name is quoted as it was typed."""
    text = written(one(**{"patern<half>": 1})).replace("<half>", HALF)
    (problem,) = custom.read(text, taken=SCHEMES).problems
    assert f"“patern{HALF}” is not something a scheme can say" in str(problem)
    str(problem).encode("utf-8")


def test_the_page_refuses_half_of_a_character_and_names_the_line(client, admin):
    """What the review typed: the save used to answer *Saved.*"""
    client.force_login(admin)
    text = with_half(
        label="Badge <half>",
        subjects=["person", "company"],
        link="https://example.org/<half>/{value}",
    )

    response = client.post(reverse("server:identifier_schemes"), {"identifier_schemes": text})

    assert response.status_code == 200
    html = response.content.decode()
    assert "Line 4, scheme “badge”: “label” holds \\ud83d, which is half of a character" in html
    assert "Line 10, scheme “badge”: “link” holds \\ud83d" in html
    assert not SiteSettings.objects.exclude(identifier_schemes="").exists()


def every_page(client, user, company) -> list[str]:
    client.force_login(user)
    return [
        reverse("accounts:profile"),
        reverse("jobs:company_create"),
        reverse("jobs:company_update", args=[company.pk]),
        reverse("jobs:company_detail", args=[company.pk]),
    ]


def test_a_stored_text_with_half_a_character_takes_no_page_down(client, user, company, admin):
    """Written past the page, as a restored backup would: the scheme is left out, and the
    four pages that draw the registry answer as they did. So do the administrator's."""
    keep(with_half(label="Badge <half>", subjects=["person", "company"]))

    assert identifiers.find("badge") is None
    for url in every_page(client, user, company):
        assert client.get(url).status_code == 200, url
    client.force_login(admin)
    for name in ("server:plugins", "server:identifier_schemes"):
        assert client.get(reverse(name)).status_code == 200, name


def test_nothing_the_registry_hands_a_page_can_fail_to_be_drawn(
    client, user, company, monkeypatch, caplog
):
    """The lesson of it, held where the registry is built and not only where the text is
    read: whatever read the text, a scheme whose name, example or link cannot be encoded
    is left out and logged, as any other refused scheme is. A plugin of somebody else's
    that read the text carelessly cannot take *Your details* down for everybody."""
    half = chr(0xD83D)
    shape = re.compile("x")

    class Raises:
        def __str__(self) -> str:
            raise RuntimeError("a label that cannot be asked")

    handed = (
        identifiers.Scheme("fine", "Fine", shape, provider=identifiers.DEFINED_HERE),
        identifiers.Scheme("in-the-label", f"Badge {half}", shape),
        identifiers.Scheme("in-the-example", "Badge", shape, example=half),
        identifiers.Scheme("in-the-link", "Badge", shape, link=f"https://example.org/{half}"),
        identifiers.Scheme("in-the-other-link", "Badge", shape, person_link=half),
        identifiers.Scheme(f"key-{half}", "Badge", shape),
        identifiers.Scheme("raises", Raises(), shape),
    )
    monkeypatch.setattr(
        identifiers, "_reader", lambda text, taken: identifiers.Reading(schemes=handed)
    )
    with caplog.at_level("WARNING", logger="postulo.core.identifiers"):
        keep("anything at all")
        assert [scheme.key for scheme in identifiers.defined_here()] == ["fine"]

    for key in ("in-the-label", "in-the-example", "in-the-link", "in-the-other-link", "raises"):
        assert f"“{key}”" in caplog.text
    reading = identifiers.read_definitions("anything at all")
    assert len(reading.problems) == 6
    for problem in reading.problems:
        str(problem).encode("utf-8")
    for url in every_page(client, user, company):
        assert client.get(url).status_code == 200, url
    with pytest.raises(ValueError, match="cannot be encoded"):
        identifiers.in_languages({"en-GB": "Badge", "pt-PT": f"Crachá {half}"})


def hostile_strings():
    """Texts built to be awkward to draw: halves of characters alone, the wrong way round
    and among text, characters that end lines, turn text round or take no room, markup, and
    what a format string would read. Seeded, so the same ones every run."""
    import random

    chance = random.Random(311)  # noqa: S311 - draws test values, guards nothing
    pieces = [
        *(chr(code) for code in (0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0xD83D, 0xDE00)),
        chr(0xDE00) + chr(0xD83D),
        *(chr(code) for code in (0x00, 0x0A, 0x1B, 0x7F, 0x85, 0x2028, 0x2029, 0xFEFF, 0xFFFE)),
        *(chr(code) for code in (0x200B, 0x202E, 0x2066, 0x10FFFF, 0x1F600)),
        "<script>",
        "%s %(x)s {0} {value}",
        "\\",
        '"',
        "é",
        "a" * 70,
    ]
    yield from pieces
    for _made in range(60):
        yield "".join(chance.choice([*pieces, "Badge", "12", "-"]) for _ in range(4))


#: Every place in a scheme a string can stand, as what to change in a valid scheme.
PLACES = {
    "key": lambda text: {"key": text},
    "label": lambda text: {"label": text},
    "a name in a language": lambda text: {"label": {"en-GB": "Badge", "pt-PT": text}},
    "a language": lambda text: {"label": {"en-GB": "Badge", text: "Crachá"}},
    "example": lambda text: {"example": text},
    "link": lambda text: {"link": "https://example.org/" + text + "/{value}"},
    "person_link": lambda text: {"person_link": "https://example.org/{value}?" + text},
    "pattern": lambda text: {"pattern": "[^ ]{1,4}" + text},
    "checksum": lambda text: {"checksum": text},
    "a subject": lambda text: {"subjects": ["person", text]},
    "a name of its own": lambda text: {text: "x"},
}


@pytest.mark.parametrize("place", PLACES)
def test_whatever_is_written_the_registry_refuses_it_or_hands_over_what_can_be_drawn(place):
    """The property the four pages rest on. For every awkward text in every place a string
    can stand, the reading either refuses the scheme, in a sentence that can itself be
    drawn, or yields a scheme whose every word encodes -- in each language it has a name in."""
    refused = accepted = 0
    for hostile in hostile_strings():
        text = json.dumps([one(**PLACES[place](hostile))])  # escaped, as a person types it
        keep(text)
        reading = identifiers.read_definitions(text)

        for problem in reading.problems:
            str(problem).encode("utf-8")
        refused += bool(reading.problems)
        for scheme in identifiers.defined_here():
            accepted += 1
            assert scheme.provider == identifiers.DEFINED_HERE
            for language in ("en-GB", "pt-PT", "fr-FR", "de"):
                with translation.override(language):
                    str(scheme.label).encode("utf-8")
            for said in (scheme.key, scheme.example, scheme.link, scheme.person_link):
                said.encode("utf-8")
            scheme.url_for("AB-1 /?#", PERSON).encode("utf-8")
            with pytest.raises(ValidationError) as told:
                identifiers.refuse_malformed(scheme, "\x00")
            " ".join(told.value.messages).encode("utf-8")
    assert refused, "something was refused, or the texts are not hostile"
    assert refused + accepted >= 80


# ------------------------------- changing a scheme refuses no identifier either (#311)
#
# A scheme can be changed as well as deleted: its pattern rewritten, or its key taken by a
# scheme Postulo starts to ship with another shape. The rows stored under it were right the
# day they were typed. Held to the scheme of today each time they were looked at, they
# refused the page they sat on, answered 422 to a client sending back what it was given,
# and were dropped by an import in silence. The rule is the one a telephone number and an
# address follow (#304, #306): **a stored row is never refused or lost by being looked
# at.** It is marked where it is shown, has no link, and answers the day it is changed.

LONGER_STAFF = STAFF | {"pattern": r"[A-Z]{3}-\d{6}", "example": "ABC-123456"}
LONGER_SIREN = SIREN | {"pattern": r"\d{14}", "link": "https://annuaire.example.org/{value}"}


@pytest.fixture
def outgrown(user, company):
    """A person's and a company's identifier, typed while their schemes took them, under
    schemes whose patterns have since been changed to something they do not fit."""
    define(STAFF, SIREN)
    person = PersonIdentifier.objects.create(
        profile=user.profile, scheme="staff-number", value="AB-123456"
    )
    firm = CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="siren", value="552081317"
    )
    define(LONGER_STAFF, LONGER_SIREN)
    return PersonIdentifier.objects.get(pk=person.pk), CompanyIdentifier.objects.get(pk=firm.pk)


def details_with(person, user, **changes) -> dict:
    """*Your details* as the page posts it with one stored identifier on it."""
    return {
        "first_name": "Alexandra",
        "last_name": "Morgan",
        "headline": "",
        "phone_0": "",
        "phone_1": "",
        "location": "",
        "website": "",
        "linkedin_url": "",
        "source_repo_url": "",
        "identifiers-TOTAL_FORMS": "1",
        "identifiers-INITIAL_FORMS": "1",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-id": str(person.pk),
        "identifiers-0-profile": str(user.profile.pk),
        "identifiers-0-scheme": person.scheme,
        "identifiers-0-value": person.value,
        "identifiers-0-label": "",
        **changes,
    }


def company_with(firm, company, **changes) -> dict:
    return {
        "name": "Aperture Science",
        "kind": company.kind,
        "identifiers-TOTAL_FORMS": "1",
        "identifiers-INITIAL_FORMS": "1",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-id": str(firm.pk),
        "identifiers-0-company": str(company.pk),
        "identifiers-0-scheme": firm.scheme,
        "identifiers-0-value": firm.value,
        "identifiers-0-label": "",
        **changes,
    }


def test_your_details_still_saves_over_an_identifier_its_scheme_has_outgrown(
    outgrown, client, user
):
    """What the review did: only the first name changed, and the page was refused."""
    person, _firm = outgrown
    client.force_login(user)

    response = client.post(reverse("accounts:profile"), details_with(person, user))

    assert response.status_code == 302, response.context["identifiers"].errors
    user.refresh_from_db()
    assert user.first_name == "Alexandra"
    kept = PersonIdentifier.objects.get(pk=person.pk)
    assert (kept.scheme, kept.value) == ("staff-number", "AB-123456"), "nothing is rewritten"


def test_a_companys_form_still_saves_over_one(outgrown, client, user, company):
    _person, firm = outgrown
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_update", args=[company.pk]), company_with(firm, company)
    )

    assert response.status_code == 302, response.context["identifiers"].errors
    company.refresh_from_db()
    assert company.name == "Aperture Science"
    kept = CompanyIdentifier.objects.get(pk=firm.pk)
    assert (kept.scheme, kept.value) == ("siren", "552081317")


def test_it_answers_to_its_scheme_the_day_it_is_changed(outgrown, client, user, company):
    """Left alone it passes; changed, it is a value somebody typed today."""
    person, firm = outgrown
    client.force_login(user)

    # Another value the scheme refuses: refused, with the example of today.
    response = client.post(
        reverse("accounts:profile"),
        details_with(person, user, **{"identifiers-0-value": "AB-999999"}),
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "That does not look like a Staff number identifier (for example ABC-123456)." in html
    assert "data-kept-as-it-was" not in html, "it has answered: its error is beside it"
    assert PersonIdentifier.objects.get(pk=person.pk).value == "AB-123456"

    # One it accepts: stored, in the scheme's own spelling.
    response = client.post(
        reverse("accounts:profile"),
        details_with(person, user, **{"identifiers-0-value": "abc-123456"}),
    )
    assert response.status_code == 302
    assert PersonIdentifier.objects.get(pk=person.pk).value == "ABC-123456"

    # And the same for a company's, and for the model asked directly.
    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        company_with(firm, company, **{"identifiers-0-value": "12345"}),
    )
    assert response.status_code == 200
    assert "That does not look like a SIREN identifier." in response.content.decode()
    firm.full_clean()
    firm.value = "1234"
    with pytest.raises(ValidationError):
        firm.full_clean()
    # Moved to another kind, it is that kind's to accept: its value is no longer stored.
    firm.value, firm.scheme = "552081317", "wikidata"
    with pytest.raises(ValidationError):
        firm.full_clean()


def test_a_row_made_and_cleaned_again_without_being_read_is_left_alone(user):
    """The row as the table holds it is asked for where the row was not read from it."""
    define(STAFF)
    row = PersonIdentifier.objects.create(
        profile=user.profile, scheme="staff-number", value="AB-123456"
    )
    define(LONGER_STAFF)

    row.full_clean()
    PersonIdentifier(pk=row.pk, profile=user.profile, scheme=row.scheme, value=row.value).clean()
    deferred = PersonIdentifier.objects.only("label").get(pk=row.pk)
    deferred.full_clean()
    with pytest.raises(ValidationError):
        PersonIdentifier(profile=user.profile, scheme="staff-number", value="AB-123456").clean()


def test_the_row_is_marked_where_it_is_edited_and_has_no_link(outgrown, client, user, company):
    """*Kept as it was*, with what the scheme expects now, as an address is marked; and no
    link, because one built from a value of another shape leads wherever it leads."""
    person, firm = outgrown
    assert person.kept_as_it_was == [
        "That does not look like a Staff number identifier (for example ABC-123456)."
    ]
    assert firm.kept_as_it_was == ["That does not look like a SIREN identifier."]
    assert person.url == "" and firm.url == ""

    client.force_login(user)
    for url, sentence in (
        (reverse("accounts:profile"), "That does not look like a Staff number identifier"),
        (reverse("jobs:company_update", args=[company.pk]), "does not look like a SIREN"),
    ):
        html = client.get(url).content.decode()
        assert html.count("data-kept-as-it-was") == 1, url
        mark = html[html.index("data-kept-as-it-was") :]
        mark = mark[: mark.index("</div>")]
        assert '<span class="badge" data-tone="amber">Kept as it was</span>' in mark
        assert "This identifier does not fit what its kind expects now." in mark
        assert "is checked the next time you change it." in mark
        assert sentence in mark
        assert 'role="alert"' not in mark, "nothing has been refused"

    shown = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    shown = shown[shown.index("data-identifiers") :]
    shown = shown[: shown.index("</dl>")]
    assert "552081317" in shown and "<a " not in shown

    # A row its scheme accepts is not marked, and is linked.
    define(STAFF, LONGER_SIREN | {"pattern": r"\d{9}"})
    assert PersonIdentifier.objects.get(pk=person.pk).kept_as_it_was == []
    assert CompanyIdentifier.objects.get(pk=firm.pk).url == (
        "https://annuaire.example.org/552081317"
    )
    for url in (reverse("accounts:profile"), reverse("jobs:company_update", args=[company.pk])):
        assert "data-kept-as-it-was" not in client.get(url).content.decode()


def test_the_api_takes_back_the_list_it_gave_and_holds_a_new_value_to_the_scheme(
    outgrown, client, user, company
):
    headers = bearer(user)
    listed = client.get(f"/api/v1/companies/{company.pk}", **headers).json()["identifiers"]
    assert listed == [{"scheme": "siren", "value": "552081317", "label": "", "url": ""}]

    for sent in (listed, [*listed, {"scheme": "wikidata", "value": "Q95"}]):
        response = client.patch(
            f"/api/v1/companies/{company.pk}",
            data=json.dumps({"identifiers": sent}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200, response.content
    assert {(row.scheme, row.value) for row in company.identifiers.all()} == {
        ("siren", "552081317"),
        ("wikidata", "Q95"),
    }

    response = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps({"identifiers": [{"scheme": "siren", "value": "999999999"}]}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 422, "another value, held to the scheme of today"
    assert company.identifiers.filter(scheme="siren", value="552081317").exists()


def test_merging_two_companies_moves_one(outgrown, user, company):
    from postulo.jobs import merging

    _person, firm = outgrown
    other = Company.objects.create(owner=user, name="Aperture Laboratories")

    merging.merge_companies(other, company)

    moved = CompanyIdentifier.objects.get(pk=firm.pk)
    assert (moved.company_id, moved.scheme, moved.value) == (other.pk, "siren", "552081317")


def test_an_archive_brings_back_as_other_what_the_scheme_here_refuses(
    outgrown, user, other_user, company
):
    """An importer drops nothing and stores nothing under a scheme it does not fit. On the
    same instance, after the patterns changed, a company's identifier used to be dropped
    with nothing said and a person's stored under a pattern it failed."""
    from postulo.core.importer import load
    from postulo.documents.models import CV, Prints

    person, _firm = outgrown
    cv = CV.objects.create(owner=user, name="Main", identifiers_choice=Prints.CHOSEN)
    cv.pinned_identifiers.set([person])

    report = load(other_user, archive_of(user))

    restored = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (restored.scheme, restored.label, restored.value) == (
        "other",
        "staff-number",
        "AB-123456",
    )
    firm = CompanyIdentifier.objects.for_user(other_user).get()
    assert (firm.scheme, firm.label, firm.value) == ("other", "siren", "552081317")
    assert restored.kept_as_it_was == [] and firm.kept_as_it_was == []
    # The report says so, for each of them.
    said = [line for line in report.skipped if "dentifier" in line]
    assert said == [
        "Identifier 'staff-number' 'AB-123456': its kind does not accept that value on "
        "this instance, so it is kept as Other, named by its key",
        "Aperture: Identifier 'siren' '552081317': its kind does not accept that value on "
        "this instance, so it is kept as Other, named by its key",
    ]
    # And the CV that printed it still prints it.
    theirs = CV.objects.for_user(other_user).get(name="Main")
    assert list(theirs.pinned_identifiers.all()) == [restored]


def test_an_archive_from_an_instance_that_shaped_the_key_otherwise(user, other_user, company):
    """Two instances, one key, two patterns: `member` is four digits there and three
    letters here. Nothing is lost, and nothing is stored under a pattern it fails."""
    from postulo.core.importer import load

    both = {"key": "member", "label": "Member", "subjects": ["person", "company"]}
    define(both | {"pattern": r"\d{4}"})
    PersonIdentifier.objects.create(profile=user.profile, scheme="member", value="1234")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="member", value="5678")
    archive = archive_of(user)
    define(both | {"pattern": "[A-Z]{3}", "upper": True})  # the other instance

    report = load(other_user, archive)

    person = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (person.scheme, person.label, person.value) == ("other", "member", "1234")
    firm = CompanyIdentifier.objects.for_user(other_user).get()
    assert (firm.scheme, firm.label, firm.value) == ("other", "member", "5678")
    assert len([line for line in report.skipped if "kept as Other" in line]) == 2


def test_an_archive_says_which_kinds_this_instance_has_not_got(user, other_user, company):
    """Restored as *Other* since the first half of this; said in the report since the review."""
    from postulo.core.importer import load

    define(STAFF, SIREN)
    PersonIdentifier.objects.create(profile=user.profile, scheme="staff-number", value="AB-123456")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="siren", value="552081317")
    PersonIdentifier.objects.create(
        profile=user.profile, scheme="orcid", value="0000-0002-1825-0097"
    )
    archive = archive_of(user)
    keep("")

    report = load(other_user, archive)

    assert [line for line in report.skipped if "dentifier" in line] == [
        "Identifier 'staff-number' 'AB-123456': this instance has no such kind, so it is "
        "kept as Other, named by its key",
        "Aperture: Identifier 'siren' '552081317': this instance has no such kind, so it "
        "is kept as Other, named by its key",
    ]
    assert PersonIdentifier.objects.filter(profile=other_user.profile, scheme="orcid").exists()


def test_a_value_an_archive_writes_another_way_is_stored_in_the_schemes_own_spelling(
    user, other_user
):
    """What typing it here would have written. A CV that had chosen it still finds it."""
    from postulo.core.importer import load
    from postulo.documents.models import CV, Prints

    define(one(pattern="[a-z]{2}-[0-9]{4}"))
    pinned = PersonIdentifier.objects.create(profile=user.profile, scheme="badge", value="ab-1234")
    cv = CV.objects.create(owner=user, name="Main", identifiers_choice=Prints.CHOSEN)
    cv.pinned_identifiers.set([pinned])
    archive = archive_of(user)
    define(one(pattern="[A-Z]{2}-[0-9]{4}", upper=True))  # the other instance

    report = load(other_user, archive)

    restored = PersonIdentifier.objects.get(profile=other_user.profile)
    assert (restored.scheme, restored.value) == ("badge", "AB-1234")
    assert restored.kept_as_it_was == []
    assert not [line for line in report.skipped if "dentifier" in line]
    assert list(CV.objects.for_user(other_user).get(name="Main").pinned_identifiers.all()) == [
        restored
    ]


def candidate_plan(person, rows: list[dict]):
    from postulo.core import export
    from postulo.resume import candidate

    document = {
        "postulo": {
            "candidate_format": export.CANDIDATE_FORMAT,
            "version": "0.5.0",
            "exported_at": "2026-10-02T10:00:00+00:00",
        },
        "account": {"identifiers": rows},
    }
    held = candidate.read(json.dumps(document).encode())
    plan = candidate.plan(person, held)
    candidate.apply(person, held)
    return [
        row for section in plan.sections if section.key == "identifiers" for row in section.rows
    ]


def test_a_candidate_file_does_what_an_archive_does_with_a_kind_this_instance_lacks(user):
    """It used to show the row as refused, where an archive restored it as *Other*. A file
    is another instance's, and so are its kinds: the identifier is added as *Other*, named
    by the key, and the row says so before anything is added."""
    from postulo.resume import candidate

    define(LONGER_STAFF)
    drawn = candidate_plan(
        user,
        [
            {"scheme": "library-card", "value": "LC-77"},
            {"scheme": "staff-number", "value": "AB-123456"},
            {"scheme": "guild", "value": "G-1", "label": "Guild of Clerks"},
            {"scheme": "", "value": "nothing to go by"},
        ],
    )

    assert [row.outcome for row in drawn] == [
        candidate.ADD,
        candidate.ADD,
        candidate.ADD,
        candidate.REFUSED,
    ]
    assert drawn[0].notes == [
        "This instance has no kind of identifier called “library-card”, so it is added as "
        "Other, named “library-card”."
    ]
    assert drawn[2].notes == [
        "This instance has no kind of identifier called “guild”, so it is added as Other, "
        "named “Guild of Clerks”."
    ]
    assert drawn[1].notes == [
        "This is not a value Staff number accepts here, so it is added as Other, named "
        "“staff-number”."
    ]
    held = sorted((row.scheme, row.label, row.value) for row in user.profile.identifiers.all())
    assert held == [
        ("other", "Guild of Clerks", "G-1"),
        ("other", "library-card", "LC-77"),
        ("other", "staff-number", "AB-123456"),
    ]


def with_shipped(monkeypatch, *schemes) -> None:
    """Postulo, or a plugin, ships these as well from now on."""
    shipped = identifiers.shipped
    monkeypatch.setattr(
        identifiers, "shipped", lambda: shipped() | {scheme.key: scheme for scheme in schemes}
    )


def test_a_key_postulo_starts_to_ship_takes_nothing_from_the_rows_under_it(
    user, company, client, admin, monkeypatch
):
    """The instance defined `vat` for a person and a company. Then Postulo ships a `vat`,
    for companies, in another shape. The scheme is Postulo's from that moment; the rows are
    still their owners'."""
    ours = {"key": "vat", "label": "VAT (ours)", "subjects": ["person", "company"]}
    define(ours | {"pattern": r"\d{9}"})
    person = PersonIdentifier.objects.create(profile=user.profile, scheme="vat", value="123456789")
    firm = CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="vat", value="987654321"
    )
    shipped = identifiers.Scheme(
        "vat",
        "VAT number",
        re.compile(r"[A-Z]{2}[0-9]{8,12}"),
        subjects=frozenset({identifiers.COMPANY}),
        link="https://vat.example.org/{value}",
        example="PT123456789",
    )
    with_shipped(monkeypatch, shipped)
    site.forget_current()

    # Postulo's own wins, and what the instance wrote under that key defines nothing.
    assert identifiers.find("vat") is shipped
    assert identifiers.find("vat", PERSON) is None

    person = PersonIdentifier.objects.get(pk=person.pk)
    firm = CompanyIdentifier.objects.get(pk=firm.pk)
    # The person's is under a scheme that no longer identifies a person: kept, by its key.
    assert person.scheme_label == "vat" and person.url == ""
    # The company's is under a scheme that would refuse it: kept, marked, not linked.
    assert firm.scheme_label == "VAT number" and firm.url == ""
    assert firm.kept_as_it_was == [
        "That does not look like a VAT number identifier (for example PT123456789)."
    ]

    client.force_login(user)
    response = client.post(reverse("accounts:profile"), details_with(person, user))
    assert response.status_code == 302, response.context["identifiers"].errors
    response = client.post(
        reverse("jobs:company_update", args=[company.pk]), company_with(firm, company)
    )
    assert response.status_code == 302, response.context["identifiers"].errors
    assert PersonIdentifier.objects.get(pk=person.pk).value == "123456789"
    assert CompanyIdentifier.objects.get(pk=firm.pk).value == "987654321"

    # And the administrator is told, on the page, without having to try to save.
    client.force_login(admin)
    html = client.get(reverse("server:identifier_schemes")).content.decode()
    assert "Line 3, scheme “vat”: this key is one of Postulo" in html
    assert "data-about-what-is-kept" in html


def test_a_scheme_handed_over_under_a_key_postulo_ships_loses_in_the_registry(monkeypatch):
    """The line that keeps Postulo's own, reached. The reader refuses such a key, so a text
    never gets a scheme this far; a reading that did not refuse it -- another plugin's, a
    mistake in this one -- hands it over, and the registry keeps the one Postulo ships."""
    impostor = identifiers.Scheme(
        "orcid", "Not ORCID", re.compile("x"), provider=identifiers.DEFINED_HERE
    )
    fine = identifiers.Scheme("fine", "Fine", re.compile("x"), provider=identifiers.DEFINED_HERE)
    monkeypatch.setattr(
        identifiers, "_reader", lambda text, taken: identifiers.Reading(schemes=(impostor, fine))
    )
    keep("whatever the text is")

    assert [scheme.key for scheme in identifiers.defined_here()] == ["orcid", "fine"]
    found = identifiers.registry()
    assert found["orcid"] is SCHEMES["orcid"] and found["orcid"].label == "ORCID"
    assert found["fine"] is fine
    assert list(found) == [*identifiers.shipped(), "fine"]


# ------------------------------------------------- the task worker sees a change (#311)


def test_each_errand_reads_the_instances_settings_afresh(user):
    """The worker has no request, and nothing forgot the policy row for it: it read the row
    once in its life. It renders CVs, and it went on printing the name a scheme had when
    the worker started into copies that are kept. An errand begins as a request does."""
    from postulo.core import errands
    from postulo.core.models import Errand

    seen: list[str] = []
    errands.HANDLERS["labels_test"] = errands.Handler(
        kind="labels_test",
        working="Reading a label",
        run=lambda errand: seen.append(identifiers.label_for("staff-number")) or {},
    )
    try:
        define(STAFF)
        site.forget_current()
        errands.perform(Errand.objects.create(kind="labels_test", owner=user).pk)

        # Another process saves the page: nothing in this one is told.
        renamed = written(STAFF | {"label": "Payroll number"})
        SiteSettings.objects.filter(pk=1).update(identifier_schemes=renamed)
        errands.perform(Errand.objects.create(kind="labels_test", owner=user).pk)

        SiteSettings.objects.filter(pk=1).update(identifier_schemes="")
        errands.perform(Errand.objects.create(kind="labels_test", owner=user).pk)
    finally:
        del errands.HANDLERS["labels_test"]

    assert seen == ["Staff number", "Payroll number", "staff-number"]


def test_each_errand_asks_again_which_plugins_are_on(user):
    """The other memo a request forgets at its start, forgotten here too."""
    from postulo.core import errands, memo
    from postulo.core.models import Errand

    seen: list[bool] = []
    errands.HANDLERS["memo_test"] = errands.Handler(
        kind="memo_test",
        working="Looking",
        run=lambda errand: seen.append(hasattr(memo.decisions, "answers")) or {},
    )
    try:
        memo.decisions.answers = {"left": "over from the errand before"}
        errands.perform(Errand.objects.create(kind="memo_test", owner=user).pk)
    finally:
        del errands.HANDLERS["memo_test"]
        memo.forget_decisions()

    assert seen == [False]


# ------------------------------------ what the page will not take for an empty box (#311)


def test_a_post_without_the_box_changes_nothing_and_says_so(client, admin):
    """A browser sends an empty box as an empty text. A request with no box at all used to
    read the same, and emptied the definitions."""
    define(STAFF)
    client.force_login(admin)
    url = reverse("server:identifier_schemes")

    for sent in ({}, {"something_else": "x"}):
        response = client.post(url, sent)
        assert response.status_code == 200
        assert "The schemes were not sent with this request, so nothing was changed." in (
            response.content.decode()
        )
    response = client.post(url, {}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200 and "were not sent" in response.content.decode()

    assert SiteSettings.objects.get(pk=1).identifier_schemes == written(STAFF)
    assert identifiers.find("staff-number") is not None


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_the_page_is_asked_and_posted_to_and_nothing_else(client, admin, method):
    """An `UpdateView` takes a PUT for a POST; a PUT carries no form, and emptied the
    definitions with a 302."""
    define(STAFF)
    client.force_login(admin)

    response = getattr(client, method)(reverse("server:identifier_schemes"))

    assert response.status_code == 405
    assert SiteSettings.objects.get(pk=1).identifier_schemes == written(STAFF)
    assert client.head(reverse("server:identifier_schemes")).status_code == 200


# ------------------------------------------------ a value that is a step of a path (#311)


@pytest.mark.parametrize("value", [".", ".."])
def test_a_value_that_is_a_step_of_the_path_has_no_link(value, client, user, company):
    """Percent-encoding does nothing about a full stop. `..` where the template has the
    value is an address a browser reads as the page above: the same host, and not the page
    the template names. Such a value has no link."""
    from urllib.parse import urljoin

    define(
        one(
            pattern="[^ ]{1,40}",
            subjects=["person", "company"],
            link="https://example.org/staff/{value}/card",
        )
    )
    scheme = identifiers.find("badge")

    assert scheme.url_for(value, PERSON) == ""
    # Where a browser would have gone with it: not to the page the template names.
    assert urljoin("https://example.org/", f"/staff/{value}/card") in (
        "https://example.org/staff/card",
        "https://example.org/card",
    )

    # And through the page: the value is shown, and is not a link.
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="badge", value=value)
    client.force_login(user)
    shown = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    shown = shown[shown.index("data-identifiers") :]
    shown = shown[: shown.index("</dl>")]
    assert "<a " not in shown and "example.org" not in shown


@pytest.mark.parametrize(
    ("template", "value", "linked"),
    [
        # Made a step by what the template has beside it.
        ("https://example.org/x/.{value}", ".", False),
        ("https://example.org/x/{value}{value}/y", ".", False),
        ("https://example.org/x/{value}", "..", False),
        # A full stop that is not the whole step, and one where no step is: a value.
        ("https://example.org/x/{value}", "...", True),
        ("https://example.org/x/{value}", "a.b", True),
        ("https://example.org/x/v{value}", "..", True),
        ("https://example.org/x?id={value}", "..", True),
        ("https://example.org/x#{value}", ".", True),
        ("https://example.org/x/{value}?also={value}", "..", False),
    ],
)
def test_only_a_whole_step_of_the_path_is_one(template, value, linked):
    define(one(pattern="[^ ]{1,40}", link=template))
    url = identifiers.find("badge").url_for(value, PERSON)
    assert bool(url) is linked
    if linked:
        assert url == template.replace("{value}", value)


# -------------------------------------- what is kept is read where it is shown (#311)

BROKEN_AND_FINE = [one(key="slow", pattern="(a+)+"), SIREN]


def test_the_page_says_what_is_wrong_with_the_text_as_it_is_kept(client, admin, caplog):
    """A text that reached the row past the page -- a restored backup -- is used in part:
    the schemes that break a rule are left out. The page showed it as though all of it
    were in force, and only the log said otherwise."""
    keep(written(*BROKEN_AND_FINE))
    client.force_login(admin)

    response = client.get(reverse("server:identifier_schemes"))

    assert response.status_code == 200
    html = response.content.decode()
    said = html[html.index('id="id_identifier_schemes_error"') :]
    said = said[: said.index("</div>")]
    assert "data-about-what-is-kept" in said
    assert "This is wrong in the text as it is kept." in said
    assert "a scheme named here is not in use" in said
    assert "Line 8, scheme “slow”: the pattern cannot be used, at character 3." in said
    box = re.search(r"<textarea[^>]*>", html)[0]
    assert 'aria-invalid="true"' in box and 'data-caret-line="8"' in box
    assert "id_identifier_schemes_error" in re.search(r'aria-describedby="([^"]*)"', box)[1]
    assert "autofocus" not in box, "nothing was refused: nobody is sent anywhere"
    # The sentences stand before the box they are about.
    assert html.index('id="id_identifier_schemes_error"') < html.index("<textarea")
    # And the rest of the text is in force, as it was.
    assert identifiers.find("siren") is not None and identifiers.find("slow") is None

    # In the dialog on Plugins too, before it is opened.
    html = client.get(reverse("server:plugins")).content.decode()
    dialog = html[html.index('<dialog popover id="settings-identifiers"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    assert "This is wrong in the text as it is kept." in dialog
    assert "Line 8, scheme “slow”: the pattern cannot be used" in dialog


def test_a_refused_save_does_not_say_it_is_about_what_is_kept(client, admin):
    keep(written(*BROKEN_AND_FINE))
    client.force_login(admin)

    response = client.post(
        reverse("server:identifier_schemes"), {"identifier_schemes": written(one(pattern="a+"))}
    )

    html = response.content.decode()
    assert "the pattern cannot be used" in html
    assert "data-about-what-is-kept" not in html and "as it is kept" not in html
    assert "autofocus" in re.search(r"<textarea[^>]*>", html)[0]


def test_showing_what_is_kept_reads_nothing_twice(client, admin, monkeypatch):
    """Reading a text is preparing every pattern in it. The page that shows what is wrong
    with the kept text uses the reading the registry made of it."""
    keep(written(*BROKEN_AND_FINE))
    readings = []
    read = identifiers.read_definitions
    monkeypatch.setattr(
        identifiers, "read_definitions", lambda text: readings.append(text) or read(text)
    )
    monkeypatch.setattr(identifiers, "_read", None)
    client.force_login(admin)

    for name in ("server:identifier_schemes", "server:plugins", "server:identifier_schemes"):
        assert client.get(reverse(name)).status_code == 200

    assert len(readings) == 1


def test_what_is_wrong_is_said_above_the_box_and_tied_to_it(client, admin):
    """After a refusal the sentences are the first thing under the label, before a box
    that may be a hundred lines long, in the region the box is described by."""
    client.force_login(admin)
    for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
        html = client.post(
            reverse("server:identifier_schemes"),
            {"identifier_schemes": written(one(pattern="a+"))},
            **headers,
        ).content.decode()
        label = html.index('<label for="id_identifier_schemes"')
        said = html.index('id="id_identifier_schemes_error" role="alert"')
        box = html.index("<textarea")
        helped = html.index('id="id_identifier_schemes_helptext"')
        assert label < said < box < helped
        described = re.search(r'aria-describedby="([^"]*)"', html[box:])[1].split()
        assert described == ["id_identifier_schemes_helptext", "id_identifier_schemes_error"]


def test_the_box_is_a_box_of_code_set_left_to_right(client, admin):
    """Its direction is its own -- JSON reads one way in any language -- and it is a box
    with a height of its own that scrolls inside itself, not one that grows with its text."""
    client.force_login(admin)
    html = client.get(reverse("server:identifier_schemes")).content.decode()
    box = re.search(r"<textarea[^>]*>", html)[0]
    assert 'dir="ltr"' in box and "data-code" in box
    assert 'spellcheck="false"' in box
    pre = re.search(r"<pre[^>]*>", html[html.index("One scheme, written out") :])[0]
    assert 'dir="ltr"' in pre

    from pathlib import Path

    import postulo

    sheet = (Path(postulo.__file__).parent / "static/css/app.css").read_text(encoding="utf-8")
    rule = sheet[sheet.index(".field > textarea[data-code]") :]
    rule = rule[: rule.index("}")]
    assert "field-sizing: fixed" in rule and "overflow: auto" in rule and "height:" in rule


# ----------------------------------------------- the companies table and the API (#311)


def test_the_companies_table_asks_for_postulos_own_schemes_and_no_others(client, user, company):
    """A scheme an instance defined has no column: the columns are settled when the module
    is imported. What can go wrong is the query asking for a column the table has not got,
    or the page failing over a company that holds such an identifier."""
    from postulo.jobs.tables import CompaniesTable

    define(SIREN)
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="siren", value="552081317")

    asked = {name for name in Company.objects.with_table_data().query.annotations if "id_" in name}
    columns = {column.key for column in CompaniesTable.columns if column.key.startswith("id_")}
    assert asked == columns == {f"id_{key}" for key in company_identifiers.shipped()} - {"id_other"}
    assert "id_siren" not in asked
    client.force_login(user)
    assert client.get(reverse("jobs:company_list")).status_code == 200


def test_the_api_says_a_pasted_address_is_for_postulos_own_schemes(client, user):
    """It said a pasted address is accepted, of every scheme. An instance's own takes the
    value alone, and the description says which is which."""
    from postulo.api.api import api

    described = api.get_openapi_schema()["components"]["schemas"]["IdentifierIn"]["properties"]
    assert (
        "For one of Postulo's own schemes a pasted address is accepted"
        in (described["value"]["description"])
    )
    assert "takes the value alone" in described["value"]["description"]

    define(SIREN | {"link": "https://annuaire.example.org/{value}"})
    headers = bearer(user)
    pasted = client.post(
        "/api/v1/companies",
        data=json.dumps(
            {
                "name": "Aperture",
                "identifiers": [
                    {"scheme": "siren", "value": "https://annuaire.example.org/552081317"}
                ],
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert pasted.status_code == 422
