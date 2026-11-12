"""A listing says which of the skills it names the person has, from ESCO, with no model (#1093).

Somebody reading a listing asks which of its requirements they meet. Postulo held both halves
of the answer -- the ESCO skill each of the person's skills is (#266), and the classification
-- and compared neither. A listing's page and its application's page now draw the skills its
words name, split into those the career record has and those it does not.

None of this needs the real classification: the skills are the few in
`tests/conftest.py::ESCO_SKILLS`, or a handful written here, the way `fetch_esco` writes them.
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import translation

from postulo.applications.services import apply_to_listing, create_listing
from postulo.documents.models import CV, CVItem
from postulo.jobs import esco, listing_skills
from postulo.jobs.models import Company
from postulo.resume.models import Skill, SkillGroup

from .conftest import write_esco_skills

pytestmark = pytest.mark.django_db

BUDGETS = esco.SKILL_NAMESPACE + "21c5790c-0930-4d74-b3b0-84caf5af12ea"
SQL = esco.SKILL_NAMESPACE + "598de5b0-5b58-4ea7-8058-a4bc4d18c742"
PROJECTS = esco.SKILL_NAMESPACE + "7111b95d-0ce3-441a-9d92-4c75d05c4388"
PYTHON = esco.SKILL_NAMESPACE + "ccd0a1d9-afda-43d9-b901-96344886e14d"

TEXT = (
    "## The role\n\nYou know **SQL** and Python, and you have led project management for "
    "years. You will also manage budgets."
)


def listing_for(person, description=TEXT, title="Data engineer"):
    company = Company.objects.create(owner=person, name="Acme")
    return create_listing(
        person,
        company=company,
        posting_data={
            "title": title,
            "description": description,
            "description_format": "markdown",
        },
    )


def record_in(person, language: str) -> None:
    person.profile.record_language = language
    person.profile.save()


# ------------------------------------------------------------------- reading the text


def test_a_text_names_the_skills_whose_names_are_runs_of_its_words(esco_skills):
    """In the order the text first names them, whatever the case and the markup around a
    word; *Python* is the skill ESCO calls *Python (computer programming)*."""
    assert esco.skills_named(TEXT, "en") == (SQL, PYTHON, PROJECTS, BUDGETS)


def test_the_longest_name_is_taken_and_not_the_shorter_one_inside_it(tmp_path, monkeypatch):
    """*project management* is one skill, not that and *management* as well."""
    directory = tmp_path / "esco"
    directory.mkdir()
    write_esco_skills(
        directory,
        {
            "a0000000-0000-4000-8000-000000000001": {"en": "management"},
            "a0000000-0000-4000-8000-000000000002": {"en": "project management"},
            "a0000000-0000-4000-8000-000000000003": {"en": "project"},
        },
    )
    monkeypatch.setattr(esco, "DATA_DIR", directory)
    esco.forget()
    try:
        found = esco.skills_named("Project management, and management.", "en")
    finally:
        esco.forget()
    assert found == (
        esco.SKILL_NAMESPACE + "a0000000-0000-4000-8000-000000000002",
        esco.SKILL_NAMESPACE + "a0000000-0000-4000-8000-000000000001",
    )


def test_the_text_is_read_in_the_language_that_finds_the_most(esco_skills):
    """A Portuguese listing read by somebody whose record is Portuguese: the English names find
    *SQL* in it, the Portuguese ones *SQL* and *gestão de projetos*, and the Portuguese win."""
    text = "Procuramos experiência em gestão de projetos e SQL."
    assert esco.skills_named(text, "en") == (SQL,)
    assert esco.skills_named(text, "pt-PT") == (PROJECTS, SQL)


def test_a_name_two_skills_share_names_neither(esco_skills):
    """*usar folhas de cálculo* is the Portuguese name of two skills in the fixture."""
    assert esco.skills_named("Saber usar folhas de cálculo.", "pt-PT") == ()


def test_without_the_skills_a_text_names_nothing(no_esco):
    assert esco.skills_named(TEXT, "en") == ()


def test_a_text_is_read_once_and_again_when_it_changes(esco_skills, monkeypatch):
    """The answer is remembered by what the text says, so a page drawn twice reads it once,
    an edited description is read afresh, and `forget` drops what was found."""
    calls = []
    original = esco._named_in

    def counted(words, language):
        calls.append(language)
        return original(words, language)

    monkeypatch.setattr(esco, "_named_in", counted)
    esco.skills_named(TEXT, "en")
    read = len(calls)
    assert read
    assert esco.skills_named(TEXT, "en") == (SQL, PYTHON, PROJECTS, BUDGETS)
    assert len(calls) == read, "the same text was read again"
    assert esco.skills_named(TEXT + " No SQL.", "en") == (SQL, PYTHON, PROJECTS, BUDGETS)
    assert len(calls) == 2 * read
    esco.forget()
    esco.skills_named(TEXT, "en")
    assert len(calls) == 3 * read


def test_one_language_and_english_are_all_that_is_read(esco_skills):
    """Answering one page reads the languages it is asked in and English, never the others
    the file holds (#1110 keeps the skills a language at a time)."""
    esco.skills_named(TEXT, "en")
    assert esco._skill_names.cache_info().currsize == 1


# ------------------------------------------------------------------- the panel


def test_the_panel_splits_the_skills_into_those_held_and_those_not(esco_skills, user):
    Skill.objects.create(owner=user, name="SQL")
    Skill.objects.create(owner=user, name="Project management")
    with translation.override("en"):
        found = listing_skills.panel(listing_for(user), user)
    assert found.available
    assert found.held == ["SQL", "project management"]
    assert found.missing == ["Python (computer programming)", "manage budgets"]
    assert found.best_cv is None


def test_somebody_elses_skills_are_not_yours(esco_skills, user, other_user):
    Skill.objects.create(owner=other_user, name="SQL")
    with translation.override("en"):
        found = listing_skills.panel(listing_for(user), user)
    assert found.held == []
    assert "SQL" in found.missing


def test_the_names_are_in_the_readers_language(esco_skills, user):
    record_in(user, "en-GB")
    Skill.objects.create(owner=user, name="SQL")
    with translation.override("fr"):
        found = listing_skills.panel(listing_for(user), user)
    assert found.missing == [
        "Python (programmation informatique)",
        "gestion de projets",
        "gérer les budgets",
    ]


def test_the_cv_that_prints_the_most_of_them_is_named(esco_skills, user):
    group = SkillGroup.objects.create(owner=user, name="Data")
    Skill.objects.create(owner=user, group=group, name="SQL")
    Skill.objects.create(owner=user, group=group, name="Project management")
    lone = SkillGroup.objects.create(owner=user, name="Other")
    Skill.objects.create(owner=user, group=lone, name="manage budgets")
    kind = ContentType.objects.get_for_model(SkillGroup)
    small = CV.objects.create(owner=user, name="Accounts")
    CVItem.objects.create(owner=user, cv=small, content_type=kind, object_id=lone.pk)
    large = CV.objects.create(owner=user, name="Data")
    CVItem.objects.create(owner=user, cv=large, content_type=kind, object_id=group.pk)
    left_off = CV.objects.create(owner=user, name="All")
    for each in (group, lone):
        CVItem.objects.create(
            owner=user, cv=left_off, content_type=kind, object_id=each.pk, is_included=False
        )
    with translation.override("en"):
        found = listing_skills.panel(listing_for(user), user)
    assert found.best_cv == {"pk": large.pk, "name": "Data"}
    assert found.best_cv_count == 2


# ------------------------------------------------------------------- the pages


def test_the_listing_page_draws_the_panel(esco_skills, user, client):
    Skill.objects.create(owner=user, name="SQL")
    listing = listing_for(user)
    client.force_login(user)
    page = client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()
    assert "data-listing-skills" in page
    held = page.split("data-skills-held")[1].split("</ul>")[0]
    missing = page.split("data-skills-missing")[1].split("</ul>")[0]
    assert "SQL" in held and "SQL" not in missing
    assert "manage budgets" in missing


def test_the_application_page_draws_the_panel(esco_skills, user, client):
    Skill.objects.create(owner=user, name="SQL")
    application = apply_to_listing(listing_for(user), {})
    client.force_login(user)
    page = client.get(application.get_absolute_url()).content.decode()
    assert "data-skills-held" in page
    assert "manage budgets" in page.split("data-skills-missing")[1]


def test_a_listing_that_names_no_skill_says_so(esco_skills, user, client):
    listing = listing_for(user, description="A friendly team.", title="Engineer")
    client.force_login(user)
    page = client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()
    assert "data-skills-none" in page
    assert "data-skills-held" not in page


def test_without_esco_the_page_says_how_an_administrator_fetches_it(no_esco, user, client):
    """No comparison is drawn, and no claim that the listing names no skill."""
    listing = listing_for(user)
    client.force_login(user)
    page = client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()
    assert "data-skills-unavailable" in page
    assert "manage.py fetch_esco" in page
    assert "data-skills-none" not in page
    assert "data-skills-held" not in page and "data-skills-missing" not in page


def test_the_listing_page_with_the_panel_costs_what_it_did_and_two_queries(
    esco_skills, user, client, django_assert_num_queries, monkeypatch
):
    """What the person has is one query, and the CV that prints the most another; reading
    the text costs none. Pinned, so a query per skill shows up here."""
    group = SkillGroup.objects.create(owner=user, name="Data")
    for name in ("SQL", "Project management", "manage budgets"):
        Skill.objects.create(owner=user, group=group, name=name)
    cv = CV.objects.create(owner=user, name="Data")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(SkillGroup),
        object_id=group.pk,
    )
    listing = listing_for(user)
    address = reverse("jobs:posting_detail", args=[listing.pk])
    client.force_login(user)
    client.get(address)  # What a first request of the session reads once.
    with django_assert_num_queries(PAGE_QUERIES):
        page = client.get(address).content.decode()
    assert "data-skills-best-cv" in page
    monkeypatch.setattr(esco, "skills_available", lambda: False)
    with django_assert_num_queries(PAGE_QUERIES - 2):
        page = client.get(address).content.decode()
    assert "data-skills-unavailable" in page


#: The listing page, its panel holding three skills the person has and the CV printing them.
PAGE_QUERIES = 27
