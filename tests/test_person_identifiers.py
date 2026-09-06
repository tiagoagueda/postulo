"""A person's external identifiers, starting with ORCID.

A name is not an identity. Two researchers share one, one researcher publishes under three,
and a marriage or a transliteration turns one into another — which is what ORCID exists to
fix, and why an application form for an academic post asks for it by name.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.accounts import identifiers
from postulo.accounts.models import PersonIdentifier

pytestmark = pytest.mark.django_db

# The example ORCID from the specification, which really does check out.
ORCID = "0000-0002-1825-0097"


# ------------------------------------------------------------------- the value


@pytest.mark.parametrize(
    "typed",
    [
        ORCID,
        "https://orcid.org/0000-0002-1825-0097",
        "http://orcid.org/0000-0002-1825-0097/",
        "0000000218250097",
        "0000 0002 1825 0097",
        "  0000-0002-1825-0097  ",
    ],
)
def test_however_it_was_pasted_it_ends_up_the_same(typed):
    """Pasting the whole address is the common case, not the exception."""
    assert identifiers.clean(identifiers.ORCID, typed) == ORCID


def test_an_orcid_that_fails_its_own_checksum_is_a_typo(caplog):
    """Which is exactly why ORCID has one, and why Postulo never has to ask orcid.org."""
    with pytest.raises(ValidationError) as raised:
        identifiers.clean(identifiers.ORCID, "0000-0002-1825-0098")
    assert raised.value.code == "checksum"


def test_the_trailing_x_is_a_real_check_digit_not_a_mistake():
    """Ten is written X, so an ORCID ending in a letter is perfectly ordinary."""
    assert identifiers.clean(identifiers.ORCID, "0000-0002-1694-233X") == "0000-0002-1694-233X"


def test_something_the_wrong_shape_says_what_the_right_one_looks_like():
    with pytest.raises(ValidationError) as raised:
        identifiers.clean(identifiers.ORCID, "not an orcid")
    assert raised.value.code == "format"


def test_an_address_somewhere_else_is_not_lifted():
    """A link on somebody else's site is not an ORCID, whatever its path says."""
    with pytest.raises(ValidationError):
        identifiers.clean(identifiers.ORCID, "https://example.org/0000-0002-1825-0097")


def test_where_an_identifier_links():
    assert identifiers.url_for(identifiers.ORCID, ORCID) == f"https://orcid.org/{ORCID}"
    assert identifiers.url_for(identifiers.OTHER, "staff-4711") == "", "nowhere to link"


def test_the_other_schemes_are_there_for_when_they_are_asked_for():
    for key in (identifiers.RESEARCHERID, identifiers.SCOPUS, identifiers.ISNI):
        assert key in identifiers.SCHEMES
    assert identifiers.clean(identifiers.ISNI, "0000000122819553") == "0000 0001 2281 9553"
    assert identifiers.clean(identifiers.SCOPUS, "7004212771") == "7004212771"


# ------------------------------------------------------------------- the model


def test_the_model_tidies_what_it_is_given(user):
    """On the model, so an import or a plugin gets the same answer as a person typing."""
    row = PersonIdentifier(
        profile=user.profile, scheme=identifiers.ORCID, value="https://orcid.org/" + ORCID
    )
    row.full_clean()
    row.save()

    assert row.value == ORCID
    assert row.url == f"https://orcid.org/{ORCID}"


def test_one_of_each_kind_and_no_more(user):
    """Nobody has two ORCIDs. A second one means one of them is wrong."""
    from django.db import IntegrityError, transaction

    PersonIdentifier.objects.create(profile=user.profile, scheme=identifiers.ORCID, value=ORCID)
    with pytest.raises(IntegrityError), transaction.atomic():
        PersonIdentifier.objects.create(
            profile=user.profile, scheme=identifiers.ORCID, value="0000-0002-1694-233X"
        )


def test_other_is_a_labelled_free_slot_and_may_repeat(user):
    """Somebody may have two staff numbers; they may not have two ORCIDs."""
    PersonIdentifier.objects.create(
        profile=user.profile, scheme=identifiers.OTHER, value="4711", label="Staff number"
    )
    PersonIdentifier.objects.create(
        profile=user.profile, scheme=identifiers.OTHER, value="A-99", label="Library card"
    )
    assert user.profile.identifiers.count() == 2


def test_they_belong_to_one_person(user, other_user):
    """Attached to the profile, so nobody else's account can see or reach them."""
    PersonIdentifier.objects.create(profile=user.profile, scheme=identifiers.ORCID, value=ORCID)

    assert user.profile.identifiers.count() == 1
    assert other_user.profile.identifiers.count() == 0


# -------------------------------------------------------------------- the page


def test_the_profile_page_offers_them(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-identifiers" in html
    assert "ORCID" in html
    assert "0000-0002-1825-0097" in html, "the example, so nobody has to guess the shape"


def test_saving_one_from_the_page(client, user):
    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "headline": "",
            "phone_0": "",
            "phone_1": "",
            "location": "",
            "website": "",
            "linkedin_url": "",
            "source_repo_url": "",
            "identifiers-TOTAL_FORMS": "1",
            "identifiers-INITIAL_FORMS": "0",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-scheme": identifiers.ORCID,
            "identifiers-0-value": f"https://orcid.org/{ORCID}",
            "identifiers-0-label": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    row = user.profile.identifiers.get()
    assert (row.scheme, row.value) == (identifiers.ORCID, ORCID), "pasted whole, stored tidy"


def test_a_wrong_checksum_is_refused_with_the_reason(client, user):
    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "",
            "last_name": "",
            "headline": "",
            "phone_0": "",
            "phone_1": "",
            "location": "",
            "website": "",
            "linkedin_url": "",
            "source_repo_url": "",
            "identifiers-TOTAL_FORMS": "1",
            "identifiers-INITIAL_FORMS": "0",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-scheme": identifiers.ORCID,
            "identifiers-0-value": "0000-0002-1825-0098",
            "identifiers-0-label": "",
        },
    )

    assert response.status_code == 200
    assert "does not match the rest" in response.content.decode()
    assert not user.profile.identifiers.exists()


# ---------------------------------------------------------------------- on a CV


def test_an_identifier_appears_in_a_cv_contact_block(user):
    """Beside the website and the LinkedIn address, which is where a reader looks."""
    from postulo.documents.models import CV
    from postulo.documents.rendering import render_cv_html

    PersonIdentifier.objects.create(profile=user.profile, scheme=identifiers.ORCID, value=ORCID)
    cv = CV.objects.create(owner=user, name="Research", show_contact_details=True)

    html = render_cv_html(cv)
    assert ORCID in html
    assert "ORCID" in html


def test_a_cv_with_no_contact_block_shows_none_of_it(user):
    from postulo.documents.models import CV
    from postulo.documents.rendering import render_cv_html

    PersonIdentifier.objects.create(profile=user.profile, scheme=identifiers.ORCID, value=ORCID)
    cv = CV.objects.create(owner=user, name="Anonymous", show_contact_details=False)

    assert ORCID not in render_cv_html(cv)


# --------------------------------------------------------------------- exported


def test_identifiers_travel_in_the_export(user):
    from postulo.core import export

    PersonIdentifier.objects.create(profile=user.profile, scheme=identifiers.ORCID, value=ORCID)

    document = export.build_document(user)
    rows = document["account"]["identifiers"]
    assert rows == [{"scheme": identifiers.ORCID, "value": ORCID, "label": ""}]
