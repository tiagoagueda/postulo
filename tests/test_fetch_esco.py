"""The fetch_esco command: the harvest that writes the file the loader reads (#266).

The ESCO API itself is not called from a test: a suite that depends on somebody else's
server is not a suite. What is tested is what the command does with an answer: the
reshape of the API's search pages into the shape the loader reads, the check before
anything is replaced, and the way a refusal is reported with the API's own answer.
"""

from __future__ import annotations

import io
import json

import pytest
from django.core.management.base import CommandError

from postulo.jobs import esco
from postulo.jobs.management.commands import fetch_esco

#: The two queries the harvest makes, keyed in the table of pages the way the command
#: asks them: by the ISCO concept scheme, and by the class the API names for occupations.
ISCO_SCHEME = "http://data.europa.eu/esco/concept-scheme/isco"
OCCUPATION_TYPE = "occupation"

GROUPS = [
    # The scheme carries the majors and the sub-majors beside the unit groups; the
    # harvest keeps the four-digit codes, and the rest is not a unit group.
    {
        "uri": "http://data.europa.eu/esco/isco/C25",
        "code": "25",
        "preferredLabel": {
            "en": "Information and communications technology service occupations",
            "fr": "Activités de services des technologies de l'information et de la communication",
        },
    },
    {
        "uri": "http://data.europa.eu/esco/isco/C2511",
        "code": "2511",
        "preferredLabel": {
            "en": "Computer associate occupations",
            "fr": "Associés en informatique",
        },
    },
    {
        "uri": "http://data.europa.eu/esco/isco/C2512",
        "code": "2512",
        "preferredLabel": {"en": "Software developers", "fr": "Concepteurs de logiciels"},
    },
]
OCCUPATIONS = [
    {
        "uri": "http://data.europa.eu/esco/occupation/2511-1",
        "code": "2511.1",
        "preferredLabel": {"en": "Chief executive", "fr": "Directeur général"},
    },
    {
        "uri": "http://data.europa.eu/esco/occupation/2512-1",
        "code": "2512.1",
        "preferredLabel": {"en": "Web developer", "fr": "Développeur web"},
    },
    {
        "uri": "http://data.europa.eu/esco/occupation/2512-1-1",
        "code": "2512.1.1",
        "preferredLabel": {"en": "Software developer", "fr": "Développeur logiciel"},
    },
]
MINIMAL_GROUPS = [
    {
        "uri": "http://data.europa.eu/esco/isco/C2512",
        "code": "2512",
        "preferredLabel": {"en": "Software developers"},
    }
]
MINIMAL_OCCUPATIONS = [
    {
        "uri": "http://data.europa.eu/esco/occupation/2512-1",
        "code": "2512.1",
        "preferredLabel": {"en": "Web developer"},
    }
]


def page(items, total=None):
    """One search page, in the shape the API's HAL search answer has."""
    return {
        "total": len(items) if total is None else total,
        "offset": 0,
        "limit": fetch_esco.PAGE_SIZE,
        "_links": {},
        "_embedded": {"results": items},
    }


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
    """The client the command talks to, answered from a table of pages keyed by query."""

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *excinfo):
        return False

    def get(self, url, params):
        self.requests.append(params)
        key = (
            params.get("isInScheme") or params.get("type"),
            params.get("selectedVersion"),
            params.get("offset", 0),
        )
        return self.pages[key]


