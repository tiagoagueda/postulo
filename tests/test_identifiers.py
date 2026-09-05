"""Company identifiers: a Wikidata id first, and the others people have to hand."""

import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.applications.services import get_or_create_company
from postulo.core import csv_import, importer
from postulo.core import export as export_module
from postulo.core.search import search
from postulo.jobs import identifiers
from postulo.jobs.models import Company, CompanyIdentifier
from postulo.jobs.services import set_identifiers

pytestmark = pytest.mark.django_db

LEI = "HWUPKR0MPOU8FGXBT394"


def company_with(user, name, *ids):
    company = Company.objects.create(owner=user, name=name)
    for scheme, value, *label in ids:
        CompanyIdentifier.objects.create(
            owner=user, company=company, scheme=scheme, value=value, label=label[0] if label else ""
        )
    return company


# ---------------------------------------------------------------- the schemes


@pytest.mark.parametrize(
    ("scheme", "raw", "value"),
    [
        ("wikidata", "Q95", "Q95"),
        ("wikidata", " q95 ", "Q95"),
        ("wikidata", "https://www.wikidata.org/wiki/Q95", "Q95"),
        ("wikidata", "http://www.wikidata.org/entity/Q95", "Q95"),
        ("lei", "hwupkr0mpou8fgxbt394", LEI),
        ("lei", "https://search.gleif.org/#/record/HWUPKR0MPOU8FGXBT394", LEI),
        ("register", "PT501234567", "PT 501234567"),
        ("register", "fr  552 081 317", "FR 552 081 317"),
        ("register", "DE HRB 12345", "DE HRB 12345"),
        ("linkedin", "https://www.linkedin.com/company/Aperture-Science/", "aperture-science"),
        ("linkedin", "aperture-science", "aperture-science"),
        ("crunchbase", "https://www.crunchbase.com/organization/initech", "initech"),
        ("opencorporates", "https://opencorporates.com/companies/gb/01234567", "gb/01234567"),
        ("opencorporates", "GB/01234567", "gb/01234567"),
        ("other", "  DUNS 123456789 ", "DUNS 123456789"),
    ],
)
def test_a_pasted_value_is_tidied_into_the_canonical_id(scheme, raw, value):
    assert identifiers.clean(scheme, raw) == value


@pytest.mark.parametrize(
    ("scheme", "raw"),
    [
        ("wikidata", "95"),
        ("wikidata", "Q0"),
        ("wikidata", "P31"),
        ("wikidata", "https://www.wikidata.org/wiki/Property:P31"),
        ("lei", "HWUPKR0MPOU8FGXBT395"),  # check digits do not match
        ("lei", "TOOSHORT"),
        ("register", "501234567"),  # no country
        ("linkedin", "https://example.org/company/x"),
        ("opencorporates", "01234567"),
        ("nonsense", "x"),
        ("other", ""),
    ],
)
def test_a_malformed_value_is_refused_with_an_example(scheme, raw):
    with pytest.raises(ValidationError):
        identifiers.clean(scheme, raw)


def test_each_linked_scheme_knows_where_it_points():
    assert identifiers.url_for("wikidata", "Q95") == "https://www.wikidata.org/wiki/Q95"
    assert identifiers.url_for("lei", LEI) == f"https://search.gleif.org/#/record/{LEI}"
    assert identifiers.url_for("linkedin", "acme") == "https://www.linkedin.com/company/acme/"
    assert (
        identifiers.url_for("opencorporates", "gb/1") == "https://opencorporates.com/companies/gb/1"
    )
    assert identifiers.url_for("register", "PT 1") == "", "no universal home for a register number"
    assert identifiers.url_for("other", "x") == ""


# ---------------------------------------------------------------- the model


def test_a_company_has_one_value_per_scheme_and_an_id_names_one_company(user):
    acme = company_with(user, "Acme", ("wikidata", "Q95"))
    with pytest.raises(ValidationError):
        CompanyIdentifier(owner=user, company=acme, scheme="wikidata", value="Q96").full_clean()
    other = Company.objects.create(owner=user, name="Other")
    with pytest.raises(ValidationError):
        CompanyIdentifier(owner=user, company=other, scheme="wikidata", value="Q95").full_clean()
    # "Other" identifiers are a labelled free slot: several per company are fine.
    CompanyIdentifier.objects.create(
        owner=user, company=acme, scheme="other", value="1", label="DUNS"
    )
    CompanyIdentifier.objects.create(
        owner=user, company=acme, scheme="other", value="2", label="SIRET"
    )
    assert acme.identifiers.count() == 3


