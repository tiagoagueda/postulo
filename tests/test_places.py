"""The offline geocoder: a location is a guess, and the record says so (#108).

The dataset itself is not in the tree (``data/GEONAMES-LICENCE.md``); the tests
build a table of their own and point the loader at it, the way the ESCO tests do
with the classification.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time

import pytest
from babel import Locale, localedata

from postulo.core import language_names, languages
from postulo.jobs import places
from postulo.jobs.models import Company

pytestmark = pytest.mark.usefixtures("cldr_in_hand")


def city_row(
    geonameid: str,
    name: str,
    asciiname: str,
    alternates: str,
    lat: str,
    lon: str,
    country: str,
    population: int,
) -> str:
    """One row of the table, in the column order GeoNames publishes it."""
    return "\t".join(
        [
            geonameid,
            name,
            asciiname,
            alternates,
            lat,
            lon,
            "PPLC",
            "adm1",
            country,
            country,
            "",
            "",
            "",
            "",
            str(population),
            "0",
            "",
            "Europe/Lisbon",
            "2026-01-01",
        ]
    )


def country_row(iso: str, iso3: str, name: str, capital: str, languages: str = "") -> str:
    """One row of GeoNames' countryInfo.txt, in its real columns: the ISO codes, the FIPS
    code, the English name, the capital, area, population, continent, domain, currency,
    phone, postal-code format and regex, languages, geonameid and the neighbours."""
    return "\t".join(
        [
            *[iso, iso3, "000", iso, name, capital, "0", "0", "EU", "." + iso.lower()],
            *["EUR", "Euro", "0", "#####", "^(\\d{5})$", languages, "1", "", ""],
        ]
    )


CITY_ROWS = [
    ("2267067", "Porto", "Porto", "Vila do Porto,Oporto", "41.1500", "-8.6167", "PT", 242722),
    ("2267058", "Lisboa", "Lisbon", "Lissabon", "38.7167", "-9.1333", "PT", 547625),
    ("2950159", "Berlin", "Berlin", "", "52.5244", "13.4105", "DE", 3644832),
    ("2886242", "München", "Munich", "Munich,Muenchen", "48.1374", "11.5755", "DE", 1481478),
    ("6157998", "Tokyo", "Tokyo", "", "35.6895", "139.6917", "JP", 37991232),
    ("537445", "Springfield", "Springfield", "", "39.7817", "-89.6501", "US", 59503),
    ("5327371", "Springfield", "Springfield", "", "34.0064", "-120.4653", "US", 3460),
    ("2972817", "Springfield", "Springfield", "", "52.7036", "-6.9052", "IE", 1100),
    ("2158177", "Melbourne", "Melbourne", "", "-37.814", "144.9633", "AU", 4529500),
    ("2156643", "Geelong", "Geelong", "", "-38.1499", "144.3617", "AU", 253269),
    ("241131", "Victoria", "Victoria", "", "-4.6167", "55.45", "SC", 26450),
    ("2509954", "Valencia", "Valencia", "", "39.4698", "-0.3764", "ES", 814208),
    ("3625549", "Valencia", "Valencia", "", "10.1620", "-68.0077", "VE", 1385000),
]
TABLE = "\n".join(city_row(*row) for row in CITY_ROWS) + "\n"

COUNTRIES = (
    "#ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital\n"
    + "\n".join(
        [
            country_row("PT", "PRT", "Portugal", "Lisbon", "pt-PT,mwl"),
            country_row("DE", "DEU", "Germany", "Berlin", "de"),
            country_row("JP", "JPN", "Japan", "Tokyo", "ja"),
            country_row("US", "USA", "United States", "Washington", "en-US,es-US"),
            country_row("IE", "IRL", "Ireland", "Dublin", "en-IE,ga-IE"),
            country_row("AU", "AUS", "Australia", "Canberra", "en-AU"),
            country_row("SC", "SYC", "Seychelles", "Victoria", "en,fr-SC"),
            country_row("ES", "ESP", "Spain", "Madrid", "es-ES,ca"),
            country_row("VE", "VEN", "Venezuela", "Caracas", "es-VE"),
        ]
    )
    + "\n"
)


@pytest.fixture(autouse=True)
def fresh_loader():
    """The loader caches what it reads; a test that writes a table wants a fresh read."""
    places._index.cache_clear()
    yield
    places._index.cache_clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A directory the loader reads from, with the tables the tests write."""
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    (tmp_path / places.CITIES_FILE).write_text(TABLE, encoding="utf-8")
    (tmp_path / places.COUNTRIES_FILE).write_text(COUNTRIES, encoding="utf-8")
    return tmp_path


