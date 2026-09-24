"""Where a company is, read from the free text somebody typed, and why it is a guess.

A `location` is free text on the record: "Lisboa, Portugal", "Berlin (hybrid)", "Remote",
"" — whatever a person typed or a capture read off a page. A map of a job search needs
coordinates, and a geocoding service would answer that by being sent **the name of a
company this person is applying to**, which is precisely the disclosure the rest of this
codebase refuses to make (#108). So the resolution is offline: GeoNames' table of the
cities above a thousand inhabitants, downloaded into ``data/`` once, the way the ESCO
classification is (#266), and matched against when a location is saved. Nothing in a
request path reaches for the internet to ask what a place is.

The answer is a guess, and the record says so: the caller keeps which text it was
matched from, when, and whether a person then corrected it. "Springfield" is ambiguous
and "Remote" is not a place at all, and neither is an error state — an unplaced location
is a location the map does not draw, and the list beside the map still says where the
companies are.

City level, deliberately. A search at street precision is a map of where the person
will be at nine in the morning if any of it works out, and that is not a column to
keep; ``docs/THREAT-MODEL.md`` says the line. City level also happens to be all the
data supports — ``location`` is usually a city, and this table resolves exactly that.
"""

from __future__ import annotations

import logging
import unicodedata
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"

#: The table the cities come from, and the table the country names in it are read from.
#: ``manage.py fetch_geonames`` writes both; ``data/GEONAMES-LICENCE.md`` says on whose
#: terms.
CITIES_FILE = "geonames-cities1000.txt"
COUNTRIES_FILE = "geonames-countryinfo.txt"

#: Once is enough to say the dataset is not there; the absence is not an error to repeat.
_warned = False


def fold(text: str) -> str:
    """A name the way a person who does not own its spelling would type it.

    Casefolded, accents stripped — "München" and "Munchen" are one question — and
    nothing else: a matcher that also corrects typos is one that guesses in ways
    nobody can check.
    """
    stripped = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return stripped.casefold().strip()


def _cities_file() -> Path | None:
    path = DATA_DIR / CITIES_FILE
    return path if path.is_file() else None


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[dict, ...]]:
    """The table by every name it answers to, folded, so a match is a dictionary look.

    A city's row is indexed under its own name, its ascii name and each of the
    alternates the table carries — "Lisboa" answers to "Lisbon" because the table says
    so, not because the matcher believes it. Read once per process, the way the ESCO
    classification is: the table is a few megabytes and a save is not where it gets
    reparsed.
    """
    path = _cities_file()
    if path is None:
        global _warned
        if not _warned:
            _warned = True
            logger.warning(
                "The GeoNames city dataset has not been downloaded, so a location is "
                "not placed on the map. Run 'manage.py fetch_geonames' to download it "
                "in place."
            )
        return {}
    index: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        columns = line.split("\t")
        if len(columns) < 15:
            continue
        row = {
            "name": columns[1],
            "lat": float(columns[4]),
            "lon": float(columns[5]),
            "country": columns[8],
            "population": int(columns[14] or 0),
        }
        for name in [columns[1], columns[2], *columns[3].split(",")]:
            key = fold(name)
            if key:
                index.setdefault(key, []).append(row)
    return {key: tuple(rows) for key, rows in index.items()}


@lru_cache(maxsize=1)
def _countries() -> dict[str, frozenset[str]]:
    """A country's name, in the languages its table gives it, to the code the cities carry.

    The table's own name columns and every name in its language list are folded and
    matched to the two-letter code the cities' rows carry. A country name the table
    does not know — the reader's language not among them — is not a hint at all, and
    the matcher says nothing rather than guess.
    """
    path = DATA_DIR / COUNTRIES_FILE
    if not path.is_file():
        return {}
    names: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        columns = line.split("\t")
        if len(columns) < 14 or not columns[0]:
            continue
        candidates = [columns[4], columns[5]]
        candidates.extend(
            entry.split(":", 1)[1] for entry in columns[13].split(";") if ":" in entry
        )
        for name in candidates:
            key = fold(name)
            if key:
                names.setdefault(key, set()).add(columns[0])
    return {key: frozenset(codes) for key, codes in names.items()}


def available() -> bool:
    """Whether the dataset is on this machine at all: the map page says so plainly."""
    return _cities_file() is not None


def cities() -> int:
    """How many distinct cities the table answers to; a download that answered with a
    handful is not a download, and the command that wrote the file checks it."""
    return len(_index())


def resolve(text: str) -> dict | None:
    """A company's free-text location to a place, or ``None`` where the text is not one.

    The text is read as a city and, after a comma, a country: "Lisboa, Portugal". The
    city is matched against the table by its folded name; the country, when the table
    knows it, narrows the match — and when it contradicts it, the answer is nothing
    rather than the other Springfield. Where several cities still match, the largest
    is kept: a guess, and the record keeps the text it was made from, so a person can
    correct it where the guess was wrong.
    """
    text = (text or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    city = parts[0]
    country = ", ".join(parts[1:]) if len(parts) > 1 else ""
    candidates = _index().get(fold(city))
    if not candidates:
        return None
    if country:
        codes = _countries().get(fold(country))
        if codes is not None:
            inside = [row for row in candidates if row["country"] in codes]
            if not inside:
                return None
            candidates = inside
    pick = max(candidates, key=lambda row: row["population"])
    return {
        "lat": pick["lat"],
        "lon": pick["lon"],
        "name": pick["name"],
        "country": pick["country"],
    }