def test_the_same_id_in_two_accounts_is_not_a_clash(user, other_user):
    company_with(user, "Acme", ("wikidata", "Q95"))
    company_with(other_user, "ACME Inc", ("wikidata", "Q95"))
    assert Company.by_identifier(user, "wikidata", "Q95").name == "Acme"
    assert Company.by_identifier(other_user, "wikidata", "q95").name == "ACME Inc"
    assert Company.by_identifier(user, "wikidata", "not an id") is None


def test_the_model_cleans_what_it_is_given(user):
    acme = Company.objects.create(owner=user, name="Acme")
    identifier = CompanyIdentifier(
        owner=user, company=acme, scheme="linkedin", value="https://www.linkedin.com/company/Acme/"
    )
    identifier.full_clean()
    identifier.save()
    assert identifier.value == "acme" and identifier.url.endswith("/company/acme/")
    assert str(identifier) == "LinkedIn: acme"
    with pytest.raises(ValidationError, match="Say what"):
        CompanyIdentifier(owner=user, company=acme, scheme="other", value="x").full_clean()


# --------------------------------------------------------------- matching


def test_a_wikidata_id_matches_a_company_whatever_it_is_called(user):
    acme = company_with(user, "Acme Corporation", ("wikidata", "Q95"))
    assert get_or_create_company(user, "ACME Corp.", wikidata="Q95") == acme
    assert Company.objects.for_user(user).count() == 1


def test_a_company_made_or_found_by_name_gains_the_id_it_came_with(user):
    fresh = get_or_create_company(user, "Initech", wikidata="https://www.wikidata.org/wiki/Q42")
    assert fresh.identifiers.get(scheme="wikidata").value == "Q42"
    again = get_or_create_company(user, "initech")
    assert again == fresh
    # A malformed id is dropped quietly; the company is still the point.
    plain = get_or_create_company(user, "Globex", wikidata="nope")
    assert not plain.identifiers.exists()
    # An id already on another company does not move.
    assert get_or_create_company(user, "Umbrella", wikidata="Q42") == fresh


def test_set_identifiers_validates_the_whole_set(user):
    acme = Company.objects.create(owner=user, name="Acme")
    company_with(user, "Rival", ("lei", LEI))
    with pytest.raises(ValidationError) as excinfo:
        set_identifiers(
            acme,
            [
                ("wikidata", "Q95", ""),
                ("wikidata", "Q96", ""),
                ("lei", LEI, ""),
                ("other", "x", ""),
                ("linkedin", "https://example.org/nothing", ""),
            ],
        )
    messages = " ".join(excinfo.value.messages)
    assert "one identifier per scheme" in messages
    assert "Rival already carries" in messages
    assert "needs a name" in messages
    assert "LinkedIn" in messages
    assert not acme.identifiers.exists(), "nothing is saved when anything is wrong"

    set_identifiers(acme, [("wikidata", "Q95", ""), ("other", "1", "DUNS")])
    set_identifiers(acme, [("linkedin", "acme", "")])
    assert sorted(acme.identifiers.values_list("scheme", flat=True)) == [
        "linkedin",
        "other",
        "wikidata",
    ]
    set_identifiers(acme, [("crunchbase", "acme", "")], replace=True)
    assert list(acme.identifiers.values_list("scheme", flat=True)) == ["crunchbase"]


# ---------------------------------------------------------------- the pages


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def identifier_rows(rows: list[tuple[str, str, str]], *, total: int | None = None) -> dict:
    data = {
        "identifiers-TOTAL_FORMS": str(total if total is not None else len(rows)),
        "identifiers-INITIAL_FORMS": "0",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
    }
    for index, (scheme, value, label) in enumerate(rows):
        data[f"identifiers-{index}-scheme"] = scheme
        data[f"identifiers-{index}-value"] = value
        data[f"identifiers-{index}-label"] = label
    return data


