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
The revision is recorded inside the file, so whatever ``esco-occupations-*.zip`` is in the
directory is what the loader reads.

**The names are read a language at a time (#1110).** The file was one JSON document, and
reading it put every name in all 28 languages in each process to answer a reader in one:
fifteen megabytes held, thirty at the peak. So it is a zip, as the skills are: one member
says how the classification is built -- the revision, the languages, each unit group's
major group, each occupation's unit group and NACE divisions -- and there is a member of
names per language, in the same order. `classification` holds the first, and a language's
names are read when somebody first reads in it, with the English ones that stand in where
it has none. An ``esco-<revision>.json``, which is what a release before this one
downloaded, is still read where there is no zip, the same way and at the cost of parsing it
once per language; the next ``manage.py fetch_esco`` replaces it.

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
``esco-skills-<revision>.zip`` beside the occupations: a zip, under a name the occupations'
guard does not match, with a guard of its own refusing two.
Inside it are the identifiers, one per line and sorted, and one member per language whose
line *n* is the name of identifier *n*, empty where the classification publishes none. A
language is read when somebody first needs it -- the reader's, the record's, the one a CV
is in -- and at most `SKILL_LANGUAGES_HELD` of them are kept at once. Without the file a
skill matches nothing and the skill box offers nothing, which is the ordinary state.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import os
import re
import unicodedata
import zipfile
from array import array
from collections import OrderedDict
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from postulo.core import configuration
from postulo.core import languages as tags

logger = logging.getLogger(__name__)

#: Where the classification is read from and `manage.py fetch_esco` writes it: beside the
#: code by default, and on the data volume in the image, where `POSTULO_ESCO_DIR` points.
#: The source layer the code sits in belongs to root there, and a file written into it
#: would die with the layer an upgrade replaces.
DATA_DIR = Path(configuration.read("POSTULO_ESCO_DIR"))

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

#: What the files are called in the data directory: the occupations with their unit groups,
#: the same as a release before #1110 downloaded them, and the skills.
OCCUPATIONS_PATTERN = "esco-occupations-*.zip"
LEGACY_PATTERN = "esco-*.json"
SKILLS_PATTERN = "esco-skills-*.zip"

#: The members of ``esco-occupations-<revision>.zip``: what the file says about itself and
#: how the classification is built, and a member of names per language. A member of names
#: is ``{"unit_groups": [...], "occupations": [...]}``, each list in the order the first
#: member keeps its entries and empty where the classification publishes no name.
OCCUPATIONS_ABOUT = "about.json"
OCCUPATIONS_NAMES = "names/{language}.json"

#: The two kinds of entry the classification holds, as the file and a member of names key
#: them.
KINDS = ("unit_groups", "occupations")

#: How many languages' names are held as the file has them: the one being read and English,
#: which stands in where it has none. They are what a language's matching is built from,
#: once, and a third of a megabyte each (measured on v1.2.1) that nothing asks for again; a
#: language that has left and is wanted for something not yet built is read again.
NAMES_HELD = 2

#: Once is enough to say the file is not there; the absence is not an error to repeat.
_warned = False

#: And once to say it is the file of an earlier release.
_warned_legacy = False


def data_file() -> Path | None:
    """The classification in the data directory, or None where it has not been downloaded.

    One revision at a time: ``manage.py fetch_esco`` writes
    ``esco-occupations-<revision>.zip`` and deletes the one it replaces, so two files, which
    only a hand-filled directory has, are a state to be reported (`postulo.jobs.checks` does
    it at start-up), not a choice to be made.

    The ``esco-<revision>.json`` of a release before #1110 is what is read where there is no
    zip, so an instance that has just been upgraded keeps its codes; beside a zip it is what
    ``fetch_esco`` is about to delete, and is not read.
    """
    for pattern in (OCCUPATIONS_PATTERN, LEGACY_PATTERN):
        files = sorted(DATA_DIR.glob(pattern))
        if len(files) > 1:
            names = ", ".join(file.name for file in files)
            raise RuntimeError(
                f"two ESCO classifications in {DATA_DIR} ({names}); delete the one being replaced"
            )
        if files:
            return files[0]
    return None


def is_legacy(path: Path | None) -> bool:
    """Whether ``path`` is the classification as a release before #1110 downloaded it."""
    return path is not None and path.suffix == ".json"


def structure_of(document: dict) -> dict:
    """A whole classification less its names: what `classification` holds, and what
    ``manage.py fetch_esco`` writes as the first member of the file."""
    return {
        **document,
        **{
            kind: {
                code: {key: value for key, value in entry.items() if key != "names"}
                for code, entry in document[kind].items()
            }
            for kind in KINDS
        },
    }


def names_of(document: dict, language: str) -> dict[str, list[str]]:
    """One language's names out of a whole classification: a member of names, as
    ``manage.py fetch_esco`` writes it and `_names` reads it."""
    return {
        kind: [entry.get("names", {}).get(language) or "" for entry in document[kind].values()]
        for kind in KINDS
    }


def _stamped(path: Path, name: str = "") -> tuple[tuple, str]:
    """One member of a file ``manage.py fetch_esco`` wrote, as text, and what tells that file
    from one that replaces it: its name, when it was written and its size.

    The member is empty where the file has none of that name; with no name, the text is the
    file's own, which is how the ``.json`` of an earlier release is read. The file is asked
    what it is through the handle it is read by, so the answer is about what was read.
    """
    with path.open("rb") as handle:
        status = os.fstat(handle.fileno())
        stamp = (path.name, status.st_mtime_ns, status.st_size)
        if not name:
            return stamp, handle.read().decode("utf-8")
        with zipfile.ZipFile(handle) as archive:
            try:
                return stamp, archive.read(name).decode("utf-8")
            except KeyError:
                return stamp, ""


def _member(path: Path | None, name: str) -> str:
    """One member of a file ``manage.py fetch_esco`` wrote, as text; empty where the file or
    the member is not. The occupations and the skills are both read through `_stamped`."""
    return _stamped(path, name)[1] if path is not None else ""


def _part(language: str = "") -> tuple[tuple, dict]:
    """One part of the occupations file, and what tells the file it came out of from another:
    how the classification is built, or, for a ``language``, the names in it.

    Nothing, told by nothing, where there is no file. An earlier release's file is parsed
    whole for each part and only the part is kept, so it costs a parse per language read
    rather than every language held.
    """
    global _warned_legacy
    for _look in range(2):
        path = data_file()
        if path is None:
            break
        try:
            if not is_legacy(path):
                member = OCCUPATIONS_NAMES.format(language=language) if language else ""
                stamp, text = _stamped(path, member or OCCUPATIONS_ABOUT)
                return stamp, json.loads(text or "{}")
            stamp, text = _stamped(path)
        except FileNotFoundError:
            # Replaced between being listed and being opened: its replacement is there now.
            continue
        if not _warned_legacy:
            _warned_legacy = True
            logger.warning(
                "The ESCO classification in %s is %s, the file an earlier release of Postulo "
                "downloaded, which is read whole for each language. Run 'manage.py "
                "fetch_esco' to replace it.",
                DATA_DIR,
                path.name,
            )
        document = json.loads(text)
        return stamp, names_of(document, language) if language else structure_of(document)
    return (), {}


@lru_cache(maxsize=1)
def _structure() -> tuple[tuple, dict]:
    """What `classification` answers, and which file it was read out of."""
    stamp, document = _part()
    if not stamp:
        global _warned
        if not _warned:
            _warned = True
            logger.warning(
                "The ESCO classification has not been downloaded, so no title matches a "
                "code and the title box offers no unit groups. Run 'manage.py fetch_esco' "
                "to download it in place."
            )
    return stamp, document or ABSENT


def classification() -> dict:
    """How the classification is built, and none of its names (#1110): the revision, the
    languages, each unit group's major group, each occupation's unit group and NACE divisions.

    The names are read a language at a time, by `_names`. The empty document where the file
    has not been downloaded; ``manage.py fetch_esco`` is how it gets there.
    """
    return _structure()[1]


@lru_cache(maxsize=NAMES_HELD)
def _names(language: str) -> tuple[tuple, tuple[str, ...], tuple[str, ...]]:
    """One language's names as the file has them, after which file that was: the unit
    groups' and then the occupations', each in the order `classification` keeps its entries
    and empty where the classification publishes none.

    Read when somebody first needs the language, and only that member of the file (#1110).
    A language the file has no member for has no names here, and reads the English.
    """
    stamp, found = _part(language)
    return stamp, tuple(found.get(KINDS[0]) or ()), tuple(found.get(KINDS[1]) or ())


def _held(reading: str) -> tuple[dict, tuple, tuple]:
    """How the classification is built, and its names in ``reading`` and in English as
    ``(unit groups, occupations)``, all three out of one file.

    The three are read when each is first needed, and ``manage.py fetch_esco`` may replace
    the file in between on an instance that is running, which is how an instance gets its
    next revision. The names of one revision would then be laid against the entries of
    another. So each part remembers the file it came out of, and where they differ
    everything this process holds is dropped and read again: it moves to the new file
    whole, rather than answering from two.
    """
    for _attempt in range(2):
        stamp, document = _structure()
        names, english = _names(reading), _names(FALLBACK)
        if names[0] == english[0] == stamp:
            break
        forget()
    else:
        raise RuntimeError(f"the ESCO classification in {DATA_DIR} changed while it was read")

    def fitted(columns) -> tuple:
        fit = []
        for kind, column in zip(KINDS, columns, strict=True):
            column = column or ("",) * len(document[kind])
            if len(column) != len(document[kind]):
                raise RuntimeError(f"{data_file()} is not a file 'manage.py fetch_esco' wrote")
            fit.append(column)
        return tuple(fit)

    return document, fitted(names[1:]), fitted(english[1:])


@lru_cache(maxsize=32)
def _unit_group_names(reading: str) -> dict[str, str]:
    """``{code: name}`` for every unit group, in code order, as one language reads them: the
    English name where the language has none, and empty where there is neither."""
    document, (names, _occupations), (english, _english_occupations) = _held(reading)
    return {
        code: name or fallback
        for code, name, fallback in zip(document["unit_groups"], names, english, strict=True)
    }


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
    return [(code, name or code) for code, name in _unit_group_names(_reading(language)).items()]


def name_for(code: str, language: str = "") -> str:
    """What a unit group is called, or empty where nothing here has that code.

    Empty rather than the code, because a code that has gone from a later revision belongs
    to a person's record that keeps its name — it is not something to print at them.
    """
    return _unit_group_names(_reading(language)).get(code, "")


def major_of(code: str) -> str:
    """The ISCO-08 major group a unit group belongs to, the level-1 ancestor."""
    entry = classification()["unit_groups"].get(code)
    return entry["major"] if entry else ""


def _forms(label: str) -> list[str]:
    """The names one label holds: ESCO writes both genders of an occupation in one string,
    ``développeur de logiciels/développeuse de logiciels``, and a title is typed as one of
    them. The whole label is kept as well, for the few that have another slash in them."""
    forms = [label]
    if "/" in label:
        forms.extend(part for part in label.split("/"))
    return [folded for folded in (_fold(form) for form in forms) if folded]


@lru_cache(maxsize=32)
def _occupations_by_name(reading: str) -> dict[str, str]:
    """``{folded occupation name: unit group}`` for one language, so a typed title can find
    the code of the unit group it belongs to.

    The value is the unit group rather than the occupation, because that is the level a
    report can say and a picker can offer. Two occupations that share a name in one
    language and sit in one group give that group; a name that sits in more than one gives
    nothing, because a four-digit code cannot say which -- as for skills, no match is
    better than an arbitrary one.
    """
    groups: dict[str, set[str]] = {}
    document, (_groups, names), (_english_groups, english) = _held(reading)
    for entry, name, fallback in zip(document["occupations"].values(), names, english, strict=True):
        for folded in _forms(name or fallback):
            groups.setdefault(folded, set()).add(entry["isco"])
    return {name: next(iter(found)) for name, found in groups.items() if len(found) == 1}


@lru_cache(maxsize=32)
def _unit_groups_by_name(reading: str) -> dict[str, str]:
    """``{folded unit group name: code}`` for one language, the level the picker offers."""
    return {_fold(name): code for code, name in _unit_group_names(reading).items()}


def code_for(name: str, language: str = "") -> str:
    """The unit-group code a typed job title matches, in the reader's language or in English.

    An occupation's name is tried first and a unit group's second, because the more
    specific the match, the more the standard did the work. Both are tried in the reader's
    language and then in English, because somebody reading Postulo in Portuguese may paste
    an English title out of a form they were sent. A name that matches nothing has no
    code, which is the ordinary case and not a failure.
    """
    folded = _fold(name)
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


@lru_cache(maxsize=1)
def _divisions_by_group() -> dict[str, frozenset[str]]:
    """``{unit group: NACE divisions}``, the union over the group's occupations (#897)."""
    found: dict[str, set[str]] = {}
    for entry in classification()["occupations"].values():
        found.setdefault(entry["isco"], set()).update(entry.get("nace") or ())
    return {group: frozenset(divisions) for group, divisions in found.items()}


def divisions_for(unit_group: str) -> frozenset[str]:
    """The NACE divisions the occupations of a unit group are usually employed in, through
    ESCO's companion correspondence (#897). Empty for a code nothing here has, and for a
    file harvested before the correspondence was."""
    return _divisions_by_group().get(unit_group, frozenset())


def suggestions(exclude=(), language: str = "", sections=()) -> list[str]:
    """What a picker offers, minus anything the person already has.

    The unit groups in code order: the level a person recognises, which is why the
    occupations are not offered — a picker is not a directory, and the occupations are
    there to be matched, not chosen. Where a company's ``sections`` are known, the groups
    whose occupations ESCO places in them come first (#897); nothing is left out.
    """
    from . import industries

    taken = {str(name).casefold() for name in exclude}
    offered = [(code, name) for code, name in unit_groups(language) if name.casefold() not in taken]
    wanted = set(sections or ())
    if not wanted:
        return [name for _code, name in offered]

    def near(pair) -> bool:
        return bool(wanted & set(industries.sections_for(divisions_for(pair[0]))))

    return [
        name
        for _code, name in [p for p in offered if near(p)] + [p for p in offered if not near(p)]
    ]


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

    One revision at a time, for the reason `data_file` gives. Neither of the occupations'
    patterns matches this file's name, so their guard stays what it was, and this is the
    same guard for the skills.
    """
    files = sorted(DATA_DIR.glob(SKILLS_PATTERN))
    if len(files) > 1:
        names = ", ".join(file.name for file in files)
        raise RuntimeError(
            f"two ESCO skills files in {DATA_DIR} ({names}); delete the one being replaced"
        )
    return files[0] if files else None


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
    return json.loads(_member(skills_file(), SKILLS_ABOUT) or "null") or ABSENT_SKILLS


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
    identifiers = tuple(_member(skills_file(), SKILLS_IDENTIFIERS).split("\n"))
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
    member = SKILLS_NAMES.format(language=language)
    names = tuple(_member(skills_file(), member).split("\n"))
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


#: What a word is in a listing's text, as `skills_named` reads it: a letter or a digit, then
#: whatever a skill's name keeps inside a word -- ``C++``, ``C#``, ``ASP.NET``, ``CI/CD``,
#: ``l'information`` -- less what only ends a sentence.
_WORD = re.compile(r"\w[\w+#.'/-]*")
_WORD_ENDS = ".'/-"

#: How many texts `skills_named` remembers what it found in, per process, the oldest going
#: first. The answer is a few identifiers, and the key is a digest of the text with the
#: revision it was read in, so a description edited is a new key rather than a stale answer,
#: and one worker never needs to hear what another found.
SKILLS_NAMED_HELD = 256
_named: OrderedDict[tuple, tuple[str, ...]] = OrderedDict()


def _words(text: str) -> list[str]:
    """A text as the folded words a skill's name is compared with."""
    words = (word.rstrip(_WORD_ENDS) for word in _WORD.findall(_fold(text)))
    return [word for word in words if word]


def _beginning(language: str, prefix: str) -> list[int]:
    """The positions of the names in ``language`` that, folded, begin with ``prefix``."""
    names, order, index = _named_from(language, prefix)
    found = []
    while index < len(order) and _fold(names[order[index]]).startswith(prefix):
        found.append(order[index])
        index += 1
    return found


def _continues(language: str, prefix: str) -> bool:
    """Whether some name in ``language``, folded, begins with ``prefix``."""
    names, order, index = _named_from(language, prefix)
    return index < len(order) and _fold(names[order[index]]).startswith(prefix)


def _named_as(phrase: str, language: str) -> int | None:
    """The skill a run of words in a text names, in ``language``, where exactly one does.

    Its preferred name, as `skill_for` matches a typed one; or that name less the words in
    brackets ESCO adds to tell one sense from another -- *Python (computer programming)* is
    named in a listing as *Python* -- where no skill has the bare name and one has it with
    them. Two skills either way is a name that says neither.
    """
    names, order, index = _named_from(language, phrase)
    exact: set[int] = set()
    while index < len(order) and _fold(names[order[index]]) == phrase:
        exact.add(order[index])
        index += 1
    if exact:
        return exact.pop() if len(exact) == 1 else None
    qualified = {
        position
        for position in _beginning(language, phrase + " (")
        if _fold(names[position]).endswith(")")
    }
    return qualified.pop() if len(qualified) == 1 else None


def _named_in(words: list[str], language: str) -> list[int]:
    """The skills a text names in one language, in the order it first names them.

    Read left to right, taking at each word the longest run of words that is a skill's
    name: *project management* is one skill, not *project management* and *management*. A
    run grows only while some name begins with it, so most words cost one look.
    """
    found: list[int] = []
    index = 0
    while index < len(words):
        phrase, end, best = words[index], index, None
        while True:
            position = _named_as(phrase, language)
            if position is not None:
                best = (end, position)
            end += 1
            if end >= len(words) or not _continues(language, phrase + " "):
                break
            phrase = f"{phrase} {words[end]}"
        if best is None:
            index += 1
            continue
        end, position = best
        if position not in found:
            found.append(position)
        index = end + 1
    return found


def skills_named(text: str, *languages: str) -> tuple[str, ...]:
    """The URIs of the ESCO skills a text names, in the order it first names them (#1093).

    For a listing: which of the classification's skills its words ask for, with no model
    and nothing fetched. A skill is named where a run of the text's words is its preferred
    name, folded as `skill_for` folds a typed one. The text is read in each of
    ``languages`` the classification publishes, and in English, and the language that finds
    the most is the one the answer comes from: a listing is written in one language, and
    the names of another found in it are mostly words that happen to be spelt alike.

    Remembered per text, so a page drawn again reads it once. Nothing where the skills have
    not been downloaded.
    """
    if not skills_available() or not str(text or "").strip():
        return ()
    readings: list[str] = []
    for code in (*languages, FALLBACK):
        language = _skill_language(code)
        if language and language not in readings:
            readings.append(language)
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    key = (skills_about()["revision"], digest, tuple(readings))
    held = _named.get(key)
    if held is not None:
        return held
    words = _words(text)
    best: list[int] = []
    for language in readings:
        found = _named_in(words, language)
        if len(found) > len(best):
            best = found
    identifiers = _skill_identifiers()
    answer = tuple(SKILL_NAMESPACE + identifiers[position] for position in best)
    _named[key] = answer
    while len(_named) > SKILLS_NAMED_HELD:
        try:
            _named.popitem(last=False)
        except KeyError:  # Another thread emptied it first.
            break
    return answer


def forget() -> None:
    """Drop everything this process has read of the classification.

    For the tests, which point `DATA_DIR` somewhere else, and for anything that has just
    replaced a file and wants the new one read rather than the one held.
    """
    global _warned, _warned_legacy, _warned_skills
    for cached in (
        _structure,
        _names,
        _unit_group_names,
        _occupations_by_name,
        _unit_groups_by_name,
        _divisions_by_group,
        skills_about,
        _skill_identifiers,
        _skill_names,
        _skill_order,
    ):
        cached.cache_clear()
    _named.clear()
    _warned = _warned_legacy = _warned_skills = False
