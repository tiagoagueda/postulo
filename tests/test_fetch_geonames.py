"""The fetch_geonames command: the download that writes the tables the loader reads (#108).

GeoNames itself is not called from a test: a suite that depends on somebody else's
server is not a suite. What is tested is what the command does with an answer: the
check before anything is replaced, and the way a refusal is reported.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.management.base import CommandError

from postulo.jobs import places
from postulo.jobs.management.commands import fetch_geonames

pytestmark = pytest.mark.usefixtures("cldr_in_hand")


def table_row(name: str, country: str, population: int, alternates: str = "") -> str:
    return "\t".join(
        [
            "1",
            name,
            name,
            alternates,
            "0.0",
            "0.0",
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
            "UTC",
            "2026-01-01",
        ]
    )


def country_row(iso: str, name: str, capital: str = "") -> str:
    """One row of countryInfo.txt, in its real columns (the name is the fifth)."""
    return "\t".join(
        [
            *[iso, iso * 3, "000", iso[:2], name, capital or name, "0", "0", "EU"],
            *["." + iso.lower(), "EUR", "Euro", "0", "#####", "^(\\d{5})$", "en", "1", "", ""],
        ]
    )


def city_table() -> str:
    """A table the check passes: the required cities in, twenty thousand rows deep."""
    rows = [
        table_row("Berlin", "DE", 3644832),
        table_row("Lisboa", "PT", 547625, alternates="Lisbon"),
        table_row("Tokyo", "JP", 37991232),
        table_row("Nairobi", "KE", 4398720),
        table_row("São Paulo", "BR", 12325232),
    ]
    rows.extend(table_row(f"A city {number}", "DE", 1000) for number in range(1, 10000))
    return "\n".join(rows) + "\n"


def country_table() -> str:
    rows = [
        country_row("DE", "Germany", "Berlin"),
        country_row("PT", "Portugal", "Lisbon"),
        country_row("JP", "Japan", "Tokyo"),
        country_row("KE", "Kenya", "Nairobi"),
        country_row("BR", "Brazil", "Brasilia"),
    ]
    rows.extend(country_row(f"XX{number:02d}", f"A country {number}") for number in range(1, 200))
    return "#ISO\tISO3\tISO-Numeric\n" + "\n".join(rows) + "\n"


def zip_of(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("cities1000.txt", text)
    return buffer.getvalue()


class Answer:
    """A response the command can read: a status, a body."""

    def __init__(self, content, status=200, text=""):
        self.content = content
        self.status_code = status
        self.text = text or str(content)


class FakeClient:
    """The client the command talks to, answered from a table of URLs."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *excinfo):
        return False

    def get(self, url):
        self.requests.append(url)
        return self.answers[url]

    def stream(self, method, url, headers=None):
        """What the command asks for now: the answer streamed, as httpx hands one over."""
        import contextlib

        import httpx

        self.requests.append(url)
        answer = self.answers[url]

        @contextlib.contextmanager
        def opened():
            yield httpx.Response(answer.status_code, content=answer.content)

        return opened()


@pytest.fixture(autouse=True)
def fresh_loader():
    places._index.cache_clear()
    yield
    places._index.cache_clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def run(monkeypatch, data_dir):
    """A run of the command against a table of answers, the checks at their real size."""

    def perform(answers):
        client = FakeClient(answers)
        monkeypatch.setattr(fetch_geonames, "public_only_client", lambda **kw: client)
        command = fetch_geonames.Command()
        command.stdout = io.StringIO()
        command.stderr = io.StringIO()
        command.handle()
        return client

    return perform


def answers_for(cities=None, countries=None):
    if cities is None:
        cities = city_table()
    if countries is None:
        countries = country_table()
    return {
        fetch_geonames.CITIES_URL: Answer(zip_of(cities)),
        fetch_geonames.COUNTRIES_URL: Answer(countries.encode("utf-8"), text=countries),
    }