def test_a_city_and_country_resolves_to_the_rows_of_the_table(data_dir):
    answer = places.resolve("Lisboa, Portugal")

    assert answer is not None
    assert (answer["lat"], answer["lon"]) == (38.7167, -9.1333)
    assert answer["country"] == "PT"


def test_the_name_a_reader_types_is_the_name_the_table_carries(data_dir):
    """ "Lisbon" and "Lissabon" are names the table answers to, not beliefs of the matcher."""
    for spelling in ("Lisbon", "Lissabon", "lisboa"):
        answer = places.resolve(spelling)
        assert answer is not None
        assert answer["name"] == "Lisboa"


def test_accents_and_case_are_not_the_readers_proper(data_dir):
    """ "Munchen" and "MÜNCHEN" are one question (#108)."""
    for spelling in ("Munchen", "MÜNCHEN", "München"):
        answer = places.resolve(spelling)
        assert answer is not None
        assert answer["name"] == "München"


def test_a_country_narrows_a_name_the_table_carries_in_several_places(data_dir):
    """Springfield is not a place; Springfield, United States is one (#108)."""
    in_ireland = places.resolve("Springfield, Ireland")
    in_united_states = places.resolve("Springfield, United States")

    assert in_ireland["lon"] == pytest.approx(-6.9052)
    assert in_united_states["lon"] == pytest.approx(-89.6501)


def test_without_a_country_the_largest_is_kept_and_the_guess_is_a_guess(data_dir):
    """Several Springfields: the largest is the answer the record can correct."""
    answer = places.resolve("Springfield")

    assert answer is not None
    assert answer["country"] == "US"
    assert answer["name"] == "Springfield"


def test_a_country_the_table_does_not_know_is_not_a_hint(data_dir):
    """A country the table does not carry is nothing the matcher should act on."""
    answer = places.resolve("Springfield, Atlantis")

    assert answer is not None
    assert answer["country"] == "US"


def test_a_country_that_contradicts_the_name_is_nothing(data_dir):
    """Lisbon in Germany is not another Germany; the answer is nothing, not a guess."""
    assert places.resolve("Lisbon, Germany") is None


def test_a_name_the_table_does_not_carry_is_not_a_place(data_dir):
    assert places.resolve("Atlantis") is None


def test_remote_is_not_a_place(data_dir):
    """Not by a word list: it is not in the table, and that is the answer (#108)."""
    assert places.resolve("Remote") is None


def test_an_empty_location_is_no_question_at_all(data_dir):
    assert places.resolve("") is None
    assert places.resolve(None) is None


def test_without_the_dataset_the_answer_is_nothing_and_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)

    assert places.available() is False
    assert places.resolve("Berlin") is None
    assert places.cities() == 0


# ---------------------------------------------------------------- the record


def where(company):
    """The company's primary address, read fresh."""
    return company.postal_addresses.get(is_primary=True)


def test_a_saved_location_is_placed_from_the_dataset(user, data_dir):
    """A typed location is the town and country of the primary address since #702, and the
    address is placed at its town."""
    company = Company.objects.create(owner=user, name="A company", location="Lisboa, Portugal")

    address = where(company)
    assert (address.municipality, address.country) == ("Lisboa", "PT")
    assert (address.latitude, address.longitude) == (38.7167, -9.1333)
    assert address.placed_by == "town"
    assert address.placed_at is not None
    assert company.location == "Lisboa, Portugal"


