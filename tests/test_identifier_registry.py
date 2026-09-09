"""One registry with a subject matrix, because ISNI is both (#109).

> another internal plugin : postulo-identifiers
> must include all identifiers from both companies as users with a ownership matrix so no
> valid company identifier is shown on the user data

The separation asked for already existed and was the least interesting part: a company
scheme could not appear on a person **by convention**, because of which module a form
imported its choices from. A convention holds until somebody wires a form up differently.
The first half of this file is that convention becoming a guarantee — refused at the model,
by every route in, including the ones that never call `full_clean`.

The second half is what the separation cost. Three schemes identify people *and*
organisations, and keeping the sets in two files that never had to agree had quietly picked
a side for each. Nobody filed a bug; a researcher simply could not record their Wikidata
item, and a university could not record its ISNI.

The third is the promise that none of this got weaker. Every pattern and every checksum
came across, and `tests/test_identifiers.py` and `tests/test_person_identifiers.py` pass
unchanged rather than adjusted to fit — which is the check that matters most, because a
merge is exactly where a validation quietly goes missing.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from postulo.accounts import identifiers as person_schemes
from postulo.accounts.models import PersonIdentifier
from postulo.core import identifiers as registry
from postulo.jobs import identifiers as company_schemes
from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db


# ------------------------------------------------------- the guarantee, made a guarantee


def test_no_company_scheme_is_offered_to_a_person():
    offered = set(person_schemes.schemes())

    assert "lei" not in offered
    assert "register" not in offered
    assert "crunchbase" not in offered and "opencorporates" not in offered


def test_no_person_only_scheme_is_offered_to_a_company():
    offered = set(company_schemes.schemes())

    assert "orcid" not in offered
    assert "researcherid" not in offered and "scopus" not in offered


def test_a_person_cannot_be_given_a_company_identifier_by_any_route(user):
    """Refused at the model, not merely absent from a dropdown.

    ``objects.create`` is the route a form never takes and an importer or a plugin might,
    and it is the one that decides whether this is a guarantee or a habit.
    """
    with pytest.raises(ValidationError):
        PersonIdentifier.objects.create(
            profile=user.profile, scheme="lei", value="HWUPKR0MPOU8FGXBT394"
        )

    assert not PersonIdentifier.objects.exists()


def test_a_company_cannot_be_given_a_persons_identifier_by_any_route(user):
    company = Company.objects.create(owner=user, name="Aperture Science")

    with pytest.raises(ValidationError):
        CompanyIdentifier.objects.create(
            owner=user, company=company, scheme="orcid", value="0000-0002-1825-0097"
        )

    assert not CompanyIdentifier.objects.exists()


def test_the_form_and_the_field_say_the_same_thing(user):
    """The field validator, which is what travels with the column."""
    row = PersonIdentifier(profile=user.profile, scheme="lei", value="HWUPKR0MPOU8FGXBT394")

    with pytest.raises(ValidationError) as raised:
        row.full_clean()

    assert "scheme" in raised.value.error_dict


def test_cleaning_a_value_under_the_wrong_subjects_scheme_is_refused():
    """Not "badly formatted" but "no such scheme", which is the honest answer here."""
    with pytest.raises(ValidationError) as raised:
        person_schemes.clean("lei", "HWUPKR0MPOU8FGXBT394")

    assert raised.value.code == "scheme"


# --------------------------------------------------- and what the separation used to cost


def test_a_university_can_record_its_isni(user):
    """ISNI identifies *contributors and organisations* — that is its own definition.

    It was filed under people, so an institution could not record the identifier an EU
    application form asks it for.
    """
    company = Company.objects.create(owner=user, name="Universidade de Lisboa")

    row = CompanyIdentifier(owner=user, company=company, scheme="isni", value="0000000122819550")
    row.full_clean()
    row.save()

    row.refresh_from_db()
    assert row.value == "0000 0001 2281 9550"
    assert row.url == "https://isni.org/isni/0000 0001 2281 9550"


def test_a_researcher_can_record_their_wikidata_item(user):
    row = PersonIdentifier.objects.create(
        profile=user.profile, scheme="wikidata", value="https://www.wikidata.org/wiki/Q42"
    )
    row.full_clean()

    assert row.value == "Q42"


def test_a_linkedin_profile_and_a_company_page_are_different_addresses(user):
    """One scheme, two subjects, and the link cannot be the same for both.

    A registry that could not say this would have had to keep LinkedIn on one side, which is
    exactly the loss the matrix exists to stop.
    """
    company = Company.objects.create(owner=user, name="Aperture Science")
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="linkedin", value="aperture-science"
    )
    PersonIdentifier.objects.create(profile=user.profile, scheme="linkedin", value="cave-johnson")

    assert (
        CompanyIdentifier.objects.get().url == "https://www.linkedin.com/company/aperture-science/"
    )
    assert PersonIdentifier.objects.get().url == "https://www.linkedin.com/in/cave-johnson/"


def test_three_schemes_say_both_and_the_rest_pick_one():
    both = {key for key, scheme in registry.registry().items() if len(scheme.subjects) == 2}

    assert both == {"isni", "wikidata", "linkedin", "other"}


def test_other_is_available_to_both_and_always_was():
    """The one key that was in both registries — and the hint the matrix was the right model."""
    assert "other" in person_schemes.schemes()
    assert "other" in company_schemes.schemes()


# ----------------------------------------------------- nothing got weaker in the merge


@pytest.mark.parametrize(
    ("key", "typed", "expected"),
    [
        ("orcid", "https://orcid.org/0000-0002-1825-0097", "0000-0002-1825-0097"),
        ("orcid", "0000000218250097", "0000-0002-1825-0097"),
        ("scopus", "Author ID 7004212771", "7004212771"),
        ("isni", "0000000122819550", "0000 0001 2281 9550"),
    ],
)
def test_a_persons_value_is_still_tidied_the_way_it_was(key, typed, expected):
    assert person_schemes.normalise(key, typed) == expected


@pytest.mark.parametrize(
    ("key", "typed", "expected"),
    [
        ("wikidata", "https://www.wikidata.org/wiki/Q95", "Q95"),
        ("lei", "HWUPKR0MPOU8 FGXBT394", "HWUPKR0MPOU8FGXBT394"),
        ("register", "PT501234567", "PT 501234567"),
        ("opencorporates", "https://opencorporates.com/companies/gb/01234567", "gb/01234567"),
    ],
)
def test_a_companys_value_is_still_tidied_the_way_it_was(key, typed, expected):
    assert company_schemes.normalise(key, typed) == expected


def test_both_checksums_came_across():
    with pytest.raises(ValidationError) as orcid:
        person_schemes.clean("orcid", "0000-0002-1825-0098")
    assert orcid.value.code == "checksum"

    with pytest.raises(ValidationError) as lei:
        company_schemes.clean("lei", "HWUPKR0MPOU8FGXBT395")
    assert lei.value.code == "checksum"


def test_an_address_on_somebody_elses_site_is_not_lifted():
    """The guard that stops a link on another host being read as an identifier."""
    assert person_schemes.normalise("orcid", "https://example.org/0000-0002-1825-0097") == (
        "HTTPS://EXAMPLE.ORG/0000-0002-1825-0097"
    )


# --------------------------------------------------------------- what kind of plugin


def test_the_schemes_come_from_a_plugin():
    from postulo.plugins import registry as plugins

    found = plugins.plugins("identifier")

    assert [plugin.name for plugin in found] == ["identifiers"]
    assert len(found[0].schemes) == len(registry.registry())


def test_nothing_outside_this_process_can_contribute_a_scheme():
    """Internal only, and enforced rather than intended: the kind advertises no group.

    A plugin per national company register is the obvious next thing — `register` is one
    generic scheme for SIRET, NIF, Companies House, KvK and Handelsregister alike — but a
    third-party contract is a promise about breakage, and that one is not written yet.
    """
    from postulo.plugins.registry import GROUPS, _load_third_party

    assert GROUPS["identifier"] == ""
    assert _load_third_party("identifier") == []


def test_a_registry_is_not_somebodys_to_switch_off():
    """Every other kind answers "is this on for this person"; this one answers "what does
    this key mean". Off would leave every stored identifier without a label or a check.
    """
    from postulo.plugins.policy import GOVERNED_KINDS, UNGOVERNED_KINDS

    assert "identifier" in UNGOVERNED_KINDS
    assert "identifier" not in GOVERNED_KINDS


def test_the_tables_stay_in_core():
    """What makes this able to be a plugin at all: a scheme owns no rows.

    #100 wanted a plugin to own a table, and plugins cannot. This one contributes vocabulary
    to two models that core defines and core migrates.
    """
    assert PersonIdentifier._meta.app_label == "accounts"
    assert CompanyIdentifier._meta.app_label == "jobs"


def test_the_scheme_column_no_longer_freezes_a_list_into_every_migration():
    assert PersonIdentifier._meta.get_field("scheme").choices is None
    assert CompanyIdentifier._meta.get_field("scheme").choices is None


def test_no_scheme_reaches_the_network():
    """Said twice in the modules this replaced, and true of the merged one too."""
    import inspect

    from postulo.plugins.identifiers import schemes

    source = inspect.getsource(schemes)
    code = source.split('"""', 2)[2]

    for forbidden in ("requests", "urlopen", "httpx", "socket"):
        assert forbidden not in code
