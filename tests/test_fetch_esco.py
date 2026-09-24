"""The fetch_esco command: the harvest that writes the file the loader reads (#266).

The ESCO API itself is not called from a test: a suite that depends on somebody else's
server is not a suite. What is tested is what the command does with an answer: the
reshape into the shape the loader reads, the check before anything is replaced, and the
way a refusal is reported with the API's own answer.
"""

from __future__ import annotations

import io
import json

import pytest
from django.core.management.base import CommandError

from postulo.jobs import esco
from postulo.jobs.management.commands import fetch_esco

#: The two classes the command harvests, keyed in the table of answers.
GROUPS = "http://data.europa.eu/esco/model#ISCO-08UnitGroup"
OCCUPATIONS = "http://data.europa.eu/esco/model#Occupation"
GROUPS_FALLBACK = "http://data.europa.eu/esco/model#UnitGroup"

GROUPS_EN = [
    {"@id": "https://x/esco/isco#C2511", "skos:prefLabel": "Computer associate occupations"},
    {"@id": "https://x/esco/isco#C2512", "skos:prefLabel": "Software developers"},
]
GROUPS_FR = [
    {"@id": "https://x/esco/isco#C2511", "skos:prefLabel": "Associés en informatique"},
    {"@id": "https://x/esco/isco#C2512", "skos:prefLabel": "Concepteurs de logiciels"},
]
OCCUPATIONS_EN = [
    {"@id": "https://x/esco/occupation#2511.1", "skos:prefLabel": "Chief executive"},
    {"@id": "https://x/esco/occupation#2512.1", "skos:prefLabel": "Web developer"},
    {"@id": "https://x/esco/occupation#2512.2", "skos:prefLabel": "Software developer"},
]
OCCUPATIONS_FR = [
    {"@id": "https://x/esco/occupation#2511.1", "skos:prefLabel": "Directeur général"},
    {"@id": "https://x/esco/occupation#2512.1", "skos:prefLabel": "Développeur web"},
    {"@id": "https://x/esco/occupation#2512.2", "skos:prefLabel": "Développeur logiciel"},
]
MINIMAL_GROUPS = [{"@id": "https://x/esco/isco#C2512", "skos:prefLabel": "Software developers"}]
MINIMAL_OCCUPATIONS = [
    {"@id": "https://x/esco/occupation#2512.1", "skos:prefLabel": "Web developer"}
]


class Answer:
    """A response the command can read: a status, a body and a few headers."""

    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeClient:
    """The client the command talks to, answered from a table keyed by class and language."""

    def __init__(self, tables):
        self.tables = tables

    def __enter__(self):
        return self

    def __exit__(self, *excinfo):
        return False

    def get(self, url, params):
        return self.tables[(params.get("concepttype"), params["lang"])]


def tables(groups_en, groups_fr, occupations_en, occupations_fr):
    return {
        (GROUPS, "en"): Answer(groups_en),
        (GROUPS, "fr"): Answer(groups_fr),
        (OCCUPATIONS, "en"): Answer(occupations_en),
        (OCCUPATIONS, "fr"): Answer(occupations_fr),
    }