def test_the_download_is_checked_and_written_where_the_loader_reads_it(run, data_dir):
    client = run(answers_for())

    assert (data_dir / places.COUNTRIES_FILE).is_file()
    assert (data_dir / places.LOOKUP_FILE).is_file()
    assert client.requests == [fetch_geonames.CITIES_URL, fetch_geonames.COUNTRIES_URL]


def test_the_city_table_is_removed_once_its_lookup_is_built(run, data_dir):
    """The downloaded city table was kept beside the lookup built from it, 32 MB on every
    data volume that nothing read again (#1127). What a fetch leaves is the lookup and the
    small country table, and the lookup alone is the dataset: the map says it is there and
    a location is placed from it."""
    run(answers_for())

    assert sorted(path.name for path in data_dir.iterdir()) == sorted(
        [places.LOOKUP_FILE, places.COUNTRIES_FILE]
    )
    beside = sum(
        path.stat().st_size for path in data_dir.iterdir() if path.name != places.LOOKUP_FILE
    )
    assert beside < 100_000, "the country table and nothing else"
    assert places.available() is True
    assert places.cities() == 10004
    assert places.resolve("Lisbon, Portogallo")["country"] == "PT"


def test_a_second_download_is_built_and_removed_like_the_first(run, data_dir):
    """Running the command again is how a lookup is brought up to date, and there is no
    table beside it to build from: the table is downloaded, indexed and removed again
    (#1127)."""
    run(answers_for())
    more = city_table() + table_row("Porto", "PT", 242722) + "\n"

    run(answers_for(cities=more))

    assert not (data_dir / places.CITIES_FILE).exists()
    assert places.resolve("Porto")["country"] == "PT"


def test_a_table_put_there_by_hand_is_replaced_by_the_download(run, data_dir):
    """The command has always written over the table it finds; what it then removes is the
    one it downloaded (#1127)."""
    (data_dir / places.CITIES_FILE).write_text(table_row("Atlantis", "DE", 5000) + "\n", "utf-8")

    run(answers_for())

    assert not (data_dir / places.CITIES_FILE).exists()
    assert places.resolve("Atlantis") is None
    assert places.resolve("Berlin")["country"] == "DE"


def test_a_build_that_fails_leaves_the_table_and_the_lookup_before_it(run, data_dir, monkeypatch):
    """The table is removed after its lookup is built and has answered, never before: a
    build that stops half way leaves the downloaded table, and the lookup of the download
    before it, so the next start indexes the table itself (#1127)."""
    run(answers_for())
    before = (data_dir / places.LOOKUP_FILE).read_bytes()
    real = places.build_lookup

    def full_disk(cities, target, countries=None):
        if target == data_dir / places.LOOKUP_FILE:
            raise OSError("No space left on device")
        return real(cities, target, countries)

    monkeypatch.setattr(places, "build_lookup", full_disk)
    more = city_table() + table_row("Porto", "PT", 242722) + "\n"
    with pytest.raises(OSError, match="No space left"):
        run(answers_for(cities=more))

    assert (data_dir / places.CITIES_FILE).is_file()
    assert (data_dir / places.LOOKUP_FILE).read_bytes() == before
    monkeypatch.setattr(places, "build_lookup", real)
    places._index.cache_clear()
    assert places.resolve("Porto")["country"] == "PT", "the table left there is indexed"
    assert (data_dir / places.CITIES_FILE).is_file()


def test_a_lookup_that_does_not_answer_leaves_the_table_beside_it(run, data_dir, monkeypatch):
    """The lookup is asked what the download was asked before the table is removed: one
    that lacks a city the table held is said so, and the table stays (#1127)."""
    real = places.build_lookup

    def short(cities, target, countries=None):
        if target != data_dir / places.LOOKUP_FILE:
            return real(cities, target, countries)
        cut = cities.with_name("cut.txt")
        rows = cities.read_text(encoding="utf-8").splitlines()
        cut.write_text("\n".join(rows[2:]) + "\n", encoding="utf-8")
        try:
            return real(cut, target, cities.with_name(places.COUNTRIES_FILE))
        finally:
            cut.unlink()

    monkeypatch.setattr(places, "build_lookup", short)
    with pytest.raises(CommandError, match="Lisbon is not in the lookup"):
        run(answers_for())

    assert (data_dir / places.CITIES_FILE).is_file()


