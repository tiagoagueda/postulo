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

from postulo.core import configuration, language_names, languages

logger = logging.getLogger(__name__)

#: Where the tables are read from: beside the code by default, and on the data volume
#: in the image, where `POSTULO_GEOLOCATIONS_DIR` points and the entrypoint writes them.
#: The source layer the code sits in is not a place the running container writes to.
DATA_DIR = Path(configuration.read("POSTULO_GEOLOCATIONS_DIR"))

#: The table the cities come from, and the table the country names in it are read from.
#: ``manage.py fetch_geonames`` writes both; ``data/GEONAMES-LICENCE.md`` says on whose
#: terms.
CITIES_FILE = "geonames-cities1000.txt"
COUNTRIES_FILE = "geonames-countryinfo.txt"

#: What the cities table is indexed into at provisioning, so no process parses it (#399),
#: and the countries' names with it, so none reads them out of CLDR (#1109). It is all a
#: match reads, so ``fetch_geonames`` removes the city table once this is built (#1127):
#: the lookup alone is the dataset, and a city table beside it is one an operator put there.
LOOKUP_FILE = "geonames-cities1000.sqlite"

#: GeoNames' postal codes, the step between a town and a street (#702): a centroid per
#: postcode for about a hundred countries, CC BY 4.0, downloaded only when an operator asks
#: (``manage.py fetch_geonames --postcodes``). For Canada, the Netherlands and the United
#: Kingdom the file holds the first part of each code only, and that part is what is matched.
POSTCODES_FILE = "geonames-postcodes.txt"
POSTCODES_LOOKUP_FILE = "geonames-postcodes.sqlite"

#: The countries whose codes the file holds only the first part of, and how a typed code
#: is cut to it: before the space, or the four digits of a Dutch code.
OUTWARD_ONLY = {"GB": "space", "CA": "space", "NL": "digits"}

#: What a file must answer to be a lookup a city is matched from.
_HOLDS_CITIES = "SELECT 1 FROM names JOIN cities ON cities.id = names.city LIMIT 1"

#: How long, in seconds, a process waits for another that is writing the country names
#: into the lookup (#1127). The writer reads every language's CLDR data while it holds the
#: lock, a few seconds on a slow disk; readers are not kept waiting by it.
REFRESH_WAIT = 30.0

#: Once is enough to say the dataset is not there, or that its lookup is read with the
#: country names it has because they could not be written again; neither is an error to
#: repeat.
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


def build_lookup(cities: Path, target: Path, countries: Path | None = None) -> int:
    """Write the lookup database for the table at ``cities`` to ``target``; the city count.

    Every name a city answers to, folded (its own, its ascii name and the alternates the
    table carries) points at its row, so a match is an indexed read and no process holds
    the table in memory. "Lisboa" answers to "Lisbon" because the table says so, not
    because the matcher believes it. ``manage.py fetch_geonames`` runs this once at
    provisioning; the file is replaced atomically, so a reader never sees half of one.

    The countries go in beside the cities (#1109): every name a country answers to, from
    the table at ``countries`` (the one beside ``cities`` unless another is named) and from
    CLDR, with what they were read from (`current_stamp`). Without that table there are
    no country names, and a typed country narrows nothing.
    """
    if countries is None:
        countries = cities.with_name(COUNTRIES_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Its own name per builder: three workers meeting an old install each build, and
    # none may write into another's half-made file.
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
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
        _write_countries(connection, countries)
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, target)
    return count


def _write_countries(connection: sqlite3.Connection, countries: Path) -> None:
    """Write the countries' names into a lookup, with what they were read from (#1109).

    The one way they are written: into a lookup as it is built, and again into one that
    stands without its city table when they have gone out of date (#1127). The two tables
    are made where a lookup from before #1109 has none, and what they held is replaced.
    It is the caller's transaction, so a reader sees the names as they were or as they
    are, and the cities are not touched.
    """
    names = _country_names(countries)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS countries (key TEXT, code TEXT, PRIMARY KEY (key, code))"
        " WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS about (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID"
    )
    connection.execute("DELETE FROM countries")
    connection.execute("DELETE FROM about")
    connection.executemany("INSERT INTO countries VALUES (?, ?)", names)
    connection.executemany("INSERT INTO about VALUES (?, ?)", current_stamp().items())


