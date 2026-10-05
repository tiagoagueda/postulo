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
import os
import sqlite3
import tempfile
import unicodedata
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from babel import Locale, UnknownLocaleError

from postulo.core import languages

logger = logging.getLogger(__name__)

#: Where the tables are read from: beside the code by default, and on the data volume
#: in the image, where `POSTULO_GEOLOCATIONS_DIR` points and the entrypoint writes them.
#: The source layer the code sits in is not a place the running container writes to.
DATA_DIR = Path(
    os.environ.get("POSTULO_GEOLOCATIONS_DIR") or Path(__file__).resolve().parent / "data"
)

#: The table the cities come from, and the table the country names in it are read from.
#: ``manage.py fetch_geonames`` writes both; ``data/GEONAMES-LICENCE.md`` says on whose
#: terms.
CITIES_FILE = "geonames-cities1000.txt"
COUNTRIES_FILE = "geonames-countryinfo.txt"

#: What the cities table is indexed into at provisioning, so no process parses it (#399).
LOOKUP_FILE = "geonames-cities1000.sqlite"

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


def _lookup_path() -> Path:
    return DATA_DIR / LOOKUP_FILE


def build_lookup(cities: Path, target: Path) -> int:
    """Write the lookup database for the table at ``cities`` to ``target``; the city count.

    Every name a city answers to, folded (its own, its ascii name and the alternates the
    table carries) points at its row, so a match is an indexed read and no process holds
    the table in memory. "Lisboa" answers to "Lisbon" because the table says so, not
    because the matcher believes it. ``manage.py fetch_geonames`` runs this once at
    provisioning; the file is replaced atomically, so a reader never sees half of one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            "CREATE TABLE cities (id INTEGER PRIMARY KEY, name TEXT, lat REAL, lon REAL,"
            " country TEXT, population INTEGER);"
            "CREATE TABLE names (key TEXT, city INTEGER, PRIMARY KEY (key, city))"
            " WITHOUT ROWID;"
        )
        count = 0
        with cities.open(encoding="utf-8") as handle:
            for line in handle:
                columns = line.rstrip("\r\n").split("\t")
                if len(columns) < 15:
                    continue
                count += 1
                connection.execute(
                    "INSERT INTO cities VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        count,
                        columns[1],
                        float(columns[4]),
                        float(columns[5]),
                        columns[8],
                        int(columns[14] or 0),
                    ),
                )
                keys = {fold(name) for name in [columns[1], columns[2], *columns[3].split(",")]}
                connection.executemany(
                    "INSERT INTO names VALUES (?, ?)", [(key, count) for key in keys if key]
                )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, target)
    return count


@lru_cache(maxsize=1)
def _index() -> Path | None:
    """The lookup database to read, or ``None`` where the dataset is not on this machine.

    Provisioning writes it beside the table (``fetch_geonames``), so this is a path
    and no request parses anything. An install that has the table from before the
    lookup existed, or whose lookup is older than its table, gets it built here once,
    beside the table, or in a temporary directory where that one is not writable.
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
        return None
    lookup = _lookup_path()
    if lookup.is_file() and lookup.stat().st_mtime_ns >= path.stat().st_mtime_ns:
        return lookup
    try:
        build_lookup(path, lookup)
    except OSError:
        lookup = Path(tempfile.mkdtemp(prefix="postulo-geonames-")) / LOOKUP_FILE
        build_lookup(path, lookup)
    return lookup


def _candidates(key: str) -> list[dict]:
    """Every city that answers to the folded name ``key``, as rows."""
    lookup = _index()
    if lookup is None:
        return []
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        found = connection.execute(
            "SELECT c.name, c.lat, c.lon, c.country, c.population FROM names n"
            " JOIN cities c ON c.id = n.city WHERE n.key = ?",
            (key,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {"name": name, "lat": lat, "lon": lon, "country": country, "population": population}
        for name, lat, lon, country, population in found
    ]


@lru_cache(maxsize=1)
def _countries() -> dict[str, frozenset[str]]:
    """A country's name to the code the cities carry: its ISO codes and its names.

    ``countryInfo.txt`` has, by column, the ISO code (0), the ISO3 code (1) and the English
    name (4); the capital (5) and the postal-code format (13) are not names of the country
    and are not read, which is what made "Victoria" the Seychelles (#534). The file has no
    names in other languages, so those come from the CLDR tables Babel carries, in every
    language Postulo speaks: "España", "Deutschland" and "Ελλάδα" are names of a country
    too. A name two countries share keeps both codes, and the matcher then says nothing
    rather than guess. Lines starting with ``#`` are the file's own header.
    """
    path = DATA_DIR / COUNTRIES_FILE
    if not path.is_file():
        return {}
    names: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        columns = line.split("	")
        if len(columns) < 5 or not columns[0]:
            continue
        for name in (columns[0], columns[1], columns[4]):
            key = fold(name)
            if key:
                names.setdefault(key, set()).add(columns[0])
    for code, name in _translated_countries(
        frozenset(code for codes in names.values() for code in codes if len(code) == 2)
    ):
        key = fold(name)
        if key:
            names.setdefault(key, set()).add(code)
    return {key: frozenset(codes) for key, codes in names.items()}


def _translated_countries(known: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Each ``(code, name)`` CLDR gives a country of ``known``, in each language Postulo has.

    Only the codes ``countryInfo.txt`` lists: CLDR also names regions ("Europe", "World")
    and codes with no country, which no city carries.
    """
    for tag in languages.NATIVE_NAMES:
        try:
            territories = Locale.parse(tag, sep="-").territories
        except (UnknownLocaleError, ValueError):
            continue
        for code, name in territories.items():
            if code in known:
                yield code, name


def available() -> bool:
    """Whether the dataset is on this machine at all: the map page says so plainly."""
    return _cities_file() is not None


def cities() -> int:
    """How many cities the table holds; a download that answered with a
    handful is not a download, and the command that wrote the file checks it."""
    lookup = _index()
    if lookup is None:
        return 0
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
    finally:
        connection.close()


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
    candidates = _candidates(fold(city))
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
