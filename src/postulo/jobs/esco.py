"""Where the names of jobs and skills come from, and why it is a standard rather than a list.

A job title is free text everywhere in Postulo, and so is a skill. That is correct for
what a person types and wrong for what the application can then do with it: *Software
Engineer*, *Ingénieur logiciel* and *Engenheiro de software* are one job and three
strings, and nothing in the code can tell (#266).

**So the list is ESCO**, the European Commission's classification of skills,
competences, qualifications and occupations — to occupations and skills what NACE is to
industries. The argument is the same one ``industries.py`` wrote down for NACE: a
hand-made list of a few hundred names would be those names in every language Postulo
speaks, carried by this project for ever. ESCO is maintained and published translated by
the body that keeps it, and EURES and the national employment services already speak it,
so a source plugin reading one of them (#241) has somewhere to put what it reads. The
translations are the real argument, more than the taxonomy.

**The level a person picks is the ISCO-08 unit group.** ESCO's 3,039 occupations sit at
level 5 of the ISCO-08 hierarchy and each is mapped to exactly one four-digit unit group;
the 436 of them are the level where a name is still a job somebody recognises, as NACE's
divisions are for industries. The occupations stay in the file because that is where the
matching happens: a title typed in French is found among the occupation names, and the
unit group it belongs to is the code that is kept. The code is a unit group rather than
an occupation because a report to an employment office that thinks in ISCO-08 reads a
four-digit code, and because the person's own wording stays in the title beside it.

**It is a seed, and never a closed list**, the way industries are. A title that matches
nothing has no code, and that is not a lesser kind of job. The free text stays where it
is, everywhere, and the code follows the name rather than being set beside it, so there
is one invariant and no way for the two to disagree.

**It ships in the repository and is never fetched at runtime.** At this level the
classification is 3,475 concepts; the names in the 28 languages it is published in are a
few megabytes, less than the catalogues this repository already carries. Postulo runs on
a person's own hardware, sometimes none of it on any network, and a vocabulary that
needs a fetch is not a vocabulary. The harvest is a deliberate act, done once and
recorded in the issue, and there is no step in this project that reaches for the
internet to ask what a word means.

**The terms are EUPL 1.2**, as the ESCO services publish them; ``data/ESCO-LICENCE.md``
says on whose terms, with the attribution and the changes, the way NACE's does.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.utils.translation import get_language

DATA = Path(__file__).resolve().parent / "data" / "esco-1.2.1.json"

#: The language a name is read in when ESCO publishes nothing in the reader's.
FALLBACK = "en"


@lru_cache(maxsize=1)
def classification() -> dict:
    """The whole file: the revision, the languages, the unit groups, the occupations."""
    return json.loads(DATA.read_text(encoding="utf-8"))


def revision() -> str:
    """Which version of ESCO the file holds, so a replacement names what it replaces."""
    return classification()["revision"]


def languages() -> list[str]:
    """The languages the classification itself is published in."""
    return list(classification()["languages"])


def _reading(language: str = "") -> str:
    """Which of the classification's languages to read, for whoever is reading Postulo.

    ``pt-br`` takes the Portuguese names and ``en-gb`` the English ones: the base language
    is what a classification is published in, and a variant of it is the same words. A
    language ESCO does not publish reads the English names, rather than a translation
    this project would have to keep itself.
    """
    code = (language or get_language() or FALLBACK).lower().replace("_", "-")
    known = set(languages())
    if code in known:
        return code
    base = code.partition("-")[0]
    return base if base in known else FALLBACK


def unit_groups(language: str = "") -> list[tuple[str, str]]:
    """Every ISCO-08 unit group as ``(code, name)``, in code order, in the reader's language."""
    reading = _reading(language)
    return [
        (code, entry["names"].get(reading) or entry["names"].get(FALLBACK, code))
        for code, entry in classification()["unit_groups"].items()
    ]


def name_for(code: str, language: str = "") -> str:
    """What a unit group is called, or empty where nothing here has that code.

    Empty rather than the code, because a code that has gone from a later revision belongs
    to a person's record that keeps its name — it is not something to print at them.
    """
    entry = classification()["unit_groups"].get(code)
    if entry is None:
        return ""
    reading = _reading(language)
    return entry["names"].get(reading) or entry["names"].get(FALLBACK, "")


def major_of(code: str) -> str:
    """The ISCO-08 major group a unit group belongs to, the level-1 ancestor."""
    entry = classification()["unit_groups"].get(code)
    return entry["major"] if entry else ""


@lru_cache(maxsize=32)
def _occupations_by_name(reading: str) -> dict[str, str]:
    """``{folded occupation name: unit group}`` for one language, so a typed title can find
    the code of the unit group it belongs to.

    The value is the unit group rather than the occupation, because that is the level a
    report can say and a picker can offer. Two occupations that share a name in one
    language point at the same group, which is the only answer a four-digit code can give.
    """
    return {
        (entry["names"].get(reading) or entry["names"].get(FALLBACK, "")).casefold(): entry["isco"]
        for entry in classification()["occupations"].values()
    }


@lru_cache(maxsize=32)
def _unit_groups_by_name(reading: str) -> dict[str, str]:
    """``{folded unit group name: code}`` for one language, the level the picker offers."""
    return {
        (entry["names"].get(reading) or entry["names"].get(FALLBACK, "")).casefold(): code
        for code, entry in classification()["unit_groups"].items()
    }


def code_for(name: str, language: str = "") -> str:
    """The unit-group code a typed job title matches, in the reader's language or in English.

    An occupation's name is tried first and a unit group's second, because the more
    specific the match, the more the standard did the work. Both are tried in the reader's
    language and then in English, because somebody reading Postulo in Portuguese may paste
    an English title out of a form they were sent. A name that matches nothing has no
    code, which is the ordinary case and not a failure.
    """
    folded = (name or "").strip().casefold()
    if not folded:
        return ""
    reading = _reading(language)
    if reading != FALLBACK:
        matched = _occupations_by_name(reading).get(folded) or _unit_groups_by_name(reading).get(
            folded
        )
        if matched:
            return matched
    return _occupations_by_name(FALLBACK).get(folded) or _unit_groups_by_name(FALLBACK).get(
        folded, ""
    )


def suggestions(exclude=(), language: str = "") -> list[str]:
    """What a picker offers, minus anything the person already has.

    The unit groups in code order: the level a person recognises, which is why the
    occupations are not offered — a picker is not a directory, and the occupations are
    there to be matched, not chosen.
    """
    taken = {str(name).casefold() for name in exclude}
    return [name for _code, name in unit_groups(language) if name.casefold() not in taken]
