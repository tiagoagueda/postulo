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
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from django.utils.translation import get_language

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
