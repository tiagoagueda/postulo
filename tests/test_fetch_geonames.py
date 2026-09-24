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


def country_row(iso: str, name: str, languages: str) -> str:
    return "\t".join(
        [
            iso,
            iso * 3,
            iso[:2],
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
        country_row("DE", "Germany", "English:Germany;German:Deutschland"),
        country_row("PT", "Portugal", "English:Portugal;Portuguese:Portugal"),
        country_row("JP", "Japan", "English:Japan"),
        country_row("KE", "Kenya", "English:Kenya"),
        country_row("BR", "Brazil", "English:Brazil;Portuguese:Brasil"),
    ]
    rows.extend(
        country_row(f"XX{number:02d}", f"A country {number}", "English:A country")
        for number in range(1, 200)
    )
    return "\n".join(rows) + "\n"


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


@pytest.fixture(autouse=True)
def fresh_loader():
    for cached in (places._index, places._countries):
        cached.cache_clear()
    yield
    for cached in (places._index, places._countries):
        cached.cache_clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def run(monkeypatch, data_dir):
    """A run of the command against a table of answers, the checks at their real size."""

    def perform(answers):
        client = FakeClient(answers)
        monkeypatch.setattr(fetch_geonames.httpx, "Client", lambda **kw: client)
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

    assert (data_dir / places.CITIES_FILE).is_file()
    assert (data_dir / places.COUNTRIES_FILE).is_file()
    assert client.requests == [fetch_geonames.CITIES_URL, fetch_geonames.COUNTRIES_URL]


def test_the_file_the_command_writes_is_one_the_loader_reads(run, data_dir):
    run(answers_for())

    assert places.available() is True
    assert places.resolve("Lisbon") is not None
    assert places.resolve("Lisbon")["country"] == "PT"
    assert places.resolve("Springfield") is None


def test_the_download_identifies_itself_the_way_the_policy_asks(monkeypatch, data_dir):
    """A real User-Agent is the term the provider's policy sets, and it is carried."""
    seen = {}
    inner = FakeClient(answers_for())

    def factory(**kwargs):
        seen["kwargs"] = kwargs
        return inner

    monkeypatch.setattr(fetch_geonames.httpx, "Client", factory)
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
