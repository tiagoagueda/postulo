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

**It is downloaded in place, and never fetched at runtime.** At this level the
classification is 3,475 concepts; the names in the 28 languages it is published in are a
few megabytes, and a repository is not where reference data that size is kept. So
``manage.py fetch_esco`` downloads it into ``data/`` once, at provisioning, and there is
still no step in a request that reaches for the internet to ask what a word means.
Without the file Postulo runs on: a title matches no code and the title box offers
nothing, which is the same state as a title that matches nothing, and not a failure.
The revision is recorded inside the file, so whatever ``esco-*.json`` is in the
directory is what the loader reads.

**The terms are EUPL 1.2**, as the ESCO services publish them; ``data/ESCO-LICENCE.md``
says on whose terms, with the attribution and the changes, the way NACE's does.

**Skills are the other half, and they keep ESCO's own identifier** (#266). A skill has no
code like ISCO's, so what a `resume.Skill` keeps beside its name is the URI the
classification gives the skill its name matches. The same rules as a title: the free
text stays, a name that matches nothing is not a lesser kind of skill, and the identifier
follows the name rather than being set beside it. A name matches a skill when it *is* that
skill's preferred name in some language, folded for case and spacing -- which is what lets
a CV in another language print the classification's name for it without printing
something the person did not say. Alternative names are not kept: the API hands them over
only with every description in every language, 1.5 MB for a hundred skills, and what a
person types that differs from the preferred name is offered the preferred name as they
type rather than guessed at afterwards.

**The skills are a file of their own, and never in memory all at once.** 13,939 skills in
28 languages are about fifteen megabytes of names, and parsed as one JSON document they
would put every language in memory to answer one reader. So ``fetch_esco`` writes
``esco-skills-<revision>.zip`` beside the occupations: a zip rather than a JSON file, whose
name the occupations' guard therefore does not match, with a guard of its own refusing two.
Inside it are the identifiers, one per line and sorted, and one member per language whose
line *n* is the name of identifier *n*, empty where the classification publishes none. A
language is read when somebody first needs it -- the reader's, the record's, the one a CV
is in -- and at most `SKILL_LANGUAGES_HELD` of them are kept at once. Without the file a
skill matches nothing and the skill box offers nothing, which is the ordinary state.
"""

from __future__ import annotations

import bisect
import json
import logging
import unicodedata
import zipfile
from array import array
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from postulo.core import languages as tags

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"

#: The language a name is read in when ESCO publishes nothing in the reader's.
FALLBACK = "en"

#: What the classification looks like where it has not been downloaded: the same shape,
#: nothing in it. A title matches no code and the title box offers nothing, which is the
#: ordinary state of a title that matches nothing, and not an error.
ABSENT = {
    "revision": "",
    "source": "https://esco.ec.europa.eu",
    "publisher": (
        "European Commission, Directorate-General for Employment, Social Affairs and Inclusion"
    ),
    "licence": "EUPL 1.2",
    "languages": [],
    "unit_groups": {},
    "occupations": {},
}

#: Once is enough to say the file is not there; the absence is not an error to repeat.
_warned = False


def data_file() -> Path | None:
    """The classification in the data directory, or None where it has not been downloaded.

    One revision at a time: ``manage.py fetch_esco`` writes ``esco-<revision>.json``, and
    a replacement is not finished until the old file is deleted, so two files are a state
    to be reported, not a choice to be made.
    """
    files = sorted(DATA_DIR.glob("esco-*.json"))
    if len(files) > 1:
        names = ", ".join(file.name for file in files)
        raise RuntimeError(
            f"two ESCO classifications in {DATA_DIR} ({names}); delete the one being "
            "replaced before starting Postulo"
        )
    return files[0] if files else None


@lru_cache(maxsize=1)
def classification() -> dict:
    """The whole file: the revision, the languages, the unit groups, the occupations.

    The empty document where the file has not been downloaded; ``manage.py fetch_esco``
    is how it gets there.
    """
    path = data_file()
    if path is None:
        global _warned
        if not _warned:
            _warned = True
            logger.warning(
                "The ESCO classification has not been downloaded, so no title matches a "
                "code and the title box offers no unit groups. Run 'manage.py fetch_esco' "
                "to download it in place."
            )
        return ABSENT
    return json.loads(path.read_text(encoding="utf-8"))


def revision() -> str:
    """Which version of ESCO the file holds, so a replacement names what it replaces."""
    return classification()["revision"]


def languages() -> list[str]:
    """The languages the classification itself is published in."""
    return list(classification()["languages"])


def _reading(language: str = "") -> str:
    """Which of the classification's languages to read, for whoever is reading Postulo.

    ``pt-BR`` takes the Portuguese names and ``en-GB`` the English ones: the base language
    is what a classification is published in, and a variant of it is the same words. A
    language ESCO does not publish reads the English names, rather than a translation
    this project would have to keep itself.
    """
    return tags.match(language or tags.current(), languages()) or FALLBACK


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


# ------------------------------------------------------------------------------ skills

#: The classification's identifier for a skill is this and a tail; the file keeps the tail.
SKILL_NAMESPACE = "http://data.europa.eu/esco/skill/"

#: The members of ``esco-skills-<revision>.zip``: what the file says about itself, the
#: identifiers, and a member of names per language.
SKILLS_ABOUT = "about.json"
SKILLS_IDENTIFIERS = "identifiers.txt"
SKILLS_NAMES = "names/{language}.txt"

#: How many languages of names one process keeps at once. A page asks for one or two -- the
#: reader's, the record's, English, the language of the CV being drawn -- and each is 1.3 to
#: 1.6 MB as Python holds it, with its sorted order (measured on v1.2.1); the twenty-eight
#: together would be forty megabytes held to serve one reader.
SKILL_LANGUAGES_HELD = 4

#: How many names the skill box is offered for what has been typed so far, and how much has
#: to have been typed first. Two letters is where a prefix stops being most of an alphabet.
SUGGESTED_SKILLS = 10
SHORTEST_PREFIX = 2

#: What the skills look like where they have not been downloaded: the same shape, nothing
#: in it. A skill matches nothing and nothing is offered, which is not an error.
ABSENT_SKILLS = {
    "revision": "",
    "source": ABSENT["source"],
    "publisher": ABSENT["publisher"],
    "licence": ABSENT["licence"],
    "languages": [],
    "skills": 0,
}

#: Once is enough to say the skills are not there, as for the occupations.
_warned_skills = False


def skills_file() -> Path | None:
    """The skills in the data directory, or None where they have not been downloaded.

    One revision at a time, for the reason `data_file` gives. ``esco-*.json`` does not match
    this file's name, so the occupations' guard stays what it was, and this is the same
    guard for the skills.
    """
    files = sorted(DATA_DIR.glob("esco-skills-*.zip"))
    if len(files) > 1:
        names = ", ".join(file.name for file in files)
        raise RuntimeError(
            f"two ESCO skills files in {DATA_DIR} ({names}); delete the one being replaced "
            "before starting Postulo"
        )
    return files[0] if files else None


def _member(name: str) -> str:
    """One member of the skills file, as text; empty where the file or the member is not."""
    path = skills_file()
    if path is None:
        return ""
    with zipfile.ZipFile(path) as archive:
        try:
            return archive.read(name).decode("utf-8")
        except KeyError:
            return ""


@lru_cache(maxsize=1)
def skills_about() -> dict:
    """What the skills file says about itself: the revision, the languages, how many.

    Only this member is read here, a few hundred bytes. The names are read a language at a
    time, and only when somebody asks for that language.
    """
    if skills_file() is None:
        global _warned_skills
        if not _warned_skills:
            _warned_skills = True
            logger.warning(
                "The ESCO skills have not been downloaded, so no skill matches one and the "
                "skill box offers nothing. Run 'manage.py fetch_esco' to download them in "
                "place."
            )
        return ABSENT_SKILLS
    return json.loads(_member(SKILLS_ABOUT) or "null") or ABSENT_SKILLS


def skills_available() -> bool:
    """Whether there is anything to match a skill's name against."""
    return bool(skills_about()["languages"])