def test_a_location_the_dataset_does_not_place_is_not_an_error(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Remote")

    address = where(company)
    assert address.municipality == "Remote"
    assert address.latitude is None and address.longitude is None
    assert address.placed_by == ""


def test_the_same_saved_again_is_not_a_new_guess(user, data_dir, monkeypatch):
    """The point is worked out when the address's words change, and a save that changes
    nothing in them asks nothing (#108, #702)."""
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    address = where(company)
    assert address.placed_by == "town"

    calls = []
    original = places.resolve

    def counting(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(places, "resolve", counting)
    address.street = "Unter den Linden 1"
    address.save()
    company.name = "A renamed company"
    company.save()

    assert calls == []


def test_a_changed_town_is_placed_again(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    address = where(company)
    address.municipality, address.country = "Lisboa", "PT"
    address.save()

    assert address.latitude == 38.7167
    assert address.placed_by == "town"


def test_a_changed_town_that_places_nowhere_clears_the_old_point(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    address = where(company)
    address.municipality, address.country = "Atlantis", ""
    address.save()

    assert address.latitude is None and address.longitude is None
    assert address.placed_by == ""


def test_a_point_a_person_placed_outlives_a_change_of_words(user, data_dir, monkeypatch):
    """A point placed by hand is not one the words would produce, and is not placed again."""
    from postulo.jobs import placing

    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    address = where(company)
    placing.place_by_hand(address, 39.7817, -6.9052)

    calls = []
    monkeypatch.setattr(places, "resolve", lambda text: calls.append(text))
    address.municipality = "Lisboa"
    address.save()
    address.refresh_from_db()

    assert calls == []
    assert (address.latitude, address.longitude) == (39.7817, -6.9052)
    assert address.placed_by == "manual"


def test_taking_a_hand_placed_point_off_places_it_from_its_words(user, data_dir):
    from postulo.jobs import placing

    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    address = where(company)
    placing.place_by_hand(address, 1.0, 2.0)

    placing.take_off_by_hand(address)

    address.refresh_from_db()
    assert address.placed_by == "town"
    assert address.longitude == 13.41053 or address.longitude != 2.0


def test_a_postcode_is_placed_at_its_centroid_where_the_codes_were_downloaded(user, data_dir):
    """The step between a town and a street, offline (#702)."""
    rows = "\n".join(
        [
            "PT\t1000-001\tLisboa\t\t\t\t\t\t\t38.7253\t-9.15\t6",
            "PT\t1000-001\tLisboa\t\t\t\t\t\t\t38.7255\t-9.152\t6",
            "GB\tSW1A\tLondon\t\t\t\t\t\t\t51.5\t-0.14\t4",
        ]
    )
    (data_dir / places.POSTCODES_FILE).write_text(rows + "\n", encoding="utf-8")
    places.build_postcode_lookup(
        data_dir / places.POSTCODES_FILE, data_dir / places.POSTCODES_LOOKUP_FILE
    )
    company = Company.objects.create(owner=user, name="A company")
    from postulo.core.models import PostalAddress

    address = PostalAddress.objects.create(
        owner=user, holder=company, postcode="1000 001", municipality="Lisboa", country="PT"
    )

    assert address.placed_by == "postcode"
    assert round(address.latitude, 4) == 38.7254
    assert places.resolve_postcode("SW1A 1AA", "GB") == {"lat": 51.5, "lon": -0.14}, (
        "the British file holds the first half of a code, and that half is what is matched"
    )


def test_with_the_map_off_nothing_is_placed(user, data_dir):
    from postulo.plugins.maps import MAPS
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.policy import forget_decisions

    PluginPolicy.objects.create(plugin=MAPS, person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()

    company = Company.objects.create(owner=user, name="A company", location="Berlin")

    address = where(company)
    assert address.municipality == "Berlin"
    assert address.latitude is None and address.placed_from == ""


def test_a_region_that_is_also_a_capital_is_not_a_country(data_dir):
    """Victoria is the capital of the Seychelles and a state of Australia (#534)."""
    for text in ("Melbourne, Victoria", "Geelong, Victoria"):
        answer = places.resolve(text)
        assert answer is not None, text
        assert answer["country"] == "AU"
    assert places._countries("victoria") is None
    assert places._countries("#iso") is None


def test_a_country_is_known_by_its_codes_and_its_english_name(data_dir):
    assert places.resolve("Valencia, Spain")["country"] == "ES"
    assert places.resolve("Valencia, ES")["country"] == "ES"
    assert places.resolve("Valencia, VEN")["country"] == "VE"


def test_a_country_in_another_language_narrows_the_city(data_dir):
    assert places.resolve("Valencia, España")["country"] == "ES"


@pytest.mark.parametrize(
    "text, code",
    [
        ("Valencia, España", "ES"),
        ("Valencia, Espanha", "ES"),
        ("Valencia, Venezuela", "VE"),
        ("Berlin, Deutschland", "DE"),
        ("Tokyo, Japão", "JP"),
        ("Porto, Πορτογαλία", "PT"),
        ("Lisboa, portugal", "PT"),
    ],
)
def test_a_country_is_known_by_its_name_in_any_language_postulo_speaks(data_dir, text, code):
    assert places.resolve(text)["country"] == code


def test_a_country_name_two_countries_share_keeps_both(data_dir, monkeypatch):
    """A name two countries answer to keeps both codes; a city of neither is not placed."""
    monkeypatch.setattr(
        places, "_translated_countries", lambda known: iter([("PT", "Iberia"), ("DE", "Iberia")])
    )
    assert places._countries("iberia") == {"PT", "DE"}
    assert places.resolve("Valencia, Iberia") is None


# ------------------------------------------- the countries are in the lookup (#1109)


def refuse_cldr(known):
    raise AssertionError("CLDR was read to match a country")


def test_a_country_is_matched_without_reading_a_locale(data_dir, monkeypatch):
    """Matching the first typed country read CLDR's names of every country in each of
    Postulo's 85 languages, and Babel kept every locale it was handed: 95 of them, about
    57 MB in each process for the rest of its life (#1109). The names are in the lookup
    since it was built, so a match is a read of it and loads no locale at all."""
    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)
    monkeypatch.setattr(places, "_translated_countries", refuse_cldr)
    monkeypatch.setattr(localedata, "_cache", {})

    for text, code in (
        ("Valencia, España", "ES"),
        ("Valencia, VEN", "VE"),
        ("Porto, Πορτογαλία", "PT"),
        ("Tokyo, Japão", "JP"),
        ("Springfield, Irlande", "IE"),
    ):
        assert places.resolve(text)["country"] == code, text
    assert places.resolve("Lisboa, Deutschland") is None

    assert localedata._cache == {}, "a locale was loaded to match a country"


def test_building_the_lookup_hands_back_the_locales_it_read(data_dir, monkeypatch):
    """The build is the one place the names are read out of CLDR, and on an install from
    before the countries were in the lookup it runs in a process that serves requests
    afterwards (#1109). What Babel held before, it holds after, and nothing more."""
    monkeypatch.setattr(localedata, "_cache", {})
    assert Locale.parse("pt_PT").territories["DE"] == "Alemanha"
    held = set(localedata._cache)
    assert "pt_PT" in held

    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)

    assert set(localedata._cache) == held
    assert places.resolve("Berlin, Deutschland")["country"] == "DE"


def test_a_country_is_known_by_its_name_in_every_language_postulo_has(data_dir):
    """Every language's names, as they were when each match read them out of CLDR (#1109):
    a country typed in a language that is not the reader's own is a country still."""
    codes = [line.split("\t")[0] for line in COUNTRIES.splitlines()[1:]]
    named = 0
    for tag in languages.NATIVE_NAMES:
        territories = Locale.parse(tag, sep="-").territories
        for code in codes:
            key = places.fold(territories.get(code, ""))
            if key:
                named += 1
                assert code in places._countries(key), (tag, code)
    assert named > 70 * len(codes), "most languages name every country of the table"


def test_a_lookup_from_before_the_countries_is_built_again(data_dir):
    """An install provisioned before #1109 has a lookup with the cities and nothing else.
    It is built again at its first use, as one from before the lookup existed is (#399),
    so a typed country narrows on it as it did."""
    lookup = data_dir / places.LOOKUP_FILE
    places.build_lookup(data_dir / places.CITIES_FILE, lookup)
    connection = sqlite3.connect(lookup)
    connection.executescript("DROP TABLE countries; DROP TABLE about;")
    connection.close()
    assert places.lookup_stamp() is None

    assert places.resolve("Springfield, Ireland")["country"] == "IE"

    assert places.lookup_stamp() == places.current_stamp()


def test_a_lookup_without_its_countries_narrows_nothing(data_dir):
    """As where the country table was never downloaded: no error, and no country (#1109)."""
    lookup = data_dir / places.LOOKUP_FILE
    places.build_lookup(data_dir / places.CITIES_FILE, lookup)
    connection = sqlite3.connect(lookup)
    connection.executescript("DROP TABLE countries;")
    connection.close()

    assert places._countries("ireland") is None
    assert places.resolve("Springfield, Ireland")["country"] == "US"


def test_without_the_country_table_a_typed_country_narrows_nothing(data_dir, monkeypatch):
    """No table of countries, no names of them, and CLDR is not read for none (#1109)."""
    (data_dir / places.COUNTRIES_FILE).unlink()
    monkeypatch.setattr(places, "_translated_countries", refuse_cldr)

    assert places.resolve("Springfield, Ireland")["country"] == "US"
    assert places.lookup_stamp() == places.current_stamp()


def test_a_lookup_built_again_is_what_the_next_match_reads(data_dir):
    """Nothing of the countries is held in a process (#1109): what `fetch_geonames` builds
    in another one is the next answer in this one, with no cache to clear in between."""
    assert places.resolve("Valencia, Hispania")["country"] == "VE", "no such country yet"

    more = COUNTRIES + country_row("ES", "ESP", "Hispania", "Madrid") + "\n"
    (data_dir / places.COUNTRIES_FILE).write_text(more, encoding="utf-8")
    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)

    assert places.resolve("Valencia, Hispania")["country"] == "ES"


def test_a_country_table_newer_than_the_lookup_is_indexed_again(data_dir):
    """A country table put there by hand after the lookup was made is read at the next
    start, as a newer city table is (#1109)."""
    lookup = data_dir / places.LOOKUP_FILE
    places.build_lookup(data_dir / places.CITIES_FILE, lookup)
    more = COUNTRIES + country_row("ES", "ESP", "Hispania", "Madrid") + "\n"
    (data_dir / places.COUNTRIES_FILE).write_text(more, encoding="utf-8")
    later = lookup.stat().st_mtime_ns + 5_000_000_000
    os.utime(data_dir / places.COUNTRIES_FILE, ns=(later, later))
    places._index.cache_clear()

    assert places.resolve("Valencia, Hispania")["country"] == "ES"


def test_start_up_says_when_the_country_names_are_from_another_cldr(data_dir, monkeypatch):
    """The names are read once, so a Babel upgrade does not reach them by itself (#1109):
    the lookup says which CLDR it was built from, and `manage.py check` says when that is
    not the one this Postulo carries. The names it has are matched meanwhile."""
    from postulo.jobs import checks

    assert checks.country_names(None) == [], "no lookup, nothing to say"
    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)
    assert checks.country_names(None) == []

    monkeypatch.setattr(language_names, "cldr_version", lambda: "99")
    problems = checks.country_names(None)

    assert [problem.id for problem in problems] == ["postulo.W003"]
    assert "CLDR 99" in problems[0].msg and language_names.CLDR_VERSION in problems[0].msg
    assert "fetch_geonames" in problems[0].hint
    places._index.cache_clear()
    assert places.resolve("Valencia, España")["country"] == "ES"


def test_start_up_says_when_postulo_has_a_language_the_names_were_not_read_in(
    data_dir, monkeypatch
):
    """A language added since the lookup was built has its own names for every country,
    and the lookup has none of them (#1109)."""
    from postulo.jobs import checks

    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)
    monkeypatch.setitem(languages.NATIVE_NAMES, "tlh", "tlhIngan Hol")

    assert [problem.id for problem in checks.country_names(None)] == ["postulo.W003"]


