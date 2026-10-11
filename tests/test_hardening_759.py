"""A made-up identifier id and another account's get the same answer on the company form (#759)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db


def post_with_row_id(client, company, row_id, *, create=False):
    data = {
        "name": "Aperture" if create else company.name,
        "kind": "employer",
        "identifiers-TOTAL_FORMS": "1",
        "identifiers-INITIAL_FORMS": "1",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-id": str(row_id),
        "identifiers-0-scheme": "wikidata",
        "identifiers-0-value": "Q95",
        "identifiers-0-label": "",
    }
    url = (
        reverse("jobs:company_create")
        if create
        else reverse("jobs:company_update", args=[company.pk])
    )
    return client.post(url, data)


@pytest.fixture
def theirs(other_user):
    company = Company.objects.create(owner=other_user, name="Black Mesa")
    return CompanyIdentifier.objects.create(
        owner=other_user, company=company, scheme="wikidata", value="Q1"
    )


@pytest.mark.parametrize("create", [False, True], ids=["update", "create"])
def test_a_row_that_does_not_exist_and_one_that_is_somebody_elses_answer_alike(
    client, user, theirs, create
):
    mine = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    made_up = post_with_row_id(client, mine, 99999999, create=create)
    another = post_with_row_id(client, mine, theirs.pk, create=create)

    assert made_up.status_code == another.status_code
    assert made_up.status_code in (200, 302)
    if made_up.status_code == 302:
        assert made_up["Location"].split("?")[0] == another["Location"].split("?")[0] or create
    theirs.refresh_from_db()
    assert theirs.value == "Q1" and theirs.company.owner != user


def test_the_other_accounts_row_is_not_changed_and_none_is_made_for_it(client, user, theirs):
    mine = Company.objects.create(owner=user, name="Aperture")
    before = CompanyIdentifier.objects.count()
    client.force_login(user)

    post_with_row_id(client, mine, theirs.pk)

    theirs.refresh_from_db()
    assert (theirs.scheme, theirs.value) == ("wikidata", "Q1")
    assert CompanyIdentifier.objects.filter(company__owner=user).count() <= 1
    assert CompanyIdentifier.objects.count() <= before + 1