def _fold(text) -> str:
    """A name as it is compared: one way of composing a letter, one spacing, one case."""
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


@lru_cache(maxsize=1)
def _skill_identifiers() -> tuple[str, ...]:
    """Every skill's identifier, less `SKILL_NAMESPACE`, in the order the file keeps them.

    Sorted, which is what lets an identifier be found by halving rather than by an index
    the size of the classification; a file that is not sorted is not one `fetch_esco` wrote.
    """
    if not skills_available():
        return ()
    identifiers = tuple(_member(SKILLS_IDENTIFIERS).split("\n"))
    if any(later <= earlier for earlier, later in pairwise(identifiers)):
        raise RuntimeError(f"{skills_file()} is not a file 'manage.py fetch_esco' wrote")
    return identifiers


def _skill_language(code: str) -> str:
    """Which of the skills' languages ``code`` reads, or empty where it reads none.

    ``pt-BR`` reads the Portuguese names and ``en-GB`` the English ones, as for occupations.
    Unlike `_reading` there is no English here for a language the classification does not
    publish: a caller that wants English asks for it, because English standing in for a
    language is one thing in a suggestion and another on a CV.
    """
    return tags.match(code, skills_about()["languages"])


@lru_cache(maxsize=SKILL_LANGUAGES_HELD)
def _skill_names(language: str) -> tuple[str, ...]:
    """Every skill's name in one language, line for line with `_skill_identifiers`."""
    identifiers = _skill_identifiers()
    if not identifiers or language not in skills_about()["languages"]:
        return ()
    names = tuple(_member(SKILLS_NAMES.format(language=language)).split("\n"))
    if len(names) != len(identifiers):
        raise RuntimeError(f"{skills_file()} is not a file 'manage.py fetch_esco' wrote")
    return names