def test_a_save_reads_the_lookup_and_never_parses_the_table(data_dir, monkeypatch, user):
    """The table is parsed at provisioning (#399): with its lookup beside it, an address
    saved with a town reads that and nothing else, so no request pays for the build."""
    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)

    def refuse(*args, **kwargs):
        raise AssertionError("the table was parsed on a save")

    monkeypatch.setattr(places, "build_lookup", refuse)
    company = Company.objects.create(owner=user, name="Acme", location="Lisboa, Portugal")

    address = where(company)
    assert (address.latitude, address.longitude) == (38.7167, -9.1333)


# ------------------------------------------- the lookup without its table (#1127)


@pytest.fixture
def lookup_alone(data_dir):
    """The directory as `fetch_geonames` leaves it: the lookup, the country table, and no
    city table."""
    places.build_lookup(data_dir / places.CITIES_FILE, data_dir / places.LOOKUP_FILE)
    (data_dir / places.CITIES_FILE).unlink()
    return data_dir


def refuse_to_build(*args, **kwargs):
    raise AssertionError("the lookup was built again")


def test_the_lookup_alone_is_the_dataset(lookup_alone, monkeypatch):
    """The city table was the sign that the dataset is there, so it was kept for good
    beside the lookup built from it (#1127). The lookup is the sign now: with no table
    beside it the map has its dataset, and a location is placed as it was."""
    monkeypatch.setattr(places, "build_lookup", refuse_to_build)

    assert places.available() is True
    assert places.cities() == len(CITY_ROWS)
    assert places.resolve("Lissabon, Portogallo")["name"] == "Lisboa"
    assert places.resolve("Springfield, Irlande")["country"] == "IE"
    assert places.place("", "Berlin", "DE") == ("town", {"lat": 52.5244, "lon": 13.4105})