def postcode_zip() -> bytes:
    """The postal codes as GeoNames zips them: the codes the check asks for, and enough
    others to be the table."""
    rows = [
        "DE\t10115\tBerlin\t\t\t\t\t\t\t52.53\t13.38\t6",
        "PT\t1000-001\tLisboa\t\t\t\t\t\t\t38.72\t-9.15\t6",
        "FR\t75001\tParis\t\t\t\t\t\t\t48.86\t2.34\t6",
    ]
    rows.extend(f"DE\t{number:05d}\tA place\t\t\t\t\t\t\t50.0\t10.0\t6" for number in range(500))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(fetch_geonames.POSTCODES_MEMBER, "\n".join(rows) + "\n")
    return buffer.getvalue()


@pytest.fixture
def run_with_postcodes(monkeypatch, data_dir):
    """A run that also asks for the postal codes, against a table of a few hundred."""

    def perform():
        answers = {**answers_for(), fetch_geonames.POSTCODES_URL: Answer(postcode_zip())}
        monkeypatch.setattr(fetch_geonames, "MINIMUM_POSTCODES", 500)
        monkeypatch.setattr(fetch_geonames, "public_only_client", lambda **kw: FakeClient(answers))
        command = fetch_geonames.Command()
        command.stdout = io.StringIO()
        command.stderr = io.StringIO()
        command.handle(postcodes=True)

    return perform


def test_the_postal_code_table_is_removed_once_its_lookup_is_built(run_with_postcodes, data_dir):
    """The postal codes' table was kept beside its lookup too, and nothing ever read it
    again (#1127). The lookup is what an address is placed from."""
    run_with_postcodes()

    assert sorted(path.name for path in data_dir.iterdir()) == sorted(
        [places.LOOKUP_FILE, places.COUNTRIES_FILE, places.POSTCODES_LOOKUP_FILE]
    )
    assert places.postcodes_available() is True
    assert places.resolve_postcode("1000-001", "PT") == {"lat": 38.72, "lon": -9.15}


def test_a_postal_code_lookup_that_fails_to_build_leaves_its_table(
    run_with_postcodes, data_dir, monkeypatch
):
    """As the cities: the table goes after the lookup in place has answered (#1127)."""
    real = places.build_postcode_lookup

    def full_disk(source, target):
        if target == data_dir / places.POSTCODES_LOOKUP_FILE:
            raise OSError("No space left on device")
        return real(source, target)

    monkeypatch.setattr(places, "build_postcode_lookup", full_disk)
    with pytest.raises(OSError, match="No space left"):
        run_with_postcodes()

    assert (data_dir / places.POSTCODES_FILE).is_file()
    assert not (data_dir / places.CITIES_FILE).exists(), "the cities were done before"


def test_the_file_the_command_writes_is_one_the_loader_reads(run, data_dir):
    run(answers_for())

    assert places.available() is True
    assert places.resolve("Lisbon") is not None
    assert places.resolve("Lisbon")["country"] == "PT"
    assert places.resolve("Springfield") is None


def test_the_command_reads_the_countries_names_into_the_lookup(run, data_dir, monkeypatch):
    """Provisioning is where the countries' names are read out of CLDR, in every language
    Postulo has, and stamped with the CLDR they came from (#1109). A match afterwards reads
    the lookup, and CLDR not at all."""
    run(answers_for())

    assert places.lookup_stamp() == places.current_stamp()

    def refuse(known):
        raise AssertionError("CLDR was read to match a country")

    monkeypatch.setattr(places, "_translated_countries", refuse)
    assert places.resolve("Berlin, Deutschland")["country"] == "DE"
    assert places.resolve("Lisbon, Portogallo")["country"] == "PT"
    assert places.resolve("Lisbon, Alemanha") is None


