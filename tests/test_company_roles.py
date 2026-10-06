"""A company plays a role by what it does, read from its industries (#671).

The maintainer's list: employment services are companies with the right NACE code, and are
the only kind that can be the company a listing came through. The registry in
`jobs/roles.py` holds the question; these tests hold what it answers. Nothing is stored for
it, so no archive format moves.
"""

from __future__ import annotations

import pytest
from django.utils import translation

from postulo.jobs import industries, roles
from postulo.jobs.models import Company, CompanyKind, Industry

pytestmark = pytest.mark.django_db


def company(owner, name, *industry_names, kind=CompanyKind.EMPLOYER):
    made = Company.objects.create(owner=owner, name=name, kind=kind)
    if industry_names:
        made.industries.set(Industry.named(owner, industry_names))
    return made


def test_the_intermediary_is_division_78_and_the_public_service_qualifies_alone():
    intermediary = roles.role(roles.INTERMEDIARY)

    assert intermediary.divisions == {"78"}
    assert intermediary.kinds == {CompanyKind.EMPLOYMENT_SERVICE.value}
    assert industries.name_for("78", "en") == "Employment activities"


def test_the_place_of_learning_is_division_85_and_nothing_else():
    learning = roles.role(roles.PLACE_OF_LEARNING)

    assert learning.divisions == {"85"}
    assert learning.kinds == frozenset()
    assert industries.name_for("85", "en") == "Education"
    assert industries.section_of("85") == "Q"


def test_every_division_a_role_names_exists_in_the_vendored_file():
    known = industries.classification()["divisions"]
    for role in roles.ROLES.values():
        assert role.divisions <= set(known), role.key


def test_a_company_with_the_education_industry_qualifies_as_a_place_of_learning(user):
    school = company(user, "Aveiro", "Education")
    bank = company(user, "Bank", "Banking")
    bare = company(user, "Bare")
    agency = company(user, "Hays", "Employment activities")

    learning = Company.objects.for_user(user).qualifying(roles.PLACE_OF_LEARNING)

    assert list(learning) == [school]
    assert not {bank, bare, agency} & set(learning)


@pytest.mark.parametrize("language", ["fr", "pt-PT"])
def test_the_translated_names_of_education_qualify_too(user, language):
    name = industries.name_for("85", language)
    assert name and name != "Education"
    with translation.override(language):
        made = company(user, f"Ecole {language}", name)

    assert made in Company.objects.for_user(user).qualifying(roles.PLACE_OF_LEARNING)


def test_an_unknown_role_is_refused_not_answered_with_nothing(user):
    with pytest.raises(ValueError):
        roles.role("landlord")
    with pytest.raises(ValueError):
        Company.objects.for_user(user).qualifying("landlord")


def test_a_company_in_employment_activities_qualifies_and_one_without_does_not(user):
    agency = company(user, "Hays", "Employment activities")
    bank = company(user, "Bank", "Banking")
    bare = company(user, "Bare")

    assert agency.industries.get().code == "78"
    assert list(Company.objects.for_user(user).qualifying()) == [agency]
    assert bank not in Company.objects.for_user(user).qualifying()
    assert bare not in Company.objects.for_user(user).qualifying()


def test_a_company_with_two_matching_industries_is_listed_once(user):
    both = company(user, "Hays", "Employment activities", "Banking")

    assert list(Company.objects.for_user(user).qualifying()) == [both]


@pytest.mark.parametrize("language", ["fr", "pt-PT", "de", "es"])
def test_the_translated_names_of_the_division_qualify_in_the_readers_language(user, language):
    """`Industry.code` follows the name in the language the reader has (or English)."""
    name = industries.name_for("78", language)
    assert name and name != "Employment activities"

    with translation.override(language):
        made = company(user, f"Agence {language}", name)

    assert made.industries.get().code == "78"
    assert made in Company.objects.for_user(user).qualifying()


def test_a_public_employment_service_always_qualifies(user):
    office = company(user, "France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE)

    assert office.industries.count() == 0
    assert office in Company.objects.for_user(user).qualifying()


def test_one_owners_qualifying_set_never_holds_anothers(user, other_user):
    company(user, "Hays", "Employment activities")
    theirs = company(other_user, "Randstad", "Employment activities")
    office = company(other_user, "Pole emploi", kind=CompanyKind.EMPLOYMENT_SERVICE)

    mine = Company.objects.for_user(user).qualifying()

    assert theirs not in mine and office not in mine
    assert {c.owner_id for c in mine} == {user.pk}