def test_a_table_put_beside_the_lookup_by_hand_is_indexed_and_left_there(lookup_alone):
    """A newer table an operator puts there is indexed at the next start, as it always
    was, and it is theirs: only the command that downloads a table removes it (#1127)."""
    table = lookup_alone / places.CITIES_FILE
    row = city_row("2735943", "Braga", "Braga", "", "41.5503", "-8.42", "PT", 121394)
    table.write_text(TABLE + row + "\n", encoding="utf-8")
    later = (lookup_alone / places.LOOKUP_FILE).stat().st_mtime_ns + 5_000_000_000
    os.utime(table, ns=(later, later))
    places._index.cache_clear()

    assert places.resolve("Braga, Portugal")["country"] == "PT"

    assert table.is_file()
    assert places.cities() == len(CITY_ROWS) + 1


#: A lookup as an upgrade finds it: its names read from an older CLDR, which had a name for
#: Spain that the one in hand has not, and lacked the one it has.
OLDER_NAMES = (
    "UPDATE about SET value = '41' WHERE key = 'cldr';"
    "DELETE FROM countries WHERE key = 'espana';"
    "INSERT INTO countries VALUES ('hispania', 'ES');"
)
#: And as a Postulo from before #1109 built it: the cities, and nothing of the countries.
BEFORE_THE_COUNTRIES = "DROP TABLE countries; DROP TABLE about;"