@lru_cache(maxsize=SKILL_LANGUAGES_HELD)
def _skill_order(language: str) -> array:
    """Where each named skill sits when one language's names are sorted, folded.

    Positions rather than the names themselves, as four bytes each: the names are already
    held by `_skill_names`, and a second copy of them folded would double what a language
    costs to answer a question asked a letter at a time.
    """
    names = _skill_names(language)
    named = (position for position, name in enumerate(names) if name)
    return array("I", sorted(named, key=lambda position: _fold(names[position])))


def _named_from(language: str, folded: str) -> tuple[tuple[str, ...], array, int]:
    """One language's names, its order, and where ``folded`` would sit in that order."""
    names = _skill_names(language)
    order = _skill_order(language)
    start = bisect.bisect_left(order, folded, key=lambda position: _fold(names[position]))
    return names, order, start


def _position_named(folded: str, language: str) -> int | None:
    """The skill whose name in ``language`` is ``folded``, where exactly one has it."""
    names, order, index = _named_from(language, folded)
    found: set[int] = set()
    while index < len(order) and _fold(names[order[index]]) == folded:
        found.add(order[index])
        index += 1
    # Two skills that share a name in one language: the name says which of them nobody
    # can tell, and an identifier chosen between them would be a guess kept as a fact.
    return found.pop() if len(found) == 1 else None


def _skill_position(uri: str) -> int | None:
    """Where the skill ``uri`` names is in the file, or None where nothing here has it."""
    if not isinstance(uri, str) or not uri.startswith(SKILL_NAMESPACE):
        return None
    tail = uri[len(SKILL_NAMESPACE) :]
    identifiers = _skill_identifiers()
    index = bisect.bisect_left(identifiers, tail)
    return index if index < len(identifiers) and identifiers[index] == tail else None


def skill_for(name: str, *languages: str) -> str:
    """The URI of the ESCO skill a typed name is, or empty where it is none of them.

    The name is tried in each of ``languages`` in turn -- a career record's own language,
    then the reader's -- and then in English, which is where a name pasted out of a form
    somebody was sent usually comes from. A name matches when it is a skill's preferred
    name, whatever the case and the spacing; anything else has no identifier, which is the
    ordinary case and not a failure.
    """
    folded = _fold(name)
    if not folded or not skills_available():
        return ""
    tried: list[str] = []
    for code in (*languages, FALLBACK):
        language = _skill_language(code)
        if not language or language in tried:
            continue
        tried.append(language)
        position = _position_named(folded, language)
        if position is not None:
            return SKILL_NAMESPACE + _skill_identifiers()[position]
    return ""


def skill_name(uri: str, language: str = "", *, strict: bool = True) -> str:
    """What the classification calls a skill in ``language``, or empty.

    Empty where nothing here has that skill -- a later revision may have retired it, and a
    person's own name for it is what they keep -- and, when ``strict``, where the
    classification publishes no name in that language. Not strict is for somebody reading
    Postulo, who may be shown the English; strict is for a document, where the English
    name standing in for a language would be a line in a language nobody chose.
    """
    position = _skill_position(uri)
    if position is None:
        return ""
    reading = _skill_language(language or tags.current())
    names = _skill_names(reading) if reading else ()
    name = names[position] if position < len(names) else ""
    if not name and not strict:
        names = _skill_names(FALLBACK)
        name = names[position] if position < len(names) else ""
    return name


def skill_suggestions(typed: str, *languages: str, limit: int = SUGGESTED_SKILLS) -> list[str]:
    """The skills' names that begin with what was typed, a few at a time.

    In the first of ``languages`` -- the reader's, which reads the English names where the
    classification does not publish that language -- and then in the rest, the career
    record's among them, each only where the classification publishes it. In the order the
    names sort, and never more than ``limit``: the box is for choosing one, and a longer
    prefix is how somebody narrows it.
    """
    prefix = _fold(typed)
    if len(prefix) < SHORTEST_PREFIX or not skills_available():
        return []
    readings: list[str] = []
    for index, code in enumerate(languages or (tags.current(),)):
        reading = _skill_language(code) or (FALLBACK if index == 0 else "")
        if reading and reading not in readings:
            readings.append(reading)
    offered: list[str] = []
    seen: set[str] = set()
    for reading in readings:
        names, order, index = _named_from(reading, prefix)
        while index < len(order) and len(offered) < limit:
            name = names[order[index]]
            folded = _fold(name)
            if not folded.startswith(prefix):
                break
            if folded not in seen:
                seen.add(folded)
                offered.append(name)
            index += 1
    return offered


def forget() -> None:
    """Drop everything this process has read of the classification.

    For the tests, which point `DATA_DIR` somewhere else, and for anything that has just
    replaced a file and wants the new one read rather than the one held.
    """
    global _warned, _warned_skills
    for cached in (
        classification,
        _occupations_by_name,
        _unit_groups_by_name,
        skills_about,
        _skill_identifiers,
        _skill_names,
        _skill_order,
    ):
        cached.cache_clear()
    _warned = _warned_skills = False
