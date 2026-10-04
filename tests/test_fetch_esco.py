"""The fetch_esco command: the harvest that writes the files the loader reads (#266).

The ESCO API itself is not called from a test: a suite that depends on somebody else's
server is not a suite. What is tested is what the command does with an answer: the
reshape of the API's search pages into the shapes the loader reads, the check before
anything is replaced, and the way a refusal is reported with the API's own answer.
"""

from __future__ import annotations

import importlib
import io
import json
import zipfile

import pytest
from django.core.management.base import CommandError

from postulo.jobs import esco
from postulo.jobs.management.commands import fetch_esco

#: The two queries the harvest makes, keyed in the table of pages the way the command
#: asks them: by the ISCO concept scheme, and by the class the API names for occupations.
ISCO_SCHEME = "http://data.europa.eu/esco/concept-scheme/isco"
OCCUPATION_TYPE = "occupation"
SKILL_TYPE = "skill"

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


SKILLS = [
    {
        "uri": "http://data.europa.eu/esco/skill/7111b95d-0ce3-441a-9d92-4c75d05c4388",
        # A no-break space at the end, as v1.2.1 has on twenty of its names, and one inside,
        # as Czech keeps a one-letter word off the end of a line with one.
        "preferredLabel": {"en": "project management ", "fr": "gestion de projets"},
    },
    {
        "uri": "http://data.europa.eu/esco/skill/21c5790c-0930-4d74-b3b0-84caf5af12ea",
        "preferredLabel": {"en": "manage budgets", "fr": "gérer les budgets"},
    },
    {
        "uri": "http://data.europa.eu/esco/skill/598de5b0-5b58-4ea7-8058-a4bc4d18c742",
        # No French name: an empty line in the French member, not a missing one.
        "preferredLabel": {"en": "SQL\nServer"},
    },
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
        (SKILL_TYPE, version, 0): Answer(page(SKILLS)),
    }


@pytest.fixture(autouse=True)
def fresh_classification():
    """The loader caches what it reads; a test that writes a file wants a fresh read."""
    esco.forget()
    yield
    esco.forget()


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
        monkeypatch.setattr(fetch_esco, "public_only_client", lambda **kw: client)
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
    assert (data_dir / "esco-skills-1.2.1.zip").exists()
    assert {request["selectedVersion"] for request in client.requests} == {"v1.2.1"}
    assert {request.get("isInScheme") or request["type"] for request in client.requests} == {
        ISCO_SCHEME,
        OCCUPATION_TYPE,
        SKILL_TYPE,
    }


def test_a_revision_that_is_absent_is_refused(data_dir, harvest):
    with pytest.raises(CommandError, match="--revision is required"):
        harvest(tables(), revision="")


def test_the_pages_are_followed_until_the_total_is_in(data_dir, harvest, monkeypatch):
    """``offset`` is a number of pages, which is how the API reads it: asked for page 2 of
    two-concept pages, it answers the third and fourth concepts (#266). Counted as concepts,
    the second request asked for page two and the harvest of a class larger than one page
    kept the first page of it and said nothing."""
    monkeypatch.setattr(fetch_esco, "PAGE_SIZE", 2)
    pages = {
        (ISCO_SCHEME, "v9.9.9", 0): Answer(page(GROUPS[:2], total=3)),
        (ISCO_SCHEME, "v9.9.9", 1): Answer(page(GROUPS[2:], total=3)),
        (OCCUPATION_TYPE, "v9.9.9", 0): Answer(page(OCCUPATIONS[:2], total=3)),
        (OCCUPATION_TYPE, "v9.9.9", 1): Answer(page(OCCUPATIONS[2:], total=3)),
        (SKILL_TYPE, "v9.9.9", 0): Answer(page(SKILLS[:2], total=3)),
        (SKILL_TYPE, "v9.9.9", 1): Answer(page(SKILLS[2:], total=3)),
    }

    client = harvest(pages)
    document = json.loads((data_dir / "esco-9.9.9.json").read_text(encoding="utf-8"))
    assert sorted(document["unit_groups"]) == ["2511", "2512"]
    assert sorted(document["occupations"]) == ["2511.1", "2512.1", "2512.1.1"]
    skills = zipfile.ZipFile(data_dir / "esco-skills-9.9.9.zip")
    assert len(skills.read(esco.SKILLS_IDENTIFIERS).decode().split("\n")) == 3
    assert [request["offset"] for request in client.requests] == [0, 1, 0, 1, 0, 1]


def test_a_harvest_that_stops_short_of_its_total_writes_nothing(data_dir, harvest):
    pages = {**tables(), (SKILL_TYPE, "v9.9.9", 0): Answer(page(SKILLS[:2], total=3))}
    pages[(SKILL_TYPE, "v9.9.9", 1)] = Answer(page([], total=3))

    with pytest.raises(CommandError, match="said 3 concepts"):
        harvest(pages)
    assert not list(data_dir.iterdir()), "neither half, not only the one that fell short"


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


# ------------------------------------------------------------------------- the skills