def alter(lookup, script):
    connection = sqlite3.connect(lookup)
    connection.executescript(script)
    connection.close()


def cities_and_names(lookup):
    """Every row of the two tables a city is matched from, in a fixed order."""
    connection = sqlite3.connect(lookup)
    try:
        return (
            connection.execute("SELECT * FROM cities ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM names ORDER BY key, city").fetchall(),
        )
    finally:
        connection.close()


def test_a_lookup_alone_reads_its_country_names_again_at_first_use(lookup_alone):
    """Names read from another CLDR were mended by building the lookup again, which takes
    the city table that is no longer kept, so every upgrade of Babel would have asked for
    32 MB to be downloaded again (#1127). The names are tables of their own, read from CLDR
    and from the country table that is kept: they are written again in place at the first
    use, and a typed country narrows by the names this Postulo has."""
    lookup = lookup_alone / places.LOOKUP_FILE
    alter(lookup, OLDER_NAMES)
    assert places.lookup_stamp() != places.current_stamp()

    assert places.resolve("Valencia, España")["country"] == "ES"

    assert places.lookup_stamp() == places.current_stamp()
    assert places._countries("hispania") is None, "a name only the older CLDR had"
    assert places.cities() == len(CITY_ROWS)


def test_a_lookup_alone_reads_the_names_of_a_language_postulo_gained(lookup_alone, monkeypatch):
    """A language added since the lookup was built is the other thing its names go out of
    date by, and is mended the same way (#1127)."""
    monkeypatch.setitem(languages.NATIVE_NAMES, "tlh", "tlhIngan Hol")
    assert places.lookup_stamp() != places.current_stamp()

    assert places.resolve("Berlin, Deutschland")["country"] == "DE"

    assert places.lookup_stamp() == places.current_stamp()


