"""The offline geocoder: a location is a guess, and the record says so (#108).

The dataset itself is not in the tree (``data/GEONAMES-LICENCE.md``); the tests
build a table of their own and point the loader at it, the way the ESCO tests do
with the classification.
"""

from __future__ import annotations

import pytest

from postulo.jobs import places
from postulo.jobs.models import Company, LocationSource


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


def country_row(iso: str, iso3: str, name: str, languages: str) -> str:
    """One row of the country table; the languages are what a reader may call it."""
    return "\t".join(
        [
            iso,
            iso3,
            iso3,
            iso,
            name,
            name,
            "",
            "0",
            "0",
            "Europe",
            "",
            "EUR",
            "Euro",
            languages,
            "",
            "000",
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
]
TABLE = "\n".join(city_row(*row) for row in CITY_ROWS) + "\n"

COUNTRIES = (
    "\n".join(
        [
            country_row("PT", "PRT", "Portugal", "English:Portugal;Portuguese:Portugal"),
            country_row("DE", "DEU", "Germany", "English:Germany;German:Deutschland"),
            country_row("JP", "JPN", "Japan", "English:Japan;Japanese:\u65e5\u672c"),
            country_row(
                "US", "USA", "United States", "English:United States;Spanish:Estados Unidos"
            ),
            country_row("IE", "IRL", "Ireland", "English:Ireland;Irish:\u00c9ire"),
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


def test_a_saved_location_is_placed_from_the_dataset(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Lisboa, Portugal")

    assert company.location_lat == 38.7167
    assert company.location_lon == -9.1333
    assert company.location_resolved_from == "Lisboa, Portugal"
    assert company.location_resolved_by == LocationSource.GUESSED
    assert company.location_resolved_at is not None


def test_a_location_the_dataset_does_not_place_is_not_an_error(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Remote")

    assert company.location_lat is None and company.location_lon is None
    assert company.location_resolved_from == "Remote"
    assert company.location_resolved_by == ""


def test_the_same_saved_again_is_not_a_new_guess(user, data_dir, monkeypatch):
    """The resolution runs when the location is saved, and a save of the same text is
    not a save of the location (#108)."""
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    assert company.location_resolved_by == LocationSource.GUESSED

    calls = []
    original = places.resolve

    def counting(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(places, "resolve", counting)
    company.name = "A renamed company"
    company.save()

    assert calls == []


def test_a_changed_location_is_guessed_again(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    company.location = "Lisboa, Portugal"
    company.save()

    assert company.location_resolved_from == "Lisboa, Portugal"
    assert company.location_lat == 38.7167
    assert company.location_resolved_by == LocationSource.GUESSED


def test_a_changed_location_that_places_nowhere_clears_the_old_guess(user, data_dir):
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    company.location = "Atlantis"
    company.save()

    assert company.location_lat is None and company.location_lon is None
    assert company.location_resolved_from == "Atlantis"
    assert company.location_resolved_by == ""


def test_a_correction_a_person_made_outlives_the_next_save(user, data_dir, monkeypatch):
    """The manual answer is not one the location would produce, and is not re-guessed."""
    company = Company.objects.create(owner=user, name="A company", location="Berlin")
    company.location_lat = 39.7817
    company.location_lon = -6.9052
    company.location_resolved_by = LocationSource.MANUAL
    company.save()

    calls = []
    monkeypatch.setattr(places, "resolve", lambda text: calls.append(text))
    company.name = "A renamed company"
    company.save()
    company.refresh_from_db()

    assert calls == []
    assert company.location_lon == -6.9052
    assert company.location_resolved_by == LocationSource.MANUAL
