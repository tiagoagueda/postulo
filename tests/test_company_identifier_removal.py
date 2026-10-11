"""A company's identifier goes by its bin, after a question, and not on the next Save (#857)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db

LEI = "5493001KJTIIGC8Y1R12"


@pytest.fixture
def company(user):
    acme = Company.objects.create(owner=user, name="Acme")
    CompanyIdentifier.objects.create(owner=user, company=acme, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(owner=user, company=acme, scheme="lei", value=LEI)
    return acme


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def remove_url(identifier) -> str:
    return reverse("jobs:company_identifier_remove", args=[identifier.pk])


def test_the_form_draws_a_bin_and_a_dialog_for_each_saved_identifier(signed_in, company):
    html = signed_in.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    for identifier in company.identifiers.all():
        assert f'popovertarget="remove-company-identifier-{identifier.pk}"' in html
        dialog = re.search(
            rf'<dialog[^>]*id="remove-company-identifier-{identifier.pk}".*?</dialog>', html, re.S
        )[0]
        assert f'action="{remove_url(identifier)}"' in dialog
        assert "data-remove-row" in dialog and "without waiting for Save" in dialog
        assert identifier.value in dialog, "the dialog says which identifier"
    assert "data-remove-trigger" in html
    assert 'name="identifiers-0-DELETE"' not in html, "no tick box any more for a saved row"
    assert "data-removed-said" in html, "and the live region the script writes into"


def test_a_new_company_has_nothing_to_remove(signed_in):
    html = signed_in.get(reverse("jobs:company_create")).content.decode()

    assert "data-remove-trigger" not in html and "remove-company-identifier" not in html


def test_confirming_removes_the_identifier_at_once_and_says_so(signed_in, company, user):
    wikidata = company.identifiers.get(scheme="wikidata")

    response = signed_in.post(remove_url(wikidata), headers={"HX-Request": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["removed"] is True and "Q95" in body["said"] and body["count"] == 1
    assert not CompanyIdentifier.objects.filter(pk=wikidata.pk).exists()
    assert company.identifiers.filter(scheme="lei").exists(), "and nothing else"


def test_without_a_script_it_removes_and_returns_to_the_form_with_a_message(signed_in, company):
    lei = company.identifiers.get(scheme="lei")

    response = signed_in.post(remove_url(lei), follow=True)

    assert response.redirect_chain[-1][0].endswith(
        f"{reverse('jobs:company_update', args=[company.pk])}#section-identifiers"
    )
    assert "removed." in response.content.decode()
    assert not CompanyIdentifier.objects.filter(pk=lei.pk).exists()


def test_a_get_removes_nothing(signed_in, company):
    wikidata = company.identifiers.get(scheme="wikidata")

    response = signed_in.get(remove_url(wikidata))

    assert response.status_code == 302
    assert CompanyIdentifier.objects.filter(pk=wikidata.pk).exists()


def test_somebody_elses_identifier_and_one_that_never_was_answer_alike(
    signed_in, company, other_user
):
    foreign_company = Company.objects.create(owner=other_user, name="Black Mesa")
    foreign = CompanyIdentifier.objects.create(
        owner=other_user, company=foreign_company, scheme="wikidata", value="Q1"
    )

    theirs = signed_in.post(remove_url(foreign))
    nobodys = signed_in.post(reverse("jobs:company_identifier_remove", args=[999999]))

    assert theirs.status_code == nobodys.status_code == 404
    assert CompanyIdentifier.objects.filter(pk=foreign.pk).exists()


def test_a_signed_out_visitor_is_sent_to_sign_in(client, company):
    wikidata = company.identifiers.get(scheme="wikidata")

    response = client.post(remove_url(wikidata))

    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
    assert CompanyIdentifier.objects.filter(pk=wikidata.pk).exists()


def test_saving_a_copy_of_the_form_that_still_has_a_removed_row_is_not_an_error(signed_in, company):
    """A second tab, or the Back button's copy, still posts the key of a row that has gone."""
    wikidata = company.identifiers.get(scheme="wikidata")
    lei = company.identifiers.get(scheme="lei")
    page = signed_in.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    data = {
        "name": "Acme",
        "identifiers-TOTAL_FORMS": "3",
        "identifiers-INITIAL_FORMS": "2",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-id": str(wikidata.pk),
        "identifiers-0-scheme": "wikidata",
        "identifiers-0-value": "Q95",
        "identifiers-1-id": str(lei.pk),
        "identifiers-1-scheme": "lei",
        "identifiers-1-value": LEI,
        "identifiers-2-scheme": "",
        "identifiers-2-value": "",
    }
    assert "identifiers-0-id" in page
    signed_in.post(remove_url(wikidata))

    response = signed_in.post(reverse("jobs:company_update", args=[company.pk]), data)

    assert response.status_code == 302, "saved, not sent back with an error"
    assert company.identifiers.filter(scheme="lei").exists()
    assert not company.identifiers.filter(scheme="wikidata").exists()
