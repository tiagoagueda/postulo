"""JSON Resume, read in: the bundled importer and its round trip with the export (#1260).

Three fixtures under `tests/data/json-resume/`, all made up for *Alex Morgan* at companies
under `example.org`: one with the classic fields, one with what 1.4.0 to 1.7.1 added (a
location as an object, an expiry date, a relationship, `custom`, `objective`), and one with
values of the wrong kind. The two good ones are held to their published schema first, so a
test that reads them is reading a real JSON Resume and not this reader's idea of one.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import fastjsonschema
import pytest
from django.urls import reverse

from postulo.documents import jsonresume, structured
from postulo.plugins import base, registry
from postulo.plugins.json_resume import JsonResumeImporter
from postulo.plugins.testing import assert_is_an_importer
from postulo.resume import importing
from tests.test_cv_structured_formats import cv, person  # noqa: F401

pytestmark = pytest.mark.django_db

DATA = Path(__file__).parent / "data" / "json-resume"
CLASSIC = DATA / "resume-1.0.0.json"
CURRENT = DATA / "resume-1.7.1.json"
ODD = DATA / "resume-odd.json"


def read(path: Path):
    return JsonResumeImporter().read(path.read_bytes())


def _schema(name: str):
    return fastjsonschema.compile(json.loads((DATA / name).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ the fixtures


def test_the_classic_fixture_is_a_json_resume():
    """The schema Postulo's export is written to (1.2.1) takes the classic fields."""
    _schema("schema.json")(json.loads(CLASSIC.read_text(encoding="utf-8")))