def test_a_lookup_alone_from_before_the_countries_gains_them_at_first_use(lookup_alone):
    """A lookup from before it carried the countries was built again from its table at its
    first use (#1109). Alone, it is given the two tables it lacks, and is then all a lookup
    built today is: nothing else of it changed with the countries (#1127)."""
    alter(lookup_alone / places.LOOKUP_FILE, BEFORE_THE_COUNTRIES)
    assert places.lookup_stamp() is None

    assert places.resolve("Springfield, Ireland")["country"] == "IE"
    assert places.resolve("Valencia, España")["country"] == "ES"

    assert places.lookup_stamp() == places.current_stamp()


@pytest.mark.parametrize("script", [OLDER_NAMES, BEFORE_THE_COUNTRIES], ids=["older", "none"])
def test_writing_the_country_names_again_leaves_the_cities_as_they_were(lookup_alone, script):
    """Only the countries are written: every city and every name of one is the row it was
    (#1127)."""
    lookup = lookup_alone / places.LOOKUP_FILE
    before = cities_and_names(lookup)
    assert len(before[0]) == len(CITY_ROWS) and len(before[1]) > len(CITY_ROWS)
    alter(lookup, script)

    assert places.resolve("Berlin, Deutschland")["country"] == "DE"

    assert places.lookup_stamp() == places.current_stamp()
    assert cities_and_names(lookup) == before


def test_a_country_table_newer_than_a_lookup_alone_is_read_into_it(lookup_alone):
    """A country table put there by hand after the lookup was made is read at the next
    start (#1109), and needs no city table to be (#1127)."""
    lookup = lookup_alone / places.LOOKUP_FILE
    more = COUNTRIES + country_row("ES", "ESP", "Hispania", "Madrid") + "\n"
    (lookup_alone / places.COUNTRIES_FILE).write_text(more, encoding="utf-8")
    earlier = lookup.stat().st_mtime_ns - 5_000_000_000
    os.utime(lookup, ns=(earlier, earlier))

    assert places.resolve("Valencia, Hispania")["country"] == "ES"
    assert places.cities() == len(CITY_ROWS)


def test_the_country_names_are_written_again_once(lookup_alone, monkeypatch):
    """Once for the instance, not once a worker or a start: the first use that finds the
    names out of date writes them, and every first use after it finds them current
    (#1127)."""
    alter(lookup_alone / places.LOOKUP_FILE, OLDER_NAMES)
    real, read = places._country_names, []

    def counting(path):
        read.append(path)
        return real(path)

    monkeypatch.setattr(places, "_country_names", counting)
    for _process in range(3):
        assert places.resolve("Valencia, España")["country"] == "ES"
        assert places.resolve("Berlin, Deutschland")["country"] == "DE"
        places._index.cache_clear()

    assert len(read) == 1


