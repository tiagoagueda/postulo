"""The public employment service as a kind of company (#202).

France Travail, IEFP, the Bundesagentur für Arbeit: dealt with throughout a search, never
applied to, and until now recordable only as an employer. What is held here: the registry
is well-formed, the form fills a picked service in and lets what the person typed win, the
kind shows and narrows on the companies page, and an employer is still an employer.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core import phones
from postulo.jobs import employment_services
from postulo.jobs.models import Company, CompanyKind

pytestmark = pytest.mark.django_db


def test_every_known_service_has_a_country_a_name_and_a_public_address():
    keys = [service.key for service in employment_services.SERVICES]
    assert len(keys) == len(set(keys)), "one key per service"
    for service in employment_services.SERVICES:
        assert service.country in phones.BY_CODE, service
        assert service.name.strip() and service.website.startswith("https://"), service
        assert service.country_name != service.country, "the country has a name"
    assert employment_services.by_key("fr:france-travail").name == "France Travail"
    assert employment_services.by_key("mars:office") is None
    assert employment_services.by_key("") is None


def test_the_registry_is_offered_by_country_in_alphabetical_order():
    groups = employment_services.grouped()
    countries = [country for country, _rows in groups]
    assert countries == sorted(countries)
    belgium = dict(groups)["Belgium"]
    assert [name for _key, name in belgium] == ["Actiris", "Forem", "VDAB"]


def form_data(**overrides) -> dict:
    data = {
        "name": "",
        "kind": CompanyKind.EMPLOYER,
        "known_service": "",
        "website": "",
        "careers_url": "",
        "location": "",
        "new_industries": "",
        "notes": "",
        "identifiers-TOTAL_FORMS": "0",
        "identifiers-INITIAL_FORMS": "0",
    }
    data.update(overrides)
    return data


def test_picking_a_known_service_fills_the_name_and_the_website(client, user):
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_create"), form_data(known_service="fr:france-travail")
    )
    assert response.status_code == 302
    office = Company.objects.for_user(user).get()
    assert office.name == "France Travail"
    assert office.website == "https://www.francetravail.fr"
    assert office.kind == CompanyKind.EMPLOYMENT_SERVICE
    assert office.is_employment_service


def test_what_the_person_typed_wins_over_the_registry(client, user):
    client.force_login(user)
    client.post(
        reverse("jobs:company_create"),
        form_data(
            known_service="pt:instituto-do-emprego-e-formacao-profissional-iefp",
            name="IEFP Lisboa",
            website="https://www.iefp.pt/lisboa",
        ),
    )
    office = Company.objects.for_user(user).get()
    assert office.name == "IEFP Lisboa" and office.website == "https://www.iefp.pt/lisboa"
    assert office.kind == CompanyKind.EMPLOYMENT_SERVICE, "picking a service means one"


def test_a_blank_name_with_no_service_is_still_refused(client, user):
    client.force_login(user)
    response = client.post(reverse("jobs:company_create"), form_data())
    assert response.status_code == 200
    assert response.context["form"].errors["name"]
    assert not Company.objects.for_user(user).exists()


def test_a_service_named_like_a_company_you_have_is_refused_in_the_same_words(client, user):
    Company.objects.create(owner=user, name="France Travail")
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_create"), form_data(known_service="fr:france-travail")
    )
    assert response.status_code == 200
    assert "You already have a company with that name." in response.context["form"].errors["name"]


def test_an_employer_is_recorded_as_one_without_touching_the_service_list(client, user):
    client.force_login(user)
    client.post(reverse("jobs:company_create"), form_data(name="Aperture Science"))
    company = Company.objects.for_user(user).get()
    assert company.kind == CompanyKind.EMPLOYER and not company.is_employment_service
    assert list(Company.objects.for_user(user).employers()) == [company]


def test_the_kind_is_offered_on_the_form_by_country(client, user):
    client.force_login(user)
    page = client.get(reverse("jobs:company_create")).content.decode()
    assert 'name="kind"' in page and 'name="known_service"' in page
    assert '<optgroup label="France">' in page and "France Travail" in page
    assert 'value="fr:france-travail"' in page


def test_the_companies_page_says_which_is_the_office_and_narrows_to_it(client, user):
    Company.objects.create(owner=user, name="Aperture Science")
    office = Company.objects.create(
        owner=user, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    client.force_login(user)

    page = client.get(reverse("jobs:company_list")).content.decode()
    assert page.count("Employment service") == 1, (
        "a chip beside the office's name, and nothing beside an employer"
    )

    narrowed = client.get(reverse("jobs:company_list"), {"kind": "employment_service"})
    assert list(narrowed.context["companies"]) == [office]
    narrowed = client.get(reverse("jobs:company_list"), {"kind": "employer"})
    assert [c.name for c in narrowed.context["companies"]] == ["Aperture Science"]

    detail = client.get(office.get_absolute_url()).content.decode()
    assert "Employment service" in detail


def test_every_company_from_before_there_were_kinds_is_an_employer(user):
    """The column has a default, so the migration invents nothing."""
    company = Company.objects.create(owner=user, name="Black Mesa")
    assert company.kind == CompanyKind.EMPLOYER