def test_the_company_form_saves_identifiers_with_the_company(signed_in, user):
    response = signed_in.post(
        reverse("jobs:company_create"),
        {
            "name": "Aperture Science",
            **identifier_rows(
                [
                    ("wikidata", "https://www.wikidata.org/wiki/Q4779874", ""),
                    ("other", "12345", "DUNS"),
                    ("", "", ""),  # the untouched extra row
                ]
            ),
        },
    )
    assert response.status_code == 302
    company = Company.objects.get(owner=user, name="Aperture Science")
    assert {(i.scheme, i.value, i.label) for i in company.identifiers.all()} == {
        ("wikidata", "Q4779874", ""),
        ("other", "12345", "DUNS"),
    }

    page = signed_in.get(company.get_absolute_url())
    assert "data-identifiers" in page.content.decode()
    assert "https://www.wikidata.org/wiki/Q4779874" in page.content.decode()
    assert "DUNS" in page.content.decode()


def test_the_company_form_refuses_a_bad_or_borrowed_id(signed_in, user):
    company_with(user, "Rival", ("wikidata", "Q95"))
    response = signed_in.post(
        reverse("jobs:company_create"),
        {"name": "Acme", **identifier_rows([("wikidata", "Q95", ""), ("lei", "bad", "")])},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Rival already carries this identifier" in body
    assert "does not look like a" in body
    assert not Company.objects.filter(owner=user, name="Acme").exists(), "nothing half-saved"


def test_editing_can_remove_an_identifier(signed_in, user):
    acme = company_with(user, "Acme", ("wikidata", "Q95"), ("linkedin", "acme"))
    wikidata = acme.identifiers.get(scheme="wikidata")
    linkedin = acme.identifiers.get(scheme="linkedin")
    data = {
        "name": "Acme",
        "identifiers-TOTAL_FORMS": "3",
        "identifiers-INITIAL_FORMS": "2",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-id": str(wikidata.pk),
        "identifiers-0-scheme": "wikidata",
        "identifiers-0-value": "Q95",
        "identifiers-0-label": "",
        "identifiers-0-DELETE": "on",
        "identifiers-1-id": str(linkedin.pk),
        "identifiers-1-scheme": "linkedin",
        "identifiers-1-value": "acme",
        "identifiers-1-label": "",
        "identifiers-2-scheme": "",
        "identifiers-2-value": "",
        "identifiers-2-label": "",
    }
    response = signed_in.post(reverse("jobs:company_update", args=[acme.pk]), data)
    assert response.status_code == 302
    assert list(acme.identifiers.values_list("scheme", flat=True)) == ["linkedin"]


def test_the_companies_table_offers_a_column_per_scheme(signed_in, user):
    company_with(user, "Acme", ("wikidata", "Q95"), ("register", "PT 501234567"))
    signed_in.post(
        reverse("core:table_settings", args=["companies"]),
        {
            "show": ["name", "id_wikidata", "id_register"],
            "order": ["name", "id_wikidata", "id_register"],
        },
    )
    body = signed_in.get(reverse("jobs:company_list")).content.decode()
    assert "https://www.wikidata.org/wiki/Q95" in body
    assert "PT 501234567" in body
    body = signed_in.get(reverse("jobs:company_list") + "?q=Q95").content.decode()
    assert "Acme" in body


def test_search_finds_a_company_by_its_id(user):
    company_with(user, "Acme", ("lei", LEI))
    groups = search(user, LEI[:8])
    assert [hit.title for group in groups for hit in group.hits] == ["Acme"]


def test_another_persons_page_never_shows_my_ids(client, user, other_user):
    acme = company_with(user, "Acme", ("wikidata", "Q95"))
    client.force_login(other_user)
    assert client.get(acme.get_absolute_url()).status_code == 404
    assert client.get(reverse("jobs:company_update", args=[acme.pk])).status_code == 404


# -------------------------------------------------------------------- the API


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "test", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_carries_identifiers_both_ways(client, user):
    auth = bearer(user, "read", "write")
    response = client.post(
        "/api/v1/companies",
        data={
            "name": "Aperture Science",
            "identifiers": [
                {"scheme": "wikidata", "value": "https://www.wikidata.org/wiki/Q4779874"},
                {"scheme": "other", "value": "12345", "label": "DUNS"},
            ],
        },
        content_type="application/json",
        **auth,
    )
    assert response.status_code == 201, response.content
    ids = response.json()["identifiers"]
    assert {(i["scheme"], i["value"], i["label"]) for i in ids} == {
        ("wikidata", "Q4779874", ""),
        ("other", "12345", "DUNS"),
    }
    assert next(i["url"] for i in ids if i["scheme"] == "wikidata").endswith("/wiki/Q4779874")
    pk = response.json()["id"]

    # The same Wikidata id, a different spelling: the same company.
    again = client.post(
        "/api/v1/companies",
        data={"name": "APERTURE", "identifiers": [{"scheme": "wikidata", "value": "q4779874"}]},
        content_type="application/json",
        **auth,
    )
    assert again.status_code == 201 and again.json()["id"] == pk
    assert Company.objects.for_user(user).count() == 1

    response = client.patch(
        f"/api/v1/companies/{pk}",
        data={"identifiers": [{"scheme": "linkedin", "value": "aperture-science"}]},
        content_type="application/json",
        **auth,
    )
    assert [i["scheme"] for i in response.json()["identifiers"]] == ["linkedin"], "PATCH replaces"

    bad = client.patch(
        f"/api/v1/companies/{pk}",
        data={"identifiers": [{"scheme": "lei", "value": "nope"}]},
        content_type="application/json",
        **auth,
    )
    assert bad.status_code == 422 and "LEI" in bad.json()["detail"]

    listed = client.get("/api/v1/companies?q=aperture-science", **auth).json()
    assert [c["id"] for c in listed["items"]] == [pk]


def test_a_listing_can_name_the_employer_by_wikidata_id(client, user):
    acme = company_with(user, "Acme Corporation", ("wikidata", "Q95"))
    response = client.post(
        "/api/v1/listings",
        data={"company_name": "ACME Corp", "company_wikidata": "Q95", "title": "Engineer"},
        content_type="application/json",
        **bearer(user, "write", "read"),
    )
    assert response.status_code == 201, response.content
    assert response.json()["company"]["id"] == acme.pk


# ---------------------------------------------------------- export and import


def test_identifiers_survive_the_round_trip_and_match_on_import(user, other_user):
    company_with(user, "Acme", ("wikidata", "Q95"), ("other", "1", "DUNS"))
    document = export_module.build_document(user)
    assert document["companies"][0]["identifiers"] == [
        {"scheme": "other", "value": "1", "label": "DUNS"},
        {"scheme": "wikidata", "value": "Q95", "label": ""},
    ]

    # The other account already knows the employer under another name and the same id.
    theirs = company_with(other_user, "ACME Corporation", ("wikidata", "Q95"))
    archive = zipfile.ZipFile(export_module.write_archive(user))
    importer.load(other_user, archive, force=True)
    assert Company.objects.for_user(other_user).count() == 1, "matched by id, not by name"
    theirs.refresh_from_db()
    assert {(i.scheme, i.value) for i in theirs.identifiers.all()} == {
        ("wikidata", "Q95"),
        ("other", "1"),
    }


def test_the_csv_importer_matches_on_a_wikidata_column(user):
    acme = company_with(user, "Acme Corporation", ("wikidata", "Q95"))
    text = (
        "Company,Wikidata,Role,Date applied\n"
        "ACME Corp.,Q95,Engineer,2026-05-01\n"
        "Initech,Q42,Dev,2026-05-02\n"
    )
    sheet = csv_import.read_sheet(text.encode(), "jobs.csv")
    mapping = csv_import.guess_mapping(sheet.headers)
    assert mapping == ["company", "wikidata", "role", "applied_at"]
    report = csv_import.perform(user, sheet, mapping)
    assert report.companies_created == 1
    assert acme.postings.filter(title="Engineer").exists()
    initech = Company.objects.get(owner=user, name="Initech")
    assert initech.identifiers.get(scheme="wikidata").value == "Q42"
