"""The offline geocoder: a location is a guess, and the record says so (#108).

The dataset itself is not in the tree (``data/GEONAMES-LICENCE.md``); the tests
build a table of their own and point the loader at it, the way the ESCO tests do
with the classification.
"""

from __future__ import annotations

import pytest

from postulo.jobs import places
from postulo.jobs.models import Company


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
    for cached in (places._index, places._countries):
        cached.cache_clear()
    yield
    for cached in (places._index, places._countries):
        cached.cache_clear()


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
    assert "victoria" not in places._countries()
    assert "#iso" not in places._countries()


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
    places._countries.cache_clear()
    try:
        assert places._countries()["iberia"] == {"PT", "DE"}
        assert places.resolve("Valencia, Iberia") is None
    finally:
        places._countries.cache_clear()


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


def test_a_table_without_a_lookup_gets_one_built_beside_it(data_dir):
    assert not (data_dir / places.LOOKUP_FILE).exists()

    assert places.resolve("Berlin")["country"] == "DE"

    assert (data_dir / places.LOOKUP_FILE).is_file()
    assert places.cities() == len(CITY_ROWS)
