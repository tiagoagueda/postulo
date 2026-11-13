"""A company's acronyms and short names, and which name the lists show (#1245)."""

from __future__ import annotations

import io
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer, search
from postulo.jobs import duplicates, merging
from postulo.jobs.models import Company, CompanyName, JobPosting

FULL = "Centre National de la Recherche Scientifique"


def acronym(user, company, name="CNRS"):
    return CompanyName.objects.create(
        owner=user,
        company=company,
        kind=CompanyName.Kind.ACRONYM,
        name=name,
        source=CompanyName.Source.TYPED,
    )


def post(client, company, **data):
    return client.post(reverse("jobs:company_names", args=[company.pk]), data)


def test_a_company_without_an_acronym_shows_its_name(user):
    company = Company.objects.create(owner=user, name=FULL)

    assert company.display_name == FULL
    assert company.acronyms() == []


def test_the_lists_show_the_chosen_acronym_and_changing_it_changes_every_list(client, user):
    company = Company.objects.create(owner=user, name=FULL)
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    acronym(user, company)
    client.force_login(user)

    assert f"<bdi>{FULL}</bdi>" in client.get(reverse("jobs:company_list")).content.decode()

    post(client, company, shown="CNRS")
    company.refresh_from_db()

    assert company.display_name == "CNRS"
    for url in (reverse("jobs:company_list"), reverse("listings:list")):
        page = client.get(url).content.decode()
        assert "<bdi>CNRS</bdi>" in page and f"<bdi>{FULL}</bdi>" not in page, url
    assert posting.company.display_name == "CNRS"

    post(client, company, shown="")
    company.refresh_from_db()
    assert company.display_name == FULL
    assert f"<bdi>{FULL}</bdi>" in client.get(reverse("listings:list")).content.decode()


def test_the_company_page_names_the_one_not_shown(client, user):
    company = Company.objects.create(owner=user, name=FULL)
    acronym(user, company)
    client.force_login(user)
    post(client, company, shown="CNRS")

    page = client.get(company.get_absolute_url()).content.decode()

    assert "data-company-other-names" in page
    assert FULL in page


def test_only_a_name_the_company_holds_can_be_chosen(client, user):
    company = Company.objects.create(owner=user, name=FULL)
    client.force_login(user)

    post(client, company, shown="EDF")
    company.refresh_from_db()

    assert company.list_name == ""


def test_removing_the_chosen_acronym_returns_the_lists_to_the_full_name(client, user):
    company = Company.objects.create(owner=user, name=FULL)
    row = acronym(user, company)
    client.force_login(user)
    post(client, company, shown="CNRS")

    post(client, company, remove=row.pk)
    company.refresh_from_db()

    assert company.display_name == FULL


def test_an_acronym_is_typed_once_and_never_as_the_name_itself(client, user):
    company = Company.objects.create(owner=user, name=FULL)
    client.force_login(user)

    post(client, company, acronym="CNRS")
    post(client, company, acronym="cnrs")
    post(client, company, acronym=FULL.upper())

    assert [row.name for row in company.acronyms()] == ["CNRS"]
    assert company.acronyms()[0].language == ""


def test_an_acronym_is_not_a_name_in_a_language(user):
    company = Company.objects.create(owner=user, name=FULL)
    acronym(user, company)
    CompanyName.objects.create(
        owner=user, company=company, language="en", name="National Centre", source="typed"
    )

    assert company.name_in("en") == "National Centre"
    assert company.name_in("fr") == FULL


def test_the_search_finds_it_by_either_name_and_shows_the_chosen_one(user):
    company = Company.objects.create(owner=user, name=FULL)
    acronym(user, company)
    company.list_name = "CNRS"
    company.save()

    for query in ("CNRS", "Centre National"):
        hits = search.search_companies(user, query, 5)
        assert [hit.title for hit in hits.hits] == ["CNRS"], query


def test_the_duplicate_notice_offers_a_company_by_its_acronym(user):
    full = Company.objects.create(owner=user, name=FULL)
    short = Company.objects.create(owner=user, name="CNRS")
    acronym(user, full)

    assert [c.record for c in duplicates.for_company(short)] == [full]
    assert [c.record for c in duplicates.for_company(full)] == [short]


def test_a_merge_keeps_the_acronyms_the_kept_company_has_not(user):
    kept = Company.objects.create(owner=user, name=FULL)
    other = Company.objects.create(owner=user, name="Le CNRS")
    acronym(user, kept)
    acronym(user, other, "cnrs")
    acronym(user, other, "CNRS-France")

    merging.merge_companies(kept, other)

    assert sorted(row.name for row in kept.acronyms()) == ["CNRS", "CNRS-France"]


def test_the_api_shows_both_names_with_the_chosen_one_marked(client, user):
    from postulo.api.models import ApiToken

    company = Company.objects.create(owner=user, name=FULL, list_name="CNRS")
    acronym(user, company)
    _token, secret = ApiToken.issue(user, "test", scopes=["read"])

    body = client.get(
        f"/api/v1/companies/{company.pk}", HTTP_AUTHORIZATION=f"Bearer {secret}"
    ).json()

    assert body["name"] == FULL and body["display_name"] == "CNRS"
    assert body["names"] == [
        {"kind": "acronym", "language": "", "name": "CNRS", "source": "typed", "shown": True}
    ]


def test_the_archive_carries_the_acronyms_and_the_choice(user, other_user):
    company = Company.objects.create(owner=user, name=FULL, list_name="CNRS")
    acronym(user, company)
    acronym(user, company, "C.N.R.S.")

    data = export.write_archive(user)
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        importer.load(other_user, archive)

    restored = Company.objects.get(owner=other_user, name=FULL)
    assert sorted(row.name for row in restored.acronyms()) == ["C.N.R.S.", "CNRS"]
    assert restored.display_name == "CNRS"
    assert export.FORMAT_VERSION >= 85


def test_an_archive_naming_an_acronym_that_is_not_there_shows_the_full_name(user, other_user):
    Company.objects.create(owner=user, name=FULL, list_name="CNRS")
    data = export.write_archive(user)  # the choice written, the acronym itself not

    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        importer.load(other_user, archive)

    assert Company.objects.get(owner=other_user, name=FULL).display_name == FULL


@pytest.mark.parametrize("shown", ["", "CNRS"])
def test_the_edit_page_offers_the_choice(client, user, shown):
    company = Company.objects.create(owner=user, name=FULL, list_name=shown)
    acronym(user, company)
    client.force_login(user)

    page = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert "data-company-acronyms" in page and 'name="shown"' in page
    assert 'value="CNRS" selected' in page if shown else 'value="" selected' in page
