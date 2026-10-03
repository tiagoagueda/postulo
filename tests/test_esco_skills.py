"""A skill on the career record keeps the ESCO skill its name is (#266).

The skills half of the issue: "a skills vocabulary for the CV that is not free text, which
the résumé side needs and currently invents per entry". The name stays the person's, and
beside it the skill keeps the classification's identifier for the skill that name is --
worked out from the name whenever the name is written, never set by hand, never read from
a file. None of this needs the real classification: the skills here are the few in
`tests/conftest.py::ESCO_SKILLS`, written the way `fetch_esco` writes them.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import translation

from postulo.core import export, importer
from postulo.documents.models import CV, CVItem
from postulo.documents.rendering import cv_text
from postulo.jobs import esco
from postulo.resume import candidate, importing, translating
from postulo.resume.models import Skill, SkillGroup, Translation

from .conftest import ESCO_SKILLS, write_esco_skills

#: The suggestions are counted in the cache, and the cache is a table.
pytestmark = pytest.mark.django_db

PROJECTS = esco.SKILL_NAMESPACE + "7111b95d-0ce3-441a-9d92-4c75d05c4388"
BUDGETS = esco.SKILL_NAMESPACE + "21c5790c-0930-4d74-b3b0-84caf5af12ea"
SQL = esco.SKILL_NAMESPACE + "598de5b0-5b58-4ea7-8058-a4bc4d18c742"
TEAMWORK = esco.SKILL_NAMESPACE + "e0000000-0000-4000-8000-000000000001"
SPREADSHEETS = esco.SKILL_NAMESPACE + "e0000000-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def _empty_cache():
    """The suggestions are counted in the cache; one test's count is not another's."""
    cache.clear()
    yield
    cache.clear()


def record_in(person, language: str) -> None:
    person.profile.record_language = language
    person.profile.save()


# ------------------------------------------------------------------- the file, read


def test_without_the_file_a_skill_matches_nothing_and_nothing_is_offered(no_esco):
    """The ordinary state of an instance nobody ran `fetch_esco` on, and not an error."""
    assert esco.skills_file() is None
    assert not esco.skills_available()
    assert esco.skill_for("project management", "en") == ""
    assert esco.skill_suggestions("proj", "en") == []
    assert esco.skill_name(PROJECTS, "fr") == ""


def test_the_skills_file_leaves_the_occupations_guard_as_it_was(esco_skills):
    """The occupations' loader refuses two `esco-*.json`; the skills are not one of those,
    and have a guard of their own."""
    (esco_skills / "esco-9.9.9.json").write_text(json.dumps(esco.ABSENT), encoding="utf-8")
    assert esco.data_file().name == "esco-9.9.9.json"
    assert esco.skills_file().name == "esco-skills-9.9.9.zip"

    write_esco_skills(esco_skills, revision="9.9.10")
    with pytest.raises(RuntimeError, match="two ESCO skills files"):
        esco.skills_file()


def test_the_file_says_which_revision_and_languages_it_holds(esco_skills):
    about = esco.skills_about()
    assert about["revision"] == "ESCO v9.9.9"
    assert about["languages"] == ["en", "fr", "pt"]
    assert about["skills"] == len(ESCO_SKILLS)


def test_a_file_fetch_esco_did_not_write_is_refused_rather_than_misread(esco_skills):
    """A language with a name too few would put every name after it on the wrong skill."""
    from postulo.jobs.management.commands import fetch_esco

    esco.skills_file().unlink()
    about = {**esco.ABSENT_SKILLS, "revision": "ESCO v9.9.9", "languages": ["en", "xx"]}
    identifiers = ["7111b95d-0ce3-441a-9d92-4c75d05c4388", "ccd0a1d9-afda-43d9-b901-96344886e14d"]
    names = {"en": ["project management", "Python (computer programming)"], "xx": ["one"]}
    fetch_esco.write_skills(esco_skills / "esco-skills-9.9.9.zip", about, identifiers, names)
    esco.forget()

    assert esco.skill_name(PROJECTS, "en") == "project management"
    with pytest.raises(RuntimeError, match="not a file"):
        esco.skill_name(PROJECTS, "xx")


def test_a_language_is_read_when_it_is_asked_for_and_not_before(esco_skills):
    """Twenty-eight languages of names are not held to serve one reader."""
    esco.skill_for("gestão de projetos", "pt")
    assert esco._skill_names.cache_info().currsize == 1, "Portuguese, before English was"

    esco.skill_suggestions("pr", "en")
    assert esco._skill_names.cache_info().currsize == 2, "and French never"
    assert esco._skill_names.cache_parameters()["maxsize"] == esco.SKILL_LANGUAGES_HELD


# ---------------------------------------------------------------- matching a name


def test_a_preferred_name_is_the_skill_whatever_the_case_and_the_spacing(esco_skills):
    assert esco.skill_for("project management", "en") == PROJECTS
    assert esco.skill_for("  Project   MANAGEMENT ", "en") == PROJECTS
    assert esco.skill_for("SQL", "en") == SQL


def test_a_name_that_is_no_skill_has_no_identifier(esco_skills):
    """And that is not a lesser kind of skill. A near miss is a miss: *Python* is not the
    classification's *Python (computer programming)*, and the box offers the latter as it
    is typed rather than having it guessed at afterwards."""
    assert esco.skill_for("Fintech wizardry", "en") == ""
    assert esco.skill_for("Python", "en") == ""
    assert esco.skill_for("", "en") == ""


def test_a_name_is_tried_in_each_language_given_and_then_in_english(esco_skills):
    assert esco.skill_for("gestão de projetos", "pt") == PROJECTS
    assert esco.skill_for("gestão de projetos", "fr", "pt-BR") == PROJECTS
    assert esco.skill_for("manage budgets", "pt") == BUDGETS, "English last, always"
    assert esco.skill_for("gestion de projets", "pt") == "", "French was not asked"


def test_a_name_two_skills_share_is_neither_of_them(esco_skills):
    """A choice between two would be a guess kept as a fact."""
    assert esco.skill_for("usar folhas de cálculo", "pt") == ""
    assert esco.skill_for("use spreadsheets software", "pt") == SPREADSHEETS


def test_what_the_classification_calls_a_skill(esco_skills):
    assert esco.skill_name(PROJECTS, "fr-FR") == "gestion de projets"
    assert esco.skill_name(PROJECTS, "pt-BR") == "gestão de projetos"
    # Strict is for a document: no English standing in for a language.
    assert esco.skill_name(TEAMWORK, "fr") == ""
    assert esco.skill_name(PROJECTS, "tr") == ""
    # Not strict is for the person reading Postulo, who may be shown the English.
    assert esco.skill_name(TEAMWORK, "fr", strict=False) == "teamwork principles"
    assert esco.skill_name(PROJECTS, "tr", strict=False) == "project management"
    # An identifier this file does not have is a skill a later revision retired.
    assert esco.skill_name(esco.SKILL_NAMESPACE + "retired", "en") == ""
    assert esco.skill_name("https://example.org/skill/7111b95d", "en") == ""


# --------------------------------------------------------------- what is offered


def test_the_box_is_offered_names_beginning_with_what_was_typed(esco_skills):
    assert esco.skill_suggestions("proj", "en") == ["project management"]
    assert esco.skill_suggestions("PY", "en") == ["Python (computer programming)"]
    assert esco.skill_suggestions("u", "en") == [], "one letter is most of an alphabet"
    assert esco.skill_suggestions("zz", "en") == []


def test_what_is_offered_is_in_the_readers_language_then_the_records(esco_skills):
    # A letter is a letter: "ge" is not the beginning of "gérer".
    assert esco.skill_suggestions("ge", "fr") == ["gestion de projets"]
    assert esco.skill_suggestions("gé", "fr") == ["gérer les budgets"]
    assert esco.skill_suggestions("ge", "en", "pt") == ["gerir orçamentos", "gestão de projetos"]
    # A reader in a language the classification does not publish is offered the English.
    assert esco.skill_suggestions("proj", "tr") == ["project management"]


def test_what_is_offered_is_bounded(esco_skills):
    assert esco.skill_suggestions("ge", "fr", limit=1) == ["gestion de projets"]
    assert len(esco.skill_suggestions("ge", "fr", "pt", limit=3)) == 3


# ------------------------------------------------------------------ on the record


def test_a_skill_keeps_the_skill_its_name_is(esco_skills, user):
    skill = Skill.objects.create(owner=user, name="Project management")
    made_up = Skill.objects.create(owner=user, name="Kubernetes wrangling")

    assert skill.esco_uri == PROJECTS
    assert made_up.esco_uri == ""
    assert Skill.objects.get(pk=skill.pk).esco_uri == PROJECTS


def test_the_identifier_follows_the_name(esco_skills, user):
    """One invariant: renamed, it is the renamed skill's, or nobody's."""
    skill = Skill.objects.create(owner=user, name="Project management")

    skill.name = "manage budgets"
    skill.save()
    assert Skill.objects.get(pk=skill.pk).esco_uri == BUDGETS

    skill.name = "Budgets, mostly"
    skill.save(update_fields=["name"])
    assert Skill.objects.get(pk=skill.pk).esco_uri == ""


def test_a_save_that_does_not_write_the_name_does_not_ask(esco_skills, user):
    skill = Skill.objects.create(owner=user, name="SQL")
    Skill.objects.filter(pk=skill.pk).update(esco_uri="")

    skill.esco_uri = ""
    skill.order = 3
    skill.save(update_fields=["order"])

    assert Skill.objects.get(pk=skill.pk).esco_uri == ""


def test_the_record_language_is_asked_before_the_readers(esco_skills, user):
    record_in(user, "pt-PT")
    with translation.override("fr"):
        skill = Skill.objects.create(owner=user, name="gestão de projetos")
    assert skill.esco_uri == PROJECTS


def test_a_name_offered_in_the_readers_language_is_recognised(esco_skills, user):
    record_in(user, "en-GB")
    with translation.override("fr"):
        skill = Skill.objects.create(owner=user, name="gérer les budgets")
    assert skill.esco_uri == BUDGETS


def test_without_the_file_the_record_is_unchanged_and_nothing_is_wrong(no_esco, user):
    skill = Skill.objects.create(owner=user, name="Project management")
    assert skill.esco_uri == "" and skill.esco_name == ""


def test_a_skill_imported_from_europass_is_matched_too(esco_skills, user):
    record = importing.Record(
        skill_groups=[{"name": "Work", "skills": ["Project management", "Juggling"]}],
    )
    importing.apply(user, record)

    found = {skill.name: skill.esco_uri for skill in Skill.objects.for_user(user)}
    assert found == {"Project management": PROJECTS, "Juggling": ""}


# ------------------------------------------------------------- on a CV, elsewhere


@pytest.fixture
def skills_on_a_cv(esco_skills, user):
    """A record in English and a CV in French holding one group of four skills."""
    record_in(user, "en-GB")
    group = SkillGroup.objects.create(owner=user, name="Work")
    made = {
        name: Skill.objects.create(owner=user, group=group, name=name, order=index)
        for index, name in enumerate(
            ("Project management", "manage budgets", "teamwork principles", "Juggling")
        )
    }
    cv = CV.objects.create(owner=user, name="Paris", language="fr-FR")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(SkillGroup),
        object_id=group.pk,
    )
    return cv, group, made


def printed(cv) -> list[str]:
    from postulo.documents.rendering import build_sections

    (section,) = build_sections(cv)
    return section.items[0].item.skill_names


def test_a_cv_in_another_language_prints_the_classifications_name(skills_on_a_cv):
    """Where the person has not written their own, and the classification publishes one;
    the first letter as the person wrote theirs."""
    cv, _group, _made = skills_on_a_cv

    assert printed(cv) == [
        "Gestion de projets",
        "gérer les budgets",
        # No French name published: the name as written, never the English standing in.
        "teamwork principles",
        # In no classification: as written, which is what it always was.
        "Juggling",
    ]
    assert "Gestion de projets" in cv_text(cv), "the text and the PDF say the same"


def test_a_translation_of_the_persons_own_always_wins(skills_on_a_cv):
    cv, _group, made = skills_on_a_cv
    Translation.objects.create(
        owner=cv.owner,
        content_type=ContentType.objects.get_for_model(Skill),
        object_id=made["Project management"].pk,
        language="fr-FR",
        field="name",
        text="Pilotage de projets",
    )

    assert printed(cv)[0] == "Pilotage de projets"
    assert [skill.name for skill, _name in translating.named_by_classification(cv)] == [
        "manage budgets"
    ]


def test_a_cv_in_the_records_own_language_prints_the_names_as_written(skills_on_a_cv):
    cv, _group, _made = skills_on_a_cv
    cv.language = "en-us"
    cv.save()

    assert printed(cv) == [
        "Project management",
        "manage budgets",
        "teamwork principles",
        "Juggling",
    ]
    assert translating.named_by_classification(cv) == []


def test_a_cv_in_a_language_the_classification_does_not_publish_is_left_alone(skills_on_a_cv):
    cv, _group, _made = skills_on_a_cv
    cv.language = "tr"
    cv.save()

    assert printed(cv) == [
        "Project management",
        "manage budgets",
        "teamwork principles",
        "Juggling",
    ]


def test_the_cv_page_says_which_names_are_the_classifications_before_it_is_exported(
    skills_on_a_cv, client
):
    cv, _group, made = skills_on_a_cv
    client.force_login(cv.owner)

    html = client.get(reverse("documents:cv_detail", args=[cv.pk])).content.decode()

    assert "data-classified" in html
    assert "Gestion de projets" in html and "gérer les budgets" in html
    languages = reverse("resume:item_languages", args=["skill", made["manage budgets"].pk])
    assert f"{languages}?language=fr-FR" in html
    assert "teamwork principles" not in html.split("data-classified", 1)[1].split("</div>")[0]


def test_the_skills_page_in_that_language_says_what_an_empty_box_prints(skills_on_a_cv, client):
    cv, _group, made = skills_on_a_cv
    client.force_login(cv.owner)
    url = reverse("resume:item_languages", args=["skill", made["Project management"].pk])

    french = client.get(f"{url}?language=fr-FR").content.decode()
    english = client.get(f"{url}?language=en-us").content.decode()

    assert "Gestion de projets" in french and "ESCO" in french
    assert "Gestion de projets" not in english
    # And before a language is chosen, the list of the skill's own says what prints instead.
    assert "None of your own yet" in client.get(url).content.decode()


def test_the_skills_own_page_says_what_it_was_recognised_as(esco_skills, user, client):
    skill = Skill.objects.create(owner=user, name="Project management")
    other = Skill.objects.create(owner=user, name="Juggling")
    client.force_login(user)

    recognised = client.get(reverse("resume:item_update", args=["skill", skill.pk]))
    unrecognised = client.get(reverse("resume:item_update", args=["skill", other.pk]))

    assert "data-esco-skill" in recognised.content.decode()
    assert "project management" in recognised.content.decode()
    assert "data-esco-skill" not in unrecognised.content.decode()


# ------------------------------------------------------------------- the skill box


def test_the_box_is_a_plain_text_box_that_asks_for_its_names_as_it_is_typed(
    esco_skills, user, client
):
    """With scripts off, the `hx-` attributes do nothing and the list stays empty; with
    them on, htmx fills the list from the address the box names."""
    client.force_login(user)

    html = client.get(reverse("resume:item_create", args=["skill"])).content.decode()

    assert 'list="skill-suggestions"' in html
    assert '<datalist id="skill-suggestions"></datalist>' in html
    assert f'hx-get="{reverse("resume:skill_suggestions")}"' in html
    assert 'hx-target="#skill-suggestions"' in html


def test_the_box_answers_a_prefix_with_names(esco_skills, user, client):
    client.force_login(user)

    answer = client.get(reverse("resume:skill_suggestions"), {"name": "proj"})

    assert answer.status_code == 200
    assert answer.content.decode().strip() == '<option value="project management">'
    assert "no-store" in answer["Cache-Control"]


def test_the_box_answers_in_the_readers_language_and_the_records(esco_skills, user, client):
    record_in(user, "pt-PT")
    user.profile.language = "fr-FR"
    user.profile.save()
    client.force_login(user)

    answer = client.get(reverse("resume:skill_suggestions"), {"name": "ge"}).content.decode()

    assert "gestion de projets" in answer and "gestão de projetos" in answer


def test_the_box_is_answered_with_nothing_where_there_is_nothing(no_esco, user, client):
    client.force_login(user)

    answer = client.get(reverse("resume:skill_suggestions"), {"name": "proj"})

    assert answer.status_code == 200 and "<option" not in answer.content.decode()


def test_a_skill_saved_from_the_form_is_matched(esco_skills, user, client):
    client.force_login(user)
    group = SkillGroup.objects.create(owner=user, name="Management")

    client.post(
        reverse("resume:item_create", args=["skill"]),
        {"name": "project management", "group": group.pk},
    )

    assert Skill.objects.for_user(user).get().esco_uri == PROJECTS


# ------------------------------------------------------------ what travels with it


def test_the_archive_carries_the_identifier_beside_the_name(esco_skills, user):
    Skill.objects.create(owner=user, name="Project management")

    (written,) = export.build_document(user)["resume"]["skills"]

    assert list(written)[:3] == ["id", "name", "esco_uri"]
    assert written["esco_uri"] == PROJECTS
    assert export.FORMAT_VERSION >= 22


def test_an_archive_is_matched_again_on_the_way_in_and_its_word_is_not_taken(
    esco_skills, user, other_user
):
    """What the file says the skill was is never what the column holds."""
    Skill.objects.create(owner=user, name="Project management")
    Skill.objects.create(owner=user, name="Juggling")
    document = export.build_document(user)
    for entry in document["resume"]["skills"]:
        entry["esco_uri"] = BUDGETS
    buffer = _zipped(document)

    importer.load(other_user, zipfile.ZipFile(buffer))

    found = {skill.name: skill.esco_uri for skill in Skill.objects.for_user(other_user)}
    assert found == {"Project management": PROJECTS, "Juggling": ""}


def test_an_archive_from_before_the_identifier_restores_with_it_worked_out(
    esco_skills, user, other_user
):
    Skill.objects.create(owner=user, name="Project management")
    document = export.build_document(user)
    document["postulo"]["format"] = 21
    document["resume"]["skills"][0].pop("esco_uri")

    importer.load(other_user, zipfile.ZipFile(_zipped(document)))

    assert Skill.objects.for_user(other_user).get().esco_uri == PROJECTS


def test_the_candidate_file_carries_it_and_its_reader_works_it_out_again(
    esco_skills, user, other_user
):
    record_in(user, "en-GB")
    Skill.objects.create(owner=user, name="Project management")
    Skill.objects.create(owner=user, name="Juggling")
    document = export.build_candidate_document(user)
    assert document["postulo"]["candidate_format"] >= 2
    assert {row["name"]: row["esco_uri"] for row in document["resume"]["skills"]} == {
        "Project management": PROJECTS,
        "Juggling": "",
    }
    for entry in document["resume"]["skills"]:
        entry["esco_uri"] = BUDGETS

    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    found = {skill.name: skill.esco_uri for skill in Skill.objects.for_user(other_user)}
    assert found == {"Project management": PROJECTS, "Juggling": ""}


def test_a_candidate_file_of_the_first_format_still_reads(esco_skills, user):
    """Format 1 wrote no identifier; nothing read one, so the file reads as it did and its
    skills are matched by their names."""
    document = {
        "postulo": {"candidate_format": 1, "version": "0.5.0", "exported_at": ""},
        "resume": {"skills": [{"id": 1, "name": "manage budgets", "group_id": None}]},
    }

    plan = candidate.plan(user, candidate.read(json.dumps(document).encode()))
    candidate.apply(user, candidate.read(json.dumps(document).encode()))

    assert not plan.notes, "an older file is not a newer one"
    assert Skill.objects.for_user(user).get().esco_uri == BUDGETS


def _zipped(document):
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return buffer


@override_settings(POSTULO_SUGGESTION_RATE="2/h")
def test_the_box_is_bounded_per_account(esco_skills, user, other_user, client):
    client.force_login(user)
    url = reverse("resume:skill_suggestions")

    assert client.get(url, {"name": "pr"}).status_code == 200
    assert client.get(url, {"name": "pro"}).status_code == 200
    refused = client.get(url, {"name": "proj"})

    assert refused.status_code == 429 and int(refused["Retry-After"]) > 0
    client.force_login(other_user)
    assert client.get(url, {"name": "pr"}).status_code == 200, "an allowance is one account's"