def test_the_current_fixture_is_a_json_resume_of_1_7_1():
    _schema("schema-1.7.1.json")(json.loads(CURRENT.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ the contract


def test_it_is_an_importer():
    assert_is_an_importer(
        JsonResumeImporter(),
        samples=[CLASSIC.read_bytes(), CURRENT.read_bytes(), ODD.read_bytes()],
        not_samples=[
            b'{"SkillsPassport": {"LearnerInfo": {}}}',
            b'{"postulo": {"format": 23}, "resume": {}}',
            b'{"name": "a JSON file about something else"}',
            b"[1, 2, 3]",
            b"%PDF-1.7",
        ],
    )


def test_it_ships_on_and_says_what_it_is():
    plugin = registry.find_any("json-resume")
    assert isinstance(plugin, JsonResumeImporter)
    assert str(base.label_of(plugin)) == "JSON Resume import"
    assert plugin.accepts == (".json", "application/json")
    assert [p.name for p in registry.plugins("importer")][:2] == ["europass", "json-resume"]


@pytest.mark.parametrize(
    "data",
    [
        b'{"basics": {"name": "Alex"}}',
        b'{"$schema": "https://jsonresume.org/schema", "meta": {}}',
        b'{"work": [], "education": []}',
        # Cut short and still plainly a resume: `read` says it is not readable.
        b'{"basics": {"name": "Al',
    ],
    ids=["basics", "schema", "two-sections", "cut-short"],
)
def test_what_it_claims(data: bytes):
    assert JsonResumeImporter().can_handle(data, "whatever.dat")


def test_a_file_cut_short_is_refused_in_words():
    with pytest.raises(base.ImportRefused, match="not readable JSON"):
        JsonResumeImporter().read(b'{"basics": {"name": "Al')


def test_nan_is_not_json():
    with pytest.raises(base.ImportRefused, match="not readable JSON"):
        JsonResumeImporter().read(b'{"basics": {"name": NaN}}')


# ------------------------------------------------------------------ the classic file


def test_the_classic_file_reads_into_postulos_terms():
    record = read(CLASSIC)

    assert record.source == "json-resume"
    assert record.source_label.startswith("Read as JSON Resume")
    assert record.person == {
        "first_name": "Alex",
        "last_name": "Morgan",
        "headline": "Logistics planner",
        "phone": "+351 21 000 0000",
        "website": "https://alex-morgan.example.org",
        "email": "alex.morgan@example.org",
        "address": {
            "street": "Rua do Exemplo 1",
            "postcode": "1000-001",
            "municipality": "Lisboa",
            "region": "Lisboa",
            "country": "PT",
        },
        "location": "Lisboa, PT",
        "orcid": "0000-0002-1825-0097",
    }
    first, second = record.experience
    assert (first["organisation"], first["role"]) == ("Example Transportes, Lda.", "Route planner")
    assert first["start_date"] == dt.date(2021, 3, 1) and first["end_date"] is None
    assert first["highlights"] == "Cut empty kilometres by a tenth\nTrained two planners"
    assert second["end_date"] == dt.date(2021, 2, 28)
    [school] = record.education
    assert (school["qualification"], school["field_of_study"], school["grade"]) == (
        "Licenciatura",
        "Logistics",
        "15/20",
    )
    assert school["highlights"] == "Operations research\nFleet management"
    assert record.honours[0]["awarded_by"] == "Example Logistics SA"
    assert record.publications[0]["date"] == "2023-05"
    assert record.skill_groups == [
        {"name": "Planning", "skills": ["Route optimisation", "SAP TM"]},
        {"name": "Skills", "skills": ["Forklift"]},
    ]


def test_volunteering_is_read_as_memberships_and_the_review_says_so():
    """Decision D5 of #1260."""
    record = read(CLASSIC)

    [food_bank] = record.memberships
    assert (food_bank["organisation"], food_bank["role"]) == ("Example Food Bank", "Driver")
    assert food_bank["start_date"] == dt.date(2019, 1, 1)
    assert any("Volunteering was read as memberships" in note for note in record.notes)


def test_no_level_is_claimed_but_a_cefr_one_or_native():
    """Decision D6: the file's own words are shown, never turned into a level."""
    record = read(CLASSIC)

    assert [(row["name"], row["proficiency"], row["stated"]) for row in record.languages] == [
        ("Portuguese", "native", ""),
        ("English", "b2", ""),
        ("Spanish", "", "Conversational"),
    ]


def test_what_has_no_place_is_named_and_not_dropped():
    record = read(CLASSIC)
    said = " ".join(record.skipped)

    for name in (
        "basics.summary",
        "interests",
        "work.url",
        "skills.level",
        "publications.summary",
        "references.reference",
    ):
        assert name in said, name
    assert "Mastodon" in said, "a profile on another site is named"


def test_a_referee_is_a_person_and_their_words_are_not_kept():
    record = read(CLASSIC)

    assert record.references == [{"name": "Jordan Example", "relationship": ""}]


# ------------------------------------------------------------------ the 1.7.1 file


def test_what_the_newer_schema_added_is_read():
    record = read(CURRENT)

    assert record.person["orcid"] == "0000-0002-1825-0097"
    assert record.experience[0]["location"] == "Lisboa, PT"
    assert record.experience[1]["location"] == "Porto"
    assert record.education[0]["location"] == "Setúbal, PT"
    assert record.education[0]["highlights"] == "Final project on depot location."
    [certificate] = record.certifications
    assert certificate == {
        "name": "ADR driver training",
        "issuer": "Example Institute",
        "issued_on": dt.date(2022, 5, 10),
        "expires_on": dt.date(2027, 5, 10),
        "credential_url": "https://example.org/adr",
    }
    assert record.publications[0]["doi"] == "10.1000/182"
    assert record.references == [{"name": "Jordan Example", "relationship": "Former manager"}]
    [project] = record.projects
    assert (project["role"], project["highlights"], project["summary"]) == (
        "Lead",
        "On time",
        "Moved the depot without a missed delivery.",
    )
    said = " ".join(record.skipped)
    for name in ("objective", "meta", "work.custom", "work.keywords", "certificates.credentialId"):
        assert name in said, name


# ------------------------------------------------------------------ the odd file


def test_a_wrong_value_loses_that_value_and_the_rest_is_read():
    record = read(ODD)
    said = " ".join(record.skipped)

    assert "first_name" not in record.person, "three words are not split by a guess (D8)"
    assert "“Alex de la Morgan” was not split" in said
    assert "headline" not in record.person and "basics.label" in said
    assert record.person["location"] == "Lisboa"
    assert [row["role"] for row in record.experience] == ["Route planner", "Dispatcher"]
    assert all(row["start_date"] is None for row in record.experience)
    assert "“March 2021” is not one Postulo reads" in said
    assert "“2017-13” is not one Postulo reads" in said
    assert "work[1]" in said and "work[2]" in said and "skills" in said
    assert record.education[0]["start_date"] is None
    assert record.languages == [
        {"name": "English", "proficiency": "", "stated": "Professional working proficiency"}
    ]
    assert record.certifications == [] and "certificates[0]" in said
    assert record.references == [] and "references[0]" in said
    assert "hobbies" in said


def test_two_words_are_a_first_name_and_a_surname_and_one_is_not():
    data = b'{"basics": {"name": "Alex"}}'

    record = JsonResumeImporter().read(data)

    assert "first_name" not in record.person
    assert any("“Alex” was not split" in note for note in record.skipped)


# ------------------------------------------------------------------ the round trip


def test_a_cv_written_as_json_resume_reads_back_as_what_was_written(cv, person):  # noqa: F811
    data = structured.cv_data(cv)
    written = jsonresume.write(data)

    record = JsonResumeImporter().read(written)

    assert JsonResumeImporter().can_handle(written)
    assert [
        (row["organisation"], row["role"], row["start_date"], row["end_date"])
        for row in record.experience
    ] == [
        (entry["organisation"], entry["role"], entry["start_date"], entry["end_date"])
        for entry in data.entries("experience")
    ]
    assert [row["highlights"] for row in record.experience] == [
        "\n".join(entry["highlights"])
        if isinstance(entry["highlights"], list)
        else entry["highlights"]
        for entry in data.entries("experience")
    ]
    assert [
        (row["institution"], row["qualification"], row["field_of_study"], row["grade"])
        for row in record.education
    ] == [
        (entry["institution"], entry["qualification"], entry["field_of_study"], entry["grade"])
        for entry in data.entries("education")
    ]
    assert record.skill_groups == [
        {"name": entry["name"], "skills": list(entry["skills"])}
        for entry in data.entries("skillgroup")
    ]
    assert [(row["name"], row["proficiency"]) for row in record.languages] == [
        (entry["name"], entry["proficiency"]) for entry in data.entries("languageskill")
    ]
    assert [(row["name"], row["issuer"], row["issued_on"]) for row in record.certifications] == [
        (entry["name"], entry["issuer"], entry["issued_on"])
        for entry in data.entries("certification")
    ]
    assert [(row["title"], row["awarded_by"]) for row in record.honours] == [
        (entry["title"], entry["awarded_by"]) for entry in data.entries("honour")
    ]
    assert [
        (row["name"], row["role"], row["url"], row["summary"], row["start_date"])
        for row in record.projects
    ] == [
        (entry["name"], entry["role"], entry["url"], entry["summary"], entry["start_date"])
        for entry in data.entries("project")
    ]
    assert record.person["first_name"] == "Alex" and record.person["last_name"] == "Morgan"
    assert record.person["orcid"] == "0000-0002-1825-0097"


def test_the_round_trip_writes_onto_a_new_account(cv, person, other_user):  # noqa: F811
    from postulo.resume.models import Certification, Experience, Honour, LanguageSkill

    record = JsonResumeImporter().read(jsonresume.write(structured.cv_data(cv)))

    importing.apply(other_user, record)

    assert sorted(Experience.objects.for_user(other_user).values_list("role", flat=True)) == [
        "Engineer",
        "Senior Engineer",
    ]
    assert Certification.objects.for_user(other_user).get().issuer == "CNCF"
    assert Honour.objects.for_user(other_user).get().awarded_by == "SOSP"
    assert dict(LanguageSkill.objects.for_user(other_user).values_list("name", "proficiency")) == {
        "Portuguese": "native",
        "French": "c1",
        "Basque": "",
    }


# ------------------------------------------------------------------ the page


def _upload(name: str, data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, data, content_type="application/json")


def test_the_page_reads_one_and_writes_nothing_until_told(client, user):
    from postulo.resume.models import Certification, Experience, Membership

    client.force_login(user)
    url = reverse("resume:import")

    client.post(url, {"file": _upload("resume.json", CURRENT.read_bytes())})
    body = client.get(url).content.decode()

    assert "Read as JSON Resume" in body and "ADR driver training" in body
    assert "Volunteering was read as memberships" in body
    assert not Experience.objects.for_user(user).exists()

    client.post(url, {"action": "confirm"})

    assert Experience.objects.for_user(user).count() == 2
    assert Certification.objects.for_user(user).get().expires_on == dt.date(2027, 5, 10)
    assert Membership.objects.for_user(user).get().organisation == "Example Food Bank"


def test_switched_off_it_reads_nothing(client, user):
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin="json-resume", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    response = client.post(
        reverse("resume:import"),
        {"file": _upload("resume.json", CURRENT.read_bytes())},
        follow=True,
    )

    assert "Nothing installed here reads that file" in response.content.decode()
    assert client.session.get("career_import") is None
