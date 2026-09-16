"""An identifier is the same identifier whatever case it is typed in (#211).

Every named scheme folds its own values — Wikidata to upper, LinkedIn to lower — so for new
values this was already true. Two things were not: rows written before a scheme gained its
folding, and the *other* scheme, which folds nothing on purpose so that a staff number typed
`AB-12` still reads `AB-12`. That makes the comparison the place to be case-blind, rather
than the stored value.
"""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError
from django.urls import reverse

from postulo.jobs import services
from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db


def a_company(user, name="Black Mesa") -> Company:
    return Company.objects.create(owner=user, name=name)


# ------------------------------------------------------------------ the stored value


def test_a_named_scheme_still_folds_what_it_stores(user):
    company = a_company(user)

    services.set_identifiers(company, [("wikidata", "q95", "")])

    assert company.identifiers.get().value == "Q95", "the canonical spelling is what is kept"


def test_other_keeps_the_case_it_was_typed_in(user):
    company = a_company(user)

    services.set_identifiers(company, [("other", "AB-12", "Staff number")])

    assert company.identifiers.get().value == "AB-12", "a staff number reads as it was written"


# --------------------------------------------------------------- finding one again


def test_a_lookup_finds_a_row_written_before_the_folding(user):
    """The case this issue is really about: an early import wrote `q95` and nothing found it."""
    company = a_company(user)
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value="q95")

    assert Company.by_identifier(user, "wikidata", "Q95") == company
    assert Company.by_identifier(user, "wikidata", "q95") == company


def test_a_linkedin_slug_matches_whatever_case_it_is_asked_for(user):
    company = a_company(user)
    services.set_identifiers(company, [("linkedin", "Aperture-Science", "")])

    assert Company.by_identifier(user, "linkedin", "aperture-science") == company
    assert Company.by_identifier(user, "linkedin", "APERTURE-SCIENCE") == company


def test_a_company_already_carrying_it_in_another_case_is_not_given_it_twice(user):
    """Otherwise the second row meets the constraint, and the person meets a 500."""
    company = a_company(user)
    early = CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="wikidata", value="q95"
    )

    services.set_identifiers(company, [("wikidata", "Q95", "")])

    assert company.identifiers.get().pk == early.pk, "the row it already had, not a second one"


# ------------------------------------------------------- one identifier, one company


def test_the_same_identifier_in_another_case_is_refused_for_a_second_company(user):
    first = a_company(user)
    CompanyIdentifier.objects.create(owner=user, company=first, scheme="wikidata", value="q95")
    second = a_company(user, "Aperture Science")

    with pytest.raises(Exception) as refusal:
        services.set_identifiers(second, [("wikidata", "Q95", "")])

    assert "already carries" in str(refusal.value)
    assert not second.identifiers.exists()


def test_two_people_may_each_hold_the_same_identifier(user, other_user):
    mine = a_company(user)
    theirs = a_company(other_user)

    services.set_identifiers(mine, [("wikidata", "Q95", "")])
    services.set_identifiers(theirs, [("wikidata", "q95", "")])

    assert mine.identifiers.get().value == theirs.identifiers.get().value == "Q95"


def test_other_cannot_be_listed_twice_on_one_company_in_two_cases(user):
    company = a_company(user)
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="other", value="AB-12", label="Staff number"
    )

    with pytest.raises(IntegrityError):
        CompanyIdentifier.objects.create(
            owner=user, company=company, scheme="other", value="ab-12", label="staff number"
        )


def test_the_form_says_so_before_the_database_does(client, user):
    company = a_company(user)
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="other", value="AB-12", label="Staff"
    )
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {
            "name": company.name,
            "kind": company.kind,
            "identifiers-TOTAL_FORMS": "2",
            "identifiers-INITIAL_FORMS": "0",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-scheme": "other",
            "identifiers-0-value": "AB-12",
            "identifiers-0-label": "Staff",
            "identifiers-1-scheme": "other",
            "identifiers-1-value": "ab-12",
            "identifiers-1-label": "Staff",
        },
    )

    assert response.status_code == 200, "refused on the form rather than raised by the database"
    assert "already listed" in response.content.decode()


# ------------------------------------------------------------------- case, not accents


def test_only_case_is_ignored_and_not_accents(user):
    """Deliberate: `Lower()` follows the database's own rules, and nothing here folds accents."""
    company = a_company(user)
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="other", value="café-1", label="Desk"
    )

    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="other", value="cafe-1", label="Desk"
    )

    assert company.identifiers.count() == 2, "an accent is a different character, not a case"