def _country_names(path: Path) -> list[tuple[str, str]]:
    """Every name a country answers to, folded, with the code the cities carry.

    ``countryInfo.txt`` has, by column, the ISO code (0), the ISO3 code (1) and the English
    name (4); the capital (5) and the postal-code format (13) are not names of the country
    and are not read, which is what made "Victoria" the Seychelles (#534). The file has no
    names in other languages, so those come from the CLDR tables Babel carries, in every
    language Postulo speaks: "España", "Deutschland" and "Ελλάδα" are names of a country
    too. A name two countries share is there once for each, and the matcher then says
    nothing rather than guess. Lines starting with ``#`` are the file's own header.

    Read when the lookup is built and at no other time (#1109): the names of 250 countries
    in 85 languages are every one of those languages' CLDR data loaded, which Babel would
    then keep (`_translated_countries` hands each back).
    """
    if not path.is_file():
        return []
    names: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) < 5 or not columns[0]:
            continue
        for name in (columns[0], columns[1], columns[4]):
            key = fold(name)
            if key:
                names.add((key, columns[0]))
    known = frozenset(code for _key, code in names if len(code) == 2)
    if known:
        for code, name in _translated_countries(known):
            key = fold(name)
            if key:
                names.add((key, code))
    return sorted(names)


def _translated_countries(known: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Each ``(code, name)`` CLDR gives a country of ``known``, in each language Postulo has.

    Only the codes ``countryInfo.txt`` lists: CLDR also names regions ("Europe", "World")
    and codes with no country, which no city carries.

    One language at a time, each handed back to Babel before the next is read (#1109):
    a locale is its language's whole CLDR data, and all of them at once are 60 MB that a
    process which only wanted the names would hold until the last one was read.
    """
    for tag in languages.NATIVE_NAMES:
        with language_names.borrowed_locales():
            try:
                territories = Locale.parse(tag, sep="-").territories
            except (UnknownLocaleError, ValueError):
                continue
            found = [(code, name) for code, name in territories.items() if code in known]
        yield from found


def current_stamp() -> dict[str, str]:
    """What a lookup built now says its country names were read from (#1109).

    The version of CLDR Babel carries and the languages Postulo has: a Babel upgrade
    renames a country here and there, and a new language brings its own names for all of
    them. Either leaves a lookup that was built before it short of names, and
    `postulo.jobs.checks` says so at start-up.
    """
    return {
        "cldr": language_names.cldr_version(),
        "languages": " ".join(sorted(languages.NATIVE_NAMES)),
    }


def lookup_stamp(lookup: Path | None = None) -> dict[str, str] | None:
    """What the lookup on this machine says its country names were read from.

    ``None`` where there is no lookup, or one from before it carried the countries.
    """
    lookup = lookup or _lookup_path()
    if not lookup.is_file():
        return None
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key, value FROM about").fetchall())
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _up_to_date(lookup: Path, cities: Path) -> bool:
    """Whether the lookup at ``lookup`` was made from the tables as they are now."""
    if not lookup.is_file():
        return False
    made = lookup.stat().st_mtime_ns
    countries = cities.with_name(COUNTRIES_FILE)
    if made < cities.stat().st_mtime_ns:
        return False
    if countries.is_file() and made < countries.stat().st_mtime_ns:
        return False
    return lookup_stamp(lookup) is not None


def _names_current(lookup: Path) -> bool:
    """Whether the country names in the lookup are the ones that would be written now.

    Read from the CLDR this Postulo carries, in the languages it has, and no earlier than
    the country table beside the lookup was last written. Asked of a lookup that stands
    without its city table (#1127), whose names are the one part of it that can go out of
    date, and the part that is written again without that table.
    """
    countries = lookup.with_name(COUNTRIES_FILE)
    try:
        if countries.is_file() and lookup.stat().st_mtime_ns < countries.stat().st_mtime_ns:
            return False
    except OSError:
        return False
    return lookup_stamp(lookup) == current_stamp()


def _refresh_countries(lookup: Path) -> bool:
    """Write the country names into the lookup at ``lookup`` again; whether it has them now.

    In place and in one transaction (#1127): the names and their stamp are replaced, made
    where a lookup from before #1109 has no tables for them, and the cities stay as they
    are, so nothing is downloaded for what CLDR and the kept country table already say.
    The write lock is taken before anything is read: of several processes that start
    together one writes, and the others wait for it and find the names written.

    ``False`` where they cannot be written, and the lookup is then as it was: there is no
    country table to read them from, the file is not a lookup, or it cannot be written (a
    volume mounted read-only, or a lock nobody let go of in `REFRESH_WAIT` seconds).
    """
    countries = lookup.with_name(COUNTRIES_FILE)
    if not countries.is_file():
        return False
    try:
        connection = sqlite3.connect(
            f"{lookup.as_uri()}?mode=rw", uri=True, timeout=REFRESH_WAIT, isolation_level=None
        )
    except sqlite3.Error:
        return False
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _names_current(lookup):
            connection.execute(_HOLDS_CITIES)
            _write_countries(connection, countries)
        connection.commit()
    except (sqlite3.Error, OSError, UnicodeError):
        return False
    finally:
        # Closing is what undoes a transaction that did not reach its commit.
        connection.close()
    return True


def _readable(lookup: Path) -> bool:
    """Whether the file at ``lookup`` is a lookup a match can read: it holds the cities."""
    if not lookup.is_file():
        return False
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute(_HOLDS_CITIES)
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return True


def _built(cities: Path, lookup: Path) -> Path:
    """The lookup built from the table at ``cities``: at ``lookup``, or in a temporary
    directory where that one is not writable."""
    try:
        build_lookup(cities, lookup)
    except FileNotFoundError:
        # The table went, which no other directory mends: `_index` reads what is left.
        raise
    except OSError:
        lookup = Path(tempfile.mkdtemp(prefix="postulo-geonames-")) / LOOKUP_FILE
        build_lookup(cities, lookup)
    return lookup


@lru_cache(maxsize=1)
def _index() -> Path | None:
    """The lookup database to read, or ``None`` where the dataset is not on this machine.

    Provisioning writes it (``fetch_geonames``) and removes the table it was built from
    (#1127), so this is a path and no request parses anything. A city table beside it is
    one an operator put there, or one from before the table was removed: where the lookup
    is missing, older than its tables, or from before it carried the countries (#1109), it
    is built here once from that table, which is left where it is.

    With no table the cities are what they are, and only the countries' names can have
    gone out of date: read from another CLDR or for other languages, older than the
    country table, or not there at all in a lookup from before #1109. They are written
    again here once, in place (`_refresh_countries`). Where they cannot be, the lookup is
    read as it is, and the log says how to have a new one.
    """
    global _warned
    lookup = _lookup_path()
    path = _cities_file()
    if path is not None:
        try:
            return lookup if _up_to_date(lookup, path) else _built(path, lookup)
        except FileNotFoundError:
            # `fetch_geonames` in another process removed the table between two looks at
            # it (#1127): the lookup it built first is what is there now.
            pass
    # Before the lookup is read: a write that was cut short is undone by the next one.
    stale = (
        lookup.is_file()
        and not _names_current(lookup)
        and not _refresh_countries(lookup)
        # Another process may have written them while this one waited in vain.
        and not _names_current(lookup)
    )
    if not _readable(lookup):
        if not _warned:
            _warned = True
            logger.warning(
                "The GeoNames city dataset has not been downloaded, so a location is "
                "not placed on the map. Run 'manage.py fetch_geonames' to download it "
                "in place."
            )
        return None
    if stale and not _warned:
        _warned = True
        logger.warning(
            "The country names in the GeoNames lookup are out of date and could not be "
            "written into it again: the country table is not beside it, or the lookup "
            "cannot be written. It is used as it is. Run 'manage.py fetch_geonames' to "
            "download the tables and build the lookup again."
        )
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


def _countries(key: str) -> frozenset[str] | None:
    """The codes of the countries that answer to the folded name ``key``.

    ``None`` for a name no country has, which narrows nothing. One indexed read of the
    lookup, as a city is (#1109): the names were put there when it was built, so no
    process reads a locale to answer, and none holds the table. A lookup with no countries
    in it answers as one whose country table was never downloaded.
    """
    lookup = _index()
    if lookup is None or not key:
        return None
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        found = connection.execute("SELECT code FROM countries WHERE key = ?", (key,)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    return frozenset(code for (code,) in found) or None


def postcode_key(code: str) -> str:
    """A postcode as it is compared: capitals, no spaces and no hyphens."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _outward(code: str, country: str) -> str:
    """The part of a code the file holds for a country it holds only part of, keyed."""
    cut = OUTWARD_ONLY.get(country)
    raw = (code or "").strip().upper()
    if cut == "space":
        return postcode_key(raw.split()[0]) if " " in raw else postcode_key(raw)[:-3]
    if cut == "digits":
        return "".join(ch for ch in raw if ch.isdigit())[:4]
    return postcode_key(raw)


def build_postcode_lookup(source: Path, target: Path) -> int:
    """Index GeoNames' postal codes at ``source`` into ``target``; how many codes.

    One centroid per country and code: where the file lists several places under one code,
    their mean, which is what a code that covers a few villages is centred on. Written to a
    temporary file and moved into place, as the cities' lookup is.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    sums: dict[tuple[str, str], list[float]] = {}
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            columns = line.rstrip("\r\n").split("\t")
            if len(columns) < 11 or not columns[0] or not columns[9] or not columns[10]:
                continue
            country = columns[0].upper()
            # The file's own codes: for the three countries it holds part of, that part.
            key = postcode_key(columns[1])
            if not key:
                continue
            try:
                lat, lon = float(columns[9]), float(columns[10])
            except ValueError:
                continue
            entry = sums.setdefault((country, key), [0.0, 0.0, 0])
            entry[0] += lat
            entry[1] += lon
            entry[2] += 1
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=target.name, suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute(
            "CREATE TABLE codes (country TEXT, key TEXT, lat REAL, lon REAL,"
            " PRIMARY KEY (country, key)) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO codes VALUES (?, ?, ?, ?)",
            [
                (country, key, lat / count, lon / count)
                for (country, key), (lat, lon, count) in sums.items()
            ],
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, target)
    return len(sums)


def _postcodes_lookup() -> Path | None:
    path = DATA_DIR / POSTCODES_LOOKUP_FILE
    return path if path.is_file() else None


def postcodes_available() -> bool:
    """Whether the postal codes were downloaded on this machine at all (#702)."""
    return _postcodes_lookup() is not None


def resolve_postcode(code: str, country: str) -> dict | None:
    """The centroid of a postcode in a country, or ``None`` where the file has none."""
    lookup = _postcodes_lookup()
    country = (country or "").upper()
    if lookup is None or not code or len(country) != 2:
        return None
    key = _outward(code, country) if country in OUTWARD_ONLY else postcode_key(code)
    if not key:
        return None
    connection = sqlite3.connect(f"{lookup.as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT lat, lon FROM codes WHERE country = ? AND key = ?", (country, key)
        ).fetchone()
    finally:
        connection.close()
    return {"lat": row[0], "lon": row[1]} if row else None


def place(postcode: str, town: str, country: str) -> tuple[str, dict] | None:
    """Where an address is, offline: its postcode's centroid, else its town (#702).

    ``("postcode", point)`` or ``("town", point)``, or ``None`` where neither is known. A
    town is read as the city table reads a typed location, narrowed by the address's
    country; nothing here is ever sent anywhere.
    """
    found = resolve_postcode(postcode, country)
    if found is not None:
        return "postcode", found
    town = (town or "").strip()
    if not town:
        return None
    answer = resolve(f"{town}, {country}" if country else town)
    if answer is None:
        return None
    return "town", {"lat": answer["lat"], "lon": answer["lon"]}


def available() -> bool:
    """Whether the dataset is on this machine at all: the map page says so plainly.

    The lookup is the dataset (#1127); a city table without one is a lookup at its first
    use.
    """
    return _lookup_path().is_file() or _cities_file() is not None


def lookup_alone() -> bool:
    """Whether the lookup is there without the city table it would be built again from."""
    return _lookup_path().is_file() and _cities_file() is None


def renews_its_countries() -> bool:
    """Whether country names that are out of date are written again at the first use.

    So for a lookup that stands alone, with the country table the names are read from
    beside it, where it can be written (#1127). `postulo.jobs.checks` asks before anything
    was matched, and does not report what the first match puts right.
    """
    lookup = _lookup_path()
    return (
        lookup_alone()
        and lookup.with_name(COUNTRIES_FILE).is_file()
        and os.access(lookup, os.W_OK)
        and os.access(lookup.parent, os.W_OK)
    )


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
        codes = _countries(fold(country))
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