def tables(version="v9.9.9"):
    return {
        (ISCO_SCHEME, version, 0): Answer(page(GROUPS)),
        (OCCUPATION_TYPE, version, 0): Answer(page(OCCUPATIONS)),
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
    """A run of the command against a table of pages, in two languages only."""

    def run(pages_to_serve, revision="9.9.9"):
        client = FakeClient(pages_to_serve)
        monkeypatch.setattr(fetch_esco.httpx, "Client", lambda **kw: client)
        monkeypatch.setattr(fetch_esco, "LANGUAGES", ("en", "fr"))
        command = fetch_esco.Command()
        command.stdout = io.StringIO()
        command.stderr = io.StringIO()
        command.handle(revision=revision)
        return client

    return run


def test_the_harvest_is_reshaped_to_the_shape_the_loader_reads(data_dir, harvest):
    harvest(tables())

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
    # The scheme's two-digit sub-major is not a unit group, and so is not in the file.
    assert "25" not in document["unit_groups"]
    assert document["unit_groups"]["2512"] == {
        "major": "2",
        "names": {"en": "Software developers", "fr": "Concepteurs de logiciels"},
    }
    occupation = document["occupations"]["2512.1.1"]
    assert occupation["isco"] == "2512"
    assert occupation["names"]["fr"] == "Développeur logiciel"


def test_the_occupations_are_indexed_within_their_unit_group(data_dir, harvest):
    harvest(tables())

    document = json.loads((data_dir / "esco-9.9.9.json").read_text(encoding="utf-8"))
    assert sorted(document["occupations"]) == ["2511.1", "2512.1", "2512.1.1"]


def test_an_occupation_with_no_code_is_reported(data_dir, harvest):
    uncoded = [
        {"uri": "http://data.europa.eu/esco/occupation/no-code", "preferredLabel": {"en": "A word"}}
    ]
    pages = {
        (ISCO_SCHEME, "v9.9.9", 0): Answer(page(MINIMAL_GROUPS)),
        (OCCUPATION_TYPE, "v9.9.9", 0): Answer(page(uncoded)),
    }

    with pytest.raises(CommandError, match="carried no ISCO-08 code"):
        harvest(pages)


def test_a_refusal_is_reported_with_the_apis_answer(data_dir, harvest):
    refusal = {
        "logref": "BadInputException",
        "status": 400,
        "message": "Illegal/Unknown type parameter values: ('occupation')",
        "_links": None,
    }
    pages = {
        (ISCO_SCHEME, "v9.9.9", 0): Answer(page(MINIMAL_GROUPS)),
        (OCCUPATION_TYPE, "v9.9.9", 0): Answer(refusal, status=400),
    }

    with pytest.raises(CommandError, match="answered 400"):
        harvest(pages)


def test_the_revision_is_asked_with_the_prefix_the_api_wants(data_dir, harvest):
    client = harvest(tables("v1.2.1"), revision="1.2.1")

    assert (data_dir / "esco-1.2.1.json").exists()
    assert {request["selectedVersion"] for request in client.requests} == {"v1.2.1"}
    assert {request.get("isInScheme") or request["type"] for request in client.requests} == {
        ISCO_SCHEME,
        OCCUPATION_TYPE,
    }


def test_a_revision_that_is_absent_is_refused(data_dir, harvest):
    with pytest.raises(CommandError, match="--revision is required"):
        harvest(tables(), revision="")


def test_the_pages_are_followed_until_the_total_is_in(data_dir, harvest, monkeypatch):
    monkeypatch.setattr(fetch_esco, "PAGE_SIZE", 2)
    pages = {
        (ISCO_SCHEME, "v9.9.9", 0): Answer(page(GROUPS[:2], total=3)),
        (ISCO_SCHEME, "v9.9.9", 2): Answer(page(GROUPS[2:])),
        (OCCUPATION_TYPE, "v9.9.9", 0): Answer(page(OCCUPATIONS[:2], total=3)),
        (OCCUPATION_TYPE, "v9.9.9", 2): Answer(page(OCCUPATIONS[2:])),
    }

    harvest(pages)
    document = json.loads((data_dir / "esco-9.9.9.json").read_text(encoding="utf-8"))
    assert sorted(document["unit_groups"]) == ["2511", "2512"]
    assert sorted(document["occupations"]) == ["2511.1", "2512.1", "2512.1.1"]


def test_the_file_the_command_writes_is_one_the_loader_reads(data_dir, harvest, monkeypatch):
    harvest(tables())
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
