"""One social profile to a service (#925) and a primary identifier (#922), on the server."""

from __future__ import annotations

import re

import pytest
from django.core.exceptions import ValidationError

from postulo.accounts import primary_identifier as person_identifiers
from postulo.accounts.forms import PersonIdentifierFormSet
from postulo.accounts.models import PersonIdentifier
from postulo.core import web_links
from postulo.core.models import WebLink

pytestmark = pytest.mark.django_db


def social(user, url, service):
    return WebLink.objects.create(
        owner=user,
        holder=user.profile,
        kind=web_links.Kind.SOCIAL,
        service=service,
        url=url,
    )


def posted(user, *entries, initial=0):
    data = {
        "social_profiles-TOTAL_FORMS": str(len(entries)),
        "social_profiles-INITIAL_FORMS": str(initial),
        "social_profiles-MIN_NUM_FORMS": "0",
        "social_profiles-MAX_NUM_FORMS": "1000",
    }
    for index, entry in enumerate(entries):
        for name, value in entry.items():
            data[f"social_profiles-{index}-{name}"] = value
    return web_links.formset_for(user.profile, web_links.Kind.SOCIAL, data=data)


def test_a_second_row_on_one_service_is_refused(user):
    social(user, "https://www.linkedin.com/in/alex", "linkedin")
    stored = WebLink.objects.get()
    formset = posted(
        user,
        {"id": str(stored.pk), "service": "linkedin", "url": stored.url},
        {"service": "linkedin", "url": "linkedin-two"},
        initial=1,
    )

    assert not formset.is_valid()
    assert "already used by another row" in str(formset.forms[1].errors["service"])


def test_other_and_a_federated_service_and_repositories_repeat(user):
    formset = posted(
        user,
        {"service": "other", "label": "a", "url": "https://example.org/a"},
        {"service": "other", "label": "b", "url": "https://example.org/b"},
        {"service": "mastodon", "url": "https://mastodon.social/@a"},
        {"service": "mastodon", "url": "https://hachyderm.io/@a"},
    )
    assert formset.is_valid(), formset.errors
    assert not web_links.one_row_per_service(web_links.Kind.REPOSITORY, "github")


def test_rows_that_already_share_a_service_are_kept_and_flagged(user):
    first = social(user, "https://www.linkedin.com/in/one", "linkedin")
    second = social(user, "https://www.linkedin.com/in/two", "linkedin")
    formset = posted(
        user,
        {"id": str(first.pk), "service": "linkedin", "url": first.url},
        {"id": str(second.pk), "service": "linkedin", "url": second.url},
        initial=2,
    )

    assert formset.is_valid(), formset.errors
    assert all(form.shares_its_service for form in formset.forms)


def test_the_select_switches_off_a_service_another_row_holds(user):
    social(user, "https://www.linkedin.com/in/alex", "linkedin")
    formset = web_links.formset_for(user.profile, web_links.Kind.SOCIAL)

    html = str(formset.forms[-1]["service"])
    assert re.search(
        r'<option[^>]*value="linkedin"[^>]*disabled|<option[^>]*disabled[^>]*value="linkedin"', html
    )


def test_the_api_refuses_two_rows_on_one_service():
    rows = [
        {"kind": "social", "url": "https://www.linkedin.com/in/a"},
        {"kind": "social", "url": "https://www.linkedin.com/in/b"},
    ]
    with pytest.raises(ValidationError):
        web_links.checked_rows(rows)


# -------------------------------------------------------------- identifiers (#922)


def test_the_first_identifier_is_the_primary_and_one_other_can_take_it(user):
    first = PersonIdentifier.objects.create(profile=user.profile, scheme="wikidata", value="Q95")
    second = PersonIdentifier.objects.create(
        profile=user.profile, scheme="orcid", value="0000-0002-1825-0097"
    )
    first.refresh_from_db()
    assert first.is_primary and not second.is_primary

    person_identifiers.set_primary(user.profile, second)

    assert list(PersonIdentifier.objects.filter(is_primary=True)) == [second]
    assert PersonIdentifier.objects.first() == second


def test_removing_the_primary_hands_it_on(user):
    first = PersonIdentifier.objects.create(profile=user.profile, scheme="wikidata", value="Q95")
    second = PersonIdentifier.objects.create(
        profile=user.profile, scheme="orcid", value="0000-0002-1825-0097"
    )
    first.delete()

    assert person_identifiers.ensure_one_primary(user.profile) == second
    second.refresh_from_db()
    assert second.is_primary


def test_the_archive_carries_the_primary(user):
    from postulo.core import export

    PersonIdentifier.objects.create(profile=user.profile, scheme="wikidata", value="Q95")
    rows = export._identifier_rows(user.profile)

    assert rows[0]["is_primary"] is True
    assert PersonIdentifierFormSet is not None