def test_the_download_identifies_itself_the_way_the_policy_asks(monkeypatch, data_dir):
    """A real User-Agent is the term the provider's policy sets, and it is carried."""
    seen = {}
    inner = FakeClient(answers_for())

    def factory(**kwargs):
        seen["kwargs"] = kwargs
        return inner

    monkeypatch.setattr(fetch_geonames, "public_only_client", factory)
    command = fetch_geonames.Command()
    command.stdout = io.StringIO()
    command.stderr = io.StringIO()
    command.handle()

    assert seen["kwargs"]["headers"]["User-Agent"].startswith("postulo fetch_geonames")


def test_a_refusal_is_reported_with_the_answer(run, data_dir):
    with pytest.raises(CommandError, match="answered 404"):
        run({**answers_for(), fetch_geonames.CITIES_URL: Answer(b"", status=404)})
    assert not (data_dir / places.CITIES_FILE).exists()


def test_an_answer_that_is_not_the_zip_is_not_the_table(run, data_dir):
    with pytest.raises(CommandError, match="not the zip"):
        run({**answers_for(), fetch_geonames.CITIES_URL: Answer(b"not a zip at all")})


def test_a_table_without_the_depth_is_not_the_table(run, data_dir):
    shallow = "\n".join(table_row(f"A city {number}", "DE", 1000) for number in range(10)) + "\n"
    with pytest.raises(CommandError, match="not the table of the cities"):
        run(answers_for(cities=shallow))
    assert not (data_dir / places.CITIES_FILE).exists()


def test_a_table_missing_a_required_city_is_not_the_table(run, data_dir):
    """The loader is asked about the cities it would be useless without, before anything
    is replaced (#108)."""
    rows = [
        table_row("Berlin", "DE", 3644832),
        table_row("Tokyo", "JP", 37991232),
        table_row("Nairobi", "KE", 4398720),
        table_row("São Paulo", "BR", 12325232),
    ]
    rows.extend(table_row(f"A city {number}", "DE", 1000) for number in range(1, 10000))
    with pytest.raises(CommandError, match="Lisbon is not in the table"):
        run(answers_for(cities="\n".join(rows) + "\n"))
    assert not (data_dir / places.CITIES_FILE).exists()


def test_the_download_goes_through_the_guarded_client(monkeypatch, data_dir):
    """Rule 5 of the threat model: an outbound request is made by `postulo.plugins.http`,
    which refuses private addresses, redirects included (#366)."""
    built = []

    def spy(**kwargs):
        built.append(kwargs)
        return FakeClient(answers_for())

    monkeypatch.setattr(fetch_geonames, "public_only_client", spy)
    command = fetch_geonames.Command()
    command.stdout = io.StringIO()
    command.handle()
    assert len(built) == 1


def test_the_real_client_is_the_guarded_one(monkeypatch, data_dir):
    """Unpatched, the command's client carries the destination guard as a request hook."""
    from postulo.plugins import http

    seen = {}

    def fake_build(guard, timeout, kwargs, **options):
        seen["guard"] = guard
        return FakeClient(answers_for())

    monkeypatch.setattr(http, "_build", fake_build)
    command = fetch_geonames.Command()
    command.stdout = io.StringIO()
    command.handle()
    assert seen["guard"] is http._public_only


def test_the_tables_are_written_into_a_directory_that_does_not_exist_yet(tmp_path):
    target = tmp_path / "maps" / "deeper" / places.CITIES_FILE
    fetch_geonames.Command()._write(target, "table")
    assert target.read_text(encoding="utf-8") == "table"