@pytest.fixture(autouse=True)
def fresh_classification():
    """The loader caches what it reads; a test that writes a file wants a fresh read."""
    for cached in (esco.classification, esco._occupations_by_name, esco._unit_groups_by_name):
        cached.cache_clear()
    yield
    for cached in (esco.classification, esco._occupations_by_name, esco._unit_groups_by_name):
        cached.cache_clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Where the command writes, pointed at a directory the test owns."""
    monkeypatch.setattr(fetch_esco, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def harvest(monkeypatch):
    """A run of the command against a table of answers, in two languages only."""

    def run(tables_to_serve, revision="9.9.9"):
        monkeypatch.setattr(fetch_esco.httpx, "Client", lambda **kw: FakeClient(tables_to_serve))
        monkeypatch.setattr(fetch_esco, "LANGUAGES", ("en", "fr"))
        monkeypatch.setattr(fetch_esco, "DELAY", 0.0)
        command = fetch_esco.Command()
        command.stdout = io.StringIO()
        command.stderr = io.StringIO()
        command.handle(revision=revision)

    return run


def test_the_harvest_is_reshaped_to_the_shape_the_loader_reads(data_dir, harvest):
    harvest(tables(GROUPS_EN, GROUPS_FR, OCCUPATIONS_EN, OCCUPATIONS_FR))

    path = data_dir / "esco-9.9.9.json"
    assert path.exists()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert list(document) == [
        "revision",
        "source",
        "publisher",
        "licence",
        "languages",
        "unit_groups",
        "occupations",
    ]
    assert document["revision"] == "ESCO v9.9.9"
    assert document["languages"] == ["en", "fr"]
    assert document["unit_groups"]["2512"] == {
        "major": "2",
        "names": {"en": "Software developers", "fr": "Concepteurs de logiciels"},
    }
    occupation = document["occupations"]["2512.2"]
    assert occupation["isco"] == "2512"
    assert occupation["names"]["fr"] == "Développeur logiciel"


def test_the_occupations_are_indexed_within_their_unit_group(data_dir, harvest):
    harvest(tables(GROUPS_EN, GROUPS_FR, OCCUPATIONS_EN, OCCUPATIONS_FR))

    document = json.loads((data_dir / "esco-9.9.9.json").read_text(encoding="utf-8"))
    assert sorted(document["occupations"]) == ["2511.1", "2512.1", "2512.2"]


def test_an_occupation_with_no_code_is_reported(data_dir, harvest):
    uncoded = [{"@id": "https://x/esco/occupation#unclassifiable", "skos:prefLabel": "A word"}]

    with pytest.raises(CommandError, match="carried no ISCO-08 code"):
        harvest(tables(GROUPS_EN, GROUPS_FR, uncoded, []))


def test_a_refusal_is_reported_with_the_apis_answer(data_dir, harvest):
    rejected = {
        (GROUPS, "en"): Answer({"detail": "unknown concept type"}, status=400),
        (GROUPS, "fr"): Answer([], status=200),
        (GROUPS_FALLBACK, "en"): Answer({"detail": "unknown concept type"}, status=400),
        (GROUPS_FALLBACK, "fr"): Answer([], status=200),
        (OCCUPATIONS, "en"): Answer([], status=200),
        (OCCUPATIONS, "fr"): Answer([], status=200),
    }

    with pytest.raises(CommandError, match="answered 400"):
        harvest(rejected)


def test_the_revision_the_api_serves_names_the_file(data_dir, harvest):
    headers = {"content-version": "2.0.0"}
    served = {
        (GROUPS, "en"): Answer(MINIMAL_GROUPS, headers=headers),
        (GROUPS, "fr"): Answer([]),
        (OCCUPATIONS, "en"): Answer(MINIMAL_OCCUPATIONS, headers=headers),
        (OCCUPATIONS, "fr"): Answer([]),
    }

    harvest(served, revision="")
    assert (data_dir / "esco-2.0.0.json").exists()


def test_the_file_the_command_writes_is_one_the_loader_reads(data_dir, harvest, monkeypatch):
    harvest(tables(GROUPS_EN, GROUPS_FR, OCCUPATIONS_EN, OCCUPATIONS_FR))
    monkeypatch.setattr(esco, "DATA_DIR", data_dir)

    assert esco.revision() == "ESCO v9.9.9"
    assert esco.code_for("Web developer", "en") == "2512"
    assert esco.code_for("Concepteurs de logiciels", "fr") == "2512"
    assert esco.name_for("2512", "fr") == "Concepteurs de logiciels"


def test_data_file_reports_its_states(tmp_path, monkeypatch):
    monkeypatch.setattr(esco, "DATA_DIR", tmp_path)

    assert esco.data_file() is None
    (tmp_path / "esco-1.2.1.json").write_text("{}", encoding="utf-8")
    assert esco.data_file().name == "esco-1.2.1.json"
    (tmp_path / "esco-2.0.0.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="two ESCO classifications"):
        esco.data_file()


def test_the_absent_document_is_the_file_shape_with_nothing_in_it():
    assert set(esco.ABSENT) == {
        "revision",
        "source",
        "publisher",
        "licence",
        "languages",
        "unit_groups",
        "occupations",
    }
    assert esco.ABSENT["revision"] == ""
    assert esco.ABSENT["languages"] == []
    assert esco.ABSENT["unit_groups"] == {} and esco.ABSENT["occupations"] == {}