def test_two_workers_starting_together_write_the_names_once(lookup_alone, monkeypatch):
    """Workers start together and each meets the same lookup: one writes the names, the
    other waits for it and finds them written, and neither fails (#1127)."""
    lookup = lookup_alone / places.LOOKUP_FILE
    alter(lookup, OLDER_NAMES)
    real, read = places._country_names, []

    def slow(path):
        read.append(path)
        time.sleep(0.3)
        return real(path)

    monkeypatch.setattr(places, "_country_names", slow)
    together = threading.Barrier(2)
    answers = []

    def worker():
        together.wait()
        answers.append(places._refresh_countries(lookup))

    threads = [threading.Thread(target=worker) for _worker in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert answers == [True, True]
    assert len(read) == 1
    assert places.lookup_stamp() == places.current_stamp()
    assert places.resolve("Valencia, España")["country"] == "ES"


def test_names_that_cannot_be_written_again_leave_the_lookup_in_use(
    lookup_alone, monkeypatch, caplog
):
    """A lookup that cannot be written (a volume mounted read-only, or here a file another
    process holds) is read as it is: its cities and the names it has are matched, and the
    log says once how to have a new one (#1127)."""
    lookup = lookup_alone / places.LOOKUP_FILE
    alter(lookup, "UPDATE about SET value = '41' WHERE key = 'cldr'")
    monkeypatch.setattr(places, "REFRESH_WAIT", 0.05)
    monkeypatch.setattr(places, "_warned", False)
    holder = sqlite3.connect(lookup, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with caplog.at_level("WARNING", logger="postulo.jobs.places"):
            for _process in range(2):
                assert places.resolve("Valencia, España")["country"] == "ES"
                assert places.resolve("Lissabon")["name"] == "Lisboa"
                places._index.cache_clear()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert ["fetch_geonames" in record.getMessage() for record in caplog.records] == [True]
    assert places.lookup_stamp()["cldr"] == "41"


def test_without_the_country_table_a_lookup_alone_keeps_the_names_it_has(
    lookup_alone, monkeypatch, caplog
):
    """The names are read from the country table, so without it there are none to write:
    the ones the lookup has are kept, not emptied, and both the log and `manage.py check`
    say to run the download again (#1127)."""
    from postulo.jobs import checks

    alter(lookup_alone / places.LOOKUP_FILE, "UPDATE about SET value = '41' WHERE key = 'cldr'")
    (lookup_alone / places.COUNTRIES_FILE).unlink()
    monkeypatch.setattr(places, "_warned", False)

    with caplog.at_level("WARNING", logger="postulo.jobs.places"):
        assert places.resolve("Valencia, España")["country"] == "ES"

    assert ["fetch_geonames" in record.getMessage() for record in caplog.records] == [True]
    assert places.lookup_stamp()["cldr"] == "41"
    problems = checks.country_names(None)
    assert [problem.id for problem in problems] == ["postulo.W003"]
    assert "fetch_geonames" in problems[0].hint


@pytest.mark.parametrize("script", [OLDER_NAMES, BEFORE_THE_COUNTRIES], ids=["older", "none"])
def test_start_up_says_nothing_of_names_the_first_use_writes_again(lookup_alone, script):
    """`manage.py check` runs at start-up, before anything is matched: it does not report
    names that the first match puts right by itself (#1127)."""
    from postulo.jobs import checks

    alter(lookup_alone / places.LOOKUP_FILE, script)

    assert checks.country_names(None) == []
    assert places.resolve("Valencia, España")["country"] == "ES"
    assert checks.country_names(None) == []


def test_start_up_says_when_a_lookup_alone_has_no_names_and_cannot_be_given_them(lookup_alone):
    """A lookup from before the countries, with neither the city table to be built again
    from nor the country table to read the names from, is said at start-up (#1127)."""
    from postulo.jobs import checks

    alter(lookup_alone / places.LOOKUP_FILE, BEFORE_THE_COUNTRIES)
    (lookup_alone / places.COUNTRIES_FILE).unlink()

    problems = checks.country_names(None)

    assert [problem.id for problem in problems] == ["postulo.W003"]
    assert "fetch_geonames" in problems[0].hint
    assert places.resolve("Springfield, Ireland")["country"] == "US", "matched, not narrowed"


def test_a_file_that_is_no_lookup_is_no_dataset_and_no_error(data_dir, monkeypatch, caplog):
    """With no table to build from, a file under the lookup's name that is not one is an
    absent dataset, not an error on every save (#1127)."""
    (data_dir / places.CITIES_FILE).unlink()
    (data_dir / places.LOOKUP_FILE).write_bytes(b"not a database at all, " * 64)
    monkeypatch.setattr(places, "_warned", False)

    with caplog.at_level("WARNING", logger="postulo.jobs.places"):
        assert places.resolve("Berlin") is None
        assert places.cities() == 0

    assert ["fetch_geonames" in record.getMessage() for record in caplog.records] == [True]


def test_a_table_removed_while_it_is_being_looked_at_leaves_the_lookup(lookup_alone, monkeypatch):
    """`fetch_geonames` in another process removes the table once its lookup is built; a
    worker that saw the table a moment before reads the lookup, and does not fail (#1127)."""
    monkeypatch.setattr(places, "_cities_file", lambda: lookup_alone / places.CITIES_FILE)

    assert places.resolve("Berlin, Deutschland")["country"] == "DE"


def test_a_table_without_a_lookup_gets_one_built_beside_it(data_dir):
    assert not (data_dir / places.LOOKUP_FILE).exists()

    assert places.resolve("Berlin")["country"] == "DE"

    assert (data_dir / places.LOOKUP_FILE).is_file()
    assert places.cities() == len(CITY_ROWS)