def test_the_skills_are_written_one_language_to_a_member(data_dir, harvest):
    harvest(tables())

    skills = zipfile.ZipFile(data_dir / "esco-skills-9.9.9.zip")
    about = json.loads(skills.read(esco.SKILLS_ABOUT))
    identifiers = skills.read(esco.SKILLS_IDENTIFIERS).decode().split("\n")
    english = skills.read(esco.SKILLS_NAMES.format(language="en")).decode().split("\n")
    french = skills.read(esco.SKILLS_NAMES.format(language="fr")).decode().split("\n")

    assert about["revision"] == "ESCO v9.9.9" and about["languages"] == ["en", "fr"]
    assert about["skills"] == 3 and about["licence"] == "EUPL 1.2"
    assert identifiers == sorted(identifiers)
    assert identifiers[0] == "21c5790c-0930-4d74-b3b0-84caf5af12ea"
    # Line for line with the identifiers; the ends of a name tidied and the inside kept.
    assert english == ["manage budgets", "SQL Server", "project management"]
    assert french == ["gérer les budgets", "", "gestion de projets"]
    assert sorted(skills.namelist()) == [
        "about.json",
        "identifiers.txt",
        "names/en.txt",
        "names/fr.txt",
    ]


def test_the_same_harvest_is_the_same_file(data_dir, harvest):
    harvest(tables())
    first = (data_dir / "esco-skills-9.9.9.zip").read_bytes()
    harvest(tables())

    assert (data_dir / "esco-skills-9.9.9.zip").read_bytes() == first


def test_a_skill_somewhere_else_is_refused(data_dir, harvest):
    stranger = {"uri": "https://example.org/skill/1", "preferredLabel": {"en": "a word"}}
    pages = {**tables(), (SKILL_TYPE, "v9.9.9", 0): Answer(page([*SKILLS, stranger]))}

    with pytest.raises(CommandError, match="have an identifier outside"):
        harvest(pages)
    assert not list(data_dir.iterdir())


def test_a_skill_with_no_english_name_is_refused(data_dir, harvest):
    unnamed = {
        "uri": "http://data.europa.eu/esco/skill/ffff0000-0000-4000-8000-000000000000",
        "preferredLabel": {"fr": "sans nom anglais"},
    }
    pages = {**tables(), (SKILL_TYPE, "v9.9.9", 0): Answer(page([*SKILLS, unnamed]))}

    with pytest.raises(CommandError, match="a skill has no English name"):
        harvest(pages)


def test_the_skills_the_command_writes_are_ones_the_loader_reads(data_dir, harvest, monkeypatch):
    harvest(tables())
    monkeypatch.setattr(esco, "DATA_DIR", data_dir)
    esco.forget()

    uri = esco.skill_for("Project Management", "fr")
    assert uri == esco.SKILL_NAMESPACE + "7111b95d-0ce3-441a-9d92-4c75d05c4388"
    assert esco.skill_name(uri, "fr") == "gestion de projets"
    assert esco.skill_for("gestion de projets", "fr") == uri, "spacing is folded to compare"
    assert esco.skill_suggestions("ma", "en") == ["manage budgets"]


def test_the_directory_can_be_moved_to_the_data_volume(monkeypatch, tmp_path):
    """#411: the image's source layer is root's, and the command runs as `postulo`."""
    monkeypatch.setenv("POSTULO_ESCO_DIR", str(tmp_path))
    try:
        assert importlib.reload(esco).DATA_DIR == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(esco)


def test_a_new_revision_leaves_only_the_new_two_files(data_dir, harvest):
    """The directory goes from one revision to one, so no read in between is refused (#535)."""
    (data_dir / "esco-1.2.1.json").write_text("{}", encoding="utf-8")
    (data_dir / "esco-skills-1.2.1.zip").write_bytes(b"")

    harvest(tables())

    assert sorted(p.name for p in data_dir.iterdir()) == [
        "esco-9.9.9.json",
        "esco-skills-9.9.9.zip",
    ]


def test_a_refused_run_keeps_the_previous_revision(data_dir, harvest):
    (data_dir / "esco-1.2.1.json").write_text("{}", encoding="utf-8")
    pages = {**tables(), (SKILL_TYPE, "v9.9.9", 0): Answer(page(SKILLS[:2], total=3))}
    pages[(SKILL_TYPE, "v9.9.9", 1)] = Answer(page([], total=3))

    with pytest.raises(CommandError, match="said 3 concepts"):
        harvest(pages)

    assert [p.name for p in data_dir.iterdir()] == ["esco-1.2.1.json"]


@pytest.mark.parametrize(
    ("names", "check_id"),
    [
        (("esco-1.2.1.json", "esco-1.2.2.json"), "postulo.E040"),
        (("esco-skills-1.2.1.zip", "esco-skills-1.2.2.zip"), "postulo.E041"),
    ],
)
def test_the_system_check_reports_two_revisions(tmp_path, monkeypatch, names, check_id):
    from postulo.jobs import checks

    monkeypatch.setattr(esco, "DATA_DIR", tmp_path)
    assert checks.esco_one_revision(None) == []
    for name in names:
        (tmp_path / name).write_bytes(b"")

    assert [error.id for error in checks.esco_one_revision(None)] == [check_id]
