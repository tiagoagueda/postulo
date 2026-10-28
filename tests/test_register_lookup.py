"""Register lookup: asking a company register about a number, on a press (#1021, #935).

Each adapter reads an answer recorded from its register on 27 October 2026 and cut down to
what is read, so what is held here is the register's real shape. The request is the real
guarded client over a transport that never leaves the machine, as the Wikidata tests do it.
"""

from __future__ import annotations

import ipaddress
import json

import httpx
import pytest
from django.core.cache import caches
from django.urls import reverse

from postulo.core import gdpr
from postulo.core.memo import forget_decisions
from postulo.jobs import register_lookup
from postulo.jobs.models import Company, CompanyIdentifier, LegalState
from postulo.plugins import http, registry
from postulo.plugins.models import PluginPolicy
from postulo.plugins.register_lookup import REGISTER_LOOKUP, adapters

PUBLIC = [ipaddress.ip_address("93.184.216.34")]

# ------------------------------------------------------------- the recorded answers

FRANCE = {
    "results": [
        {
            "siren": "552081317",
            "nom_complet": "ELECTRICITE DE FRANCE (EDF)",
            "nom_raison_sociale": "ELECTRICITE DE FRANCE",
            "activite_principale": "35.11Z",
            "activite_principale_naf25": "35.11Y",
            "date_fermeture": None,
            "etat_administratif": "A",
            "statut_diffusion": "O",
            "siege": {
                "adresse": "22-30 22 AVENUE DE WAGRAM 75008 PARIS",
                "code_postal": "75008",
                "code_pays_etranger": None,
                "libelle_commune": "PARIS",
                "libelle_voie": "DE WAGRAM",
                "numero_voie": "22",
                "type_voie": "AVENUE",
                "siret": "55208131766522",
            },
        }
    ],
    "total_results": 1,
    "page": 1,
    "per_page": 5,
    "total_pages": 1,
}

NORWAY = {
    "organisasjonsnummer": "923609016",
    "navn": "EQUINOR ASA",
    "forretningsadresse": {
        "adresse": ["Forusbeen 50"],
        "kommune": "STAVANGER",
        "land": "Norge",
        "landkode": "NO",
        "postnummer": "4035",
        "poststed": "STAVANGER",
    },
    "naeringskode1": {"beskrivelse": "Utvinning av råolje", "kode": "06.100"},
    "konkurs": False,
    "underAvvikling": False,
    "underTvangsavviklingEllerTvangsopplosning": False,
    "respons_klasse": "Enhet",
}

FINLAND = {
    "totalResults": 1,
    "companies": [
        {
            "businessId": {"value": "0112038-9", "registrationDate": "1978-03-15", "source": "3"},
            "names": [
                {"name": "Nokia Oyj", "type": "1", "registrationDate": "1997-09-01"},
                {
                    "name": "Oy Nokia Ab",
                    "type": "1",
                    "registrationDate": "1966-06-10",
                    "endDate": "1997-08-31",
                },
            ],
            "mainBusinessLine": {
                "type": "70100",
                "descriptions": [
                    {"languageCode": "3", "description": "Activities of head offices"},
                    {"languageCode": "1", "description": "Pääkonttorien toiminta"},
                ],
                "typeCodeSet": "TOIMI4",
            },
            "addresses": [
                {"type": 2, "postCode": "00045", "postOfficeBox": "226", "source": "0"},
                {
                    "type": 1,
                    "street": "Karakaari",
                    "postCode": "02610",
                    "buildingNumber": "7",
                    "postOffices": [
                        {"city": "ESBO", "languageCode": "2", "municipalityCode": "049"},
                        {"city": "ESPOO", "languageCode": "1", "municipalityCode": "049"},
                    ],
                },
            ],
            "companySituations": [],
            "tradeRegisterStatus": "1",
            "status": "2",
        }
    ],
}

CZECHIA = {
    "ico": "00177041",
    "obchodniJmeno": "Škoda Auto a.s.",
    "sidlo": {
        "kodStatu": "CZ",
        "nazevObce": "Mladá Boleslav",
        "nazevUlice": "tř. Václava Klementa",
        "cisloDomovni": 869,
        "nazevCastiObce": "Mladá Boleslav II",
        "psc": 29301,
        "textovaAdresa": "tř. Václava Klementa 869, Mladá Boleslav II, 29301 Mladá Boleslav",
    },
    "pravniForma": "121",
    "datumVzniku": "1990-11-20",
    "czNace2008": ["29100", "45200"],
}

POLAND = {
    "odpis": {
        "rodzaj": "Aktualny",
        "naglowekA": {"rejestr": "RejP", "numerKRS": "0000019193"},
        "dane": {
            "dzial1": {
                "danePodmiotu": {
                    "formaPrawna": "SPÓŁKA AKCYJNA",
                    "nazwa": "POLSKIE KOLEJE PAŃSTWOWE SPÓŁKA AKCYJNA",
                },
                "siedzibaIAdres": {
                    "adres": {
                        "ulica": "ALEJE JEROZOLIMSKIE",
                        "nrDomu": "142A",
                        "miejscowosc": "WARSZAWA",
                        "kodPocztowy": "02-305",
                        "poczta": "WARSZAWA",
                        "kraj": "POLSKA",
                    }
                },
            },
            "dzial3": {
                "przedmiotDzialalnosci": {
                    "przedmiotPrzewazajacejDzialalnosci": [
                        {
                            "opis": "DZIAŁALNOŚĆ FIRM CENTRALNYCH (HEAD OFFICES) I HOLDINGÓW",
                            "kodDzial": "70",
                            "kodKlasa": "10",
                            "kodPodklasa": "Z",
                        }
                    ]
                }
            },
        },
    }
}

LEI = "529900W18LQJJN6SJ336"
GLEIF = {
    "data": {
        "type": "lei-records",
        "id": LEI,
        "attributes": {
            "lei": LEI,
            "entity": {
                "legalName": {"name": "Société Générale Effekten GmbH", "language": "fr"},
                "legalAddress": {
                    "language": "de",
                    "addressLines": ["Neue Mainzer Straße 46-50"],
                    "city": "Frankfurt am Main",
                    "region": "DE-HE",
                    "country": "DE",
                    "postalCode": "60311",
                },
                "registeredAt": {"id": "RA000242", "other": None},
                "registeredAs": "HRB 32283",
                "jurisdiction": "DE",
                "status": "ACTIVE",
            },
        },
    }
}

#: Where each adapter asks, and what it was answered.
RECORDED = {
    "fr": (
        "FR 552081317",
        "/search?q=552081317&minimal=true&include=siege&page=1&per_page=5",
        FRANCE,
    ),
    "no": ("NO 923609016", "/enhetsregisteret/api/enheter/923609016", NORWAY),
    "fi": ("FI 0112038-9", "/opendata-ytj-api/v3/companies?businessId=0112038-9", FINLAND),
    "cz": ("CZ 00177041", "/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00177041", CZECHIA),
    "pl": ("PL 0000019193", "/api/krs/OdpisAktualny/0000019193?rejestr=P&format=json", POLAND),
}


@pytest.fixture
def registers(monkeypatch):
    """The real guard, every register resolving to a public address, over a mock transport.

    Answers are keyed on the path and query asked for; anything else is a 404.
    """
    seen: list[httpx.Request] = []
    answers: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.get(request.url.raw_path.decode(), httpx.Response(404))

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    return answers, seen


def answer(answers, path, body, status=200):
    answers[path] = httpx.Response(status, content=json.dumps(body).encode())


@pytest.fixture
def switched_on(db):
    """Register lookup on for the instance: the row that starts it off taken away, as the
    administrator's switch does."""
    PluginPolicy.objects.filter(plugin=REGISTER_LOOKUP, person=None).delete()
    forget_decisions()
    caches["ratelimit"].clear()
    yield
    caches["ratelimit"].clear()


def acme(user, scheme="register", value="FR 552081317", **fields) -> Company:
    company = Company.objects.create(owner=user, name=fields.pop("name", "EDF"), **fields)
    CompanyIdentifier.objects.create(owner=user, company=company, scheme=scheme, value=value)
    return company


# ---------------------------------------------------------------- each adapter


@pytest.mark.parametrize("country", sorted(RECORDED))
def test_every_adapter_reads_its_registers_recorded_answer(registers, country):
    answers, seen = registers
    value, path, body = RECORDED[country]
    answer(answers, path, body)
    adapter = register_lookup.adapter_for("register", value)

    record = register_lookup.lookup(adapter, value)

    assert record.name
    assert record.address is not None and record.address.municipality
    (request,) = seen
    assert request.headers["Host"] in adapter.hosts


def test_france_reads_name_head_office_state_and_nace_2_1_division(registers):
    answers, _seen = registers
    answer(answers, *RECORDED["fr"][1:])

    record = register_lookup.lookup(adapters.FRANCE, "FR 552081317")

    assert record.name == "ELECTRICITE DE FRANCE"
    assert record.address.street == "22 AVENUE DE WAGRAM"
    assert (record.address.postcode, record.address.municipality) == ("75008", "PARIS")
    assert record.state == register_lookup.ACTIVE
    assert record.activity == "35.11Y", "NAF 2025 first: its division is NACE 2.1's"
    assert record.division == "35"


def test_france_withholds_the_address_of_a_company_that_asked_for_it(registers):
    answers, _seen = registers
    hidden = json.loads(json.dumps(FRANCE))
    hidden["results"][0]["statut_diffusion"] = "P"
    answer(answers, RECORDED["fr"][1], hidden)

    record = register_lookup.lookup(adapters.FRANCE, "FR 552081317")

    assert record.name and record.address is None


def test_france_says_a_ceased_company_is_dissolved_and_since_when(registers):
    answers, _seen = registers
    ceased = json.loads(json.dumps(FRANCE))
    ceased["results"][0].update(etat_administratif="C", date_fermeture="2024-03-31")
    answer(answers, RECORDED["fr"][1], ceased)

    record = register_lookup.lookup(adapters.FRANCE, "FR 552081317")

    assert record.state == register_lookup.DISSOLVED
    assert record.state_since.isoformat() == "2024-03-31"


def test_france_a_search_that_finds_another_company_is_no_entry(registers):
    answers, _seen = registers
    other = json.loads(json.dumps(FRANCE))
    other["results"][0]["siren"] = "356000000"
    answer(answers, RECORDED["fr"][1], other)

    with pytest.raises(register_lookup.LookupRefused, match="no entry"):
        register_lookup.lookup(adapters.FRANCE, "FR 552081317")


def test_norway_reads_its_activity_and_a_bankruptcy(registers):
    answers, _seen = registers
    bankrupt = dict(NORWAY, konkurs=True, konkursdato="2025-02-01")
    answer(answers, RECORDED["no"][1], bankrupt)

    record = register_lookup.lookup(adapters.NORWAY, "NO 923609016")

    assert record.name == "EQUINOR ASA"
    assert record.address.street == "Forusbeen 50" and record.address.country == "NO"
    assert (record.activity, record.division) == ("06.100", "06")
    assert record.activity_words == "Utvinning av råolje"
    assert record.state == register_lookup.ADMINISTRATION
    assert record.state_since.isoformat() == "2025-02-01"


def test_norway_an_entry_removed_for_legal_reasons_is_said_and_not_kept(registers):
    answers, _seen = registers
    answers[RECORDED["no"][1]] = httpx.Response(410)

    with pytest.raises(register_lookup.LookupRefused, match="removed"):
        register_lookup.lookup(adapters.NORWAY, "NO 923609016")


def test_finland_reads_the_current_name_the_street_address_and_english_activity(registers):
    answers, _seen = registers
    answer(answers, *RECORDED["fi"][1:])

    record = register_lookup.lookup(adapters.FINLAND, "FI 0112038-9")

    assert record.name == "Nokia Oyj", "a name with an end date is an old one"
    assert record.address.street == "Karakaari 7"
    assert (record.address.postcode, record.address.municipality) == ("02610", "ESPOO")
    assert record.state == register_lookup.ACTIVE
    assert (record.activity, record.activity_words) == ("70100", "Activities of head offices")
    assert record.division == "70"


def test_finland_liquidation_is_an_administration(registers):
    answers, _seen = registers
    liquidating = json.loads(json.dumps(FINLAND))
    liquidating["companies"][0]["companySituations"] = [
        {"type": "SELTILA", "registrationDate": "2026-01-05"}
    ]
    answer(answers, RECORDED["fi"][1], liquidating)

    record = register_lookup.lookup(adapters.FINLAND, "FI 0112038-9")

    assert (record.state, record.state_words) == (register_lookup.ADMINISTRATION, "Liquidation")


def test_czechia_reads_the_seat_and_offers_no_activity_it_cannot_rank(registers):
    answers, _seen = registers
    answer(answers, *RECORDED["cz"][1:])

    record = register_lookup.lookup(adapters.CZECHIA, "CZ 00177041")

    assert record.name == "Škoda Auto a.s."
    assert record.address.street == "tř. Václava Klementa 869"
    assert record.address.postcode == "293 01"
    assert record.division == "", "several CZ-NACE codes and none marked as the main one"
    assert record.state == "", "being in ARES does not say it trades"


def test_poland_asks_the_associations_register_when_the_business_one_has_no_entry(registers):
    answers, seen = registers
    association = json.loads(json.dumps(POLAND))
    answer(answers, "/api/krs/OdpisAktualny/0000019193?rejestr=S&format=json", association)

    record = register_lookup.lookup(adapters.POLAND, "PL 0000019193")

    assert [request.url.params["rejestr"] for request in seen] == ["P", "S"]
    assert record.name == "POLSKIE KOLEJE PAŃSTWOWE SPÓŁKA AKCYJNA"
    assert (record.activity, record.division) == ("70.10.Z", "70")


def test_gleif_reads_the_local_register_number(registers):
    answers, seen = registers
    answer(answers, f"/api/v1/lei-records/{LEI}", GLEIF)

    record = register_lookup.lookup(adapters.GLEIF, LEI)

    assert record.name == "Société Générale Effekten GmbH"
    assert (record.registered_as, record.registered_at, record.jurisdiction) == (
        "HRB 32283",
        "RA000242",
        "DE",
    )
    assert seen[0].headers["Accept"] == "application/vnd.api+json"


# ----------------------------------------------------------------- the request


def test_only_the_number_is_sent_with_postulos_user_agent(registers):
    answers, seen = registers
    answer(answers, *RECORDED["no"][1:])

    register_lookup.lookup(adapters.NORWAY, "NO 923609016")

    (request,) = seen
    assert request.url.raw_path.decode() == "/enhetsregisteret/api/enheter/923609016"
    assert request.headers["User-Agent"].startswith("Postulo/")
    assert "Cookie" not in request.headers and "Referer" not in request.headers


def test_a_redirect_is_not_followed(registers):
    answers, seen = registers
    answers[RECORDED["no"][1]] = httpx.Response(302, headers={"Location": "https://evil.example/"})

    with pytest.raises(register_lookup.LookupRefused, match="somewhere else"):
        register_lookup.lookup(adapters.NORWAY, "NO 923609016")
    assert len(seen) == 1


def test_an_answer_too_large_is_refused(registers):
    answers, _seen = registers
    answers[RECORDED["no"][1]] = httpx.Response(
        200, content=b"{" + b" " * (register_lookup.MAX_BYTES + 10) + b"}"
    )

    with pytest.raises(register_lookup.LookupRefused, match="larger"):
        register_lookup.lookup(adapters.NORWAY, "NO 923609016")


def test_a_number_the_register_does_not_have_is_said(registers):
    with pytest.raises(register_lookup.LookupRefused, match="no entry"):
        register_lookup.lookup(adapters.NORWAY, "NO 923609016")


def test_a_registers_text_is_cleaned(registers):
    answers, _seen = registers
    answer(answers, RECORDED["no"][1], dict(NORWAY, navn="EQUINOR‮ ASA\u0007"))

    assert register_lookup.lookup(adapters.NORWAY, "NO 923609016").name == "EQUINOR ASA"


def test_an_adapter_cannot_send_the_request_outside_its_hosts(registers):
    _answers, seen = registers
    rogue = register_lookup.Adapter(
        key="xx-rogue",
        label="Rogue",
        scheme="register",
        country="XX",
        hosts=("register.example",),
        addresses=lambda number: ["https://elsewhere.example/" + number],
        read=lambda document, number: None,
        source="Rogue",
    )

    with pytest.raises(register_lookup.LookupRefused, match="not one it declared"):
        register_lookup.lookup(rogue, "XX 123")
    assert seen == []


# --------------------------------------------------------- the open registry


def test_another_package_adds_a_country_and_cannot_take_one(monkeypatch):
    def read(document, number):
        return None

    class Elsewhere:
        name = "elsewhere"
        kind = "register-adapter"
        adapters = (
            register_lookup.Adapter(
                key="de-elsewhere",
                label="Somewhere",
                scheme="register",
                country="DE",
                hosts=("register.example",),
                addresses=lambda number: [f"https://register.example/{number}"],
                read=read,
                source="Somewhere",
            ),
            register_lookup.Adapter(
                key="fr-elsewhere",
                label="Not France's",
                scheme="register",
                country="FR",
                hosts=("register.example",),
                addresses=lambda number: [f"https://register.example/{number}"],
                read=read,
                source="Somewhere",
            ),
            "not an adapter",
        )

    shipped = registry.plugins("register-adapter")
    monkeypatch.setattr(
        registry,
        "plugins",
        lambda kind, **kwargs: [Elsewhere(), *shipped] if kind == "register-adapter" else [],
    )

    found = register_lookup.adapters()

    assert register_lookup.adapter_for("register", "DE HRB 1").key == "de-elsewhere"
    assert register_lookup.adapter_for("register", "FR 552081317").key == (
        "fr-recherche-entreprises"
    ), "a country Postulo answers for stays Postulo's"
    assert "fr-elsewhere" not in found


def test_the_group_is_open_and_the_registry_is_nobodys_to_switch():
    from postulo.plugins.policy import UNGOVERNED_KINDS

    assert registry.GROUPS["register-adapter"] == "postulo.register_adapters"
    assert "register-adapter" in UNGOVERNED_KINDS


def test_every_shipped_adapter_asks_over_https_on_its_own_hosts():
    """What `lookup` refuses at run time, held for the six Postulo ships."""
    numbers = {
        "FR": "552081317",
        "NO": "923609016",
        "FI": "0112038-9",
        "CZ": "00177041",
        "PL": "0000019193",
        "": LEI,
    }
    for adapter in adapters.ADAPTERS:
        for address in adapter.addresses(numbers[adapter.country]):
            parsed = httpx.URL(address)
            assert parsed.scheme == "https", address
            assert parsed.host in adapter.hosts, address
        assert adapter.addresses("not a number") == [], adapter.key


# ------------------------------------------------------- the switch and the page


def test_register_lookup_is_off_by_default(client, user):
    from postulo.plugins.policy import decide

    company = acme(user)
    client.force_login(user)

    assert not decide(REGISTER_LOOKUP, user).on
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "data-register-lookups" not in html
    assert client.post(reverse("jobs:company_register", args=[company.pk])).status_code == 404


def test_switched_on_the_page_offers_look_up_beside_a_number_a_register_answers_for(
    client, user, switched_on
):
    company = acme(user)
    elsewhere = acme(user, value="DE HRB 12345", name="Somewhere GmbH")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    other = client.get(reverse("jobs:company_detail", args=[elsewhere.pk])).content.decode()

    assert 'data-register-lookup="register"' in html
    assert "Annuaire des Entreprises" in html
    assert "data-register-lookups" not in other, "no adapter answers for Germany"


def test_the_policy_hides_the_action_for_one_account(client, user, switched_on):
    company = acme(user)
    PluginPolicy.objects.create(
        plugin=REGISTER_LOOKUP, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "data-register-lookups" not in html
    assert client.post(reverse("jobs:company_register", args=[company.pk])).status_code == 404


def test_the_allowance_bounds_the_presses(client, user, switched_on, registers, settings):
    settings.POSTULO_REGISTER_LOOKUP_RATE = "1/h"
    answers, seen = registers
    answer(answers, *RECORDED["fr"][1:])
    company = acme(user)
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])

    client.post(url, {"scheme": "register"})
    response = client.post(url, {"scheme": "register"}, follow=True)

    assert len(seen) == 1, "the second press asked nothing"
    assert "too many requests" in response.content.decode().lower()


def test_looking_up_shows_what_came_back_and_keeps_nothing(client, user, switched_on, registers):
    answers, _seen = registers
    answer(answers, *RECORDED["fr"][1:])
    company = acme(user)
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])

    assert client.post(url, {"scheme": "register"}).status_code == 302
    html = client.get(url).content.decode()

    assert "ELECTRICITE DE FRANCE" in html
    assert "Licence Ouverte 2.0" in html, "attributed as the licence asks"
    assert 'name="take" value="name"' in html and "checked" not in html
    company.refresh_from_db()
    assert company.name == "EDF" and not company.postal_addresses.exists()


def test_the_review_keeps_only_what_is_ticked(client, user, switched_on, registers):
    answers, _seen = registers
    answer(answers, *RECORDED["fr"][1:])
    company = acme(user, legal_state=LegalState.DISSOLVED)
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])
    client.post(url, {"scheme": "register"})

    # A hand-written "register" is not one the page offered for a register number.
    client.post(url, {"keep": "1", "take": ["address", "industry", "register"]})

    company.refresh_from_db()
    assert company.name == "EDF", "the name was not ticked"
    assert company.legal_state == LegalState.DISSOLVED, "the state was not ticked"
    (address,) = company.postal_addresses.all()
    assert (address.street, address.postcode, address.municipality) == (
        "22 AVENUE DE WAGRAM",
        "75008",
        "PARIS",
    )
    assert [industry.code for industry in company.industries.all()] == ["35"]
    assert company.identifiers.count() == 1


def test_keeping_the_name_and_state_writes_the_register_as_the_source(
    client, user, switched_on, registers
):
    answers, _seen = registers
    ceased = json.loads(json.dumps(FRANCE))
    ceased["results"][0].update(etat_administratif="C", date_fermeture="2024-03-31")
    answer(answers, RECORDED["fr"][1], ceased)
    company = acme(user)
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])
    client.post(url, {"scheme": "register"})

    client.post(url, {"keep": "1", "take": ["name", "state"]})

    company.refresh_from_db()
    assert company.name == "ELECTRICITE DE FRANCE"
    assert company.legal_state == LegalState.DISSOLVED
    assert company.legal_state_since.isoformat() == "2024-03-31"
    assert company.legal_state_source.endswith("/entreprise/552081317")


def test_an_lei_without_a_register_number_is_offered_one(client, user, switched_on, registers):
    answers, _seen = registers
    answer(answers, f"/api/v1/lei-records/{LEI}", GLEIF)
    company = acme(user, scheme="lei", value=LEI, name="SG Effekten")
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])
    client.post(url, {"scheme": "lei"})

    assert 'value="register"' in client.get(url).content.decode()
    client.post(url, {"keep": "1", "take": ["register"]})

    assert company.identifiers.get(scheme="register").value == "DE HRB 32283"


@pytest.mark.parametrize(
    "stored, result", [("DE HRB 32283", "agrees"), ("DE HRB 99999", "differs")]
)
def test_an_lei_and_a_register_number_are_compared(
    client, user, switched_on, registers, stored, result
):
    answers, _seen = registers
    answer(answers, f"/api/v1/lei-records/{LEI}", GLEIF)
    company = acme(user, scheme="lei", value=LEI, name="SG Effekten")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="register", value=stored)
    client.force_login(user)
    url = reverse("jobs:company_register", args=[company.pk])
    client.post(url, {"scheme": "lei"})

    html = client.get(url).content.decode()

    assert f'data-register-comparison="{result}"' in html
    assert 'value="register"' not in html, "a company with a number is not offered another"


def test_the_comparison_reads_through_spacing_and_greeces_two_codes():
    record = register_lookup.Record(number=LEI, registered_as="HRB32283", jurisdiction="DE")

    assert register_lookup.compare("DE HRB 32283", record) == "agrees"
    assert register_lookup.compare("AT HRB 32283", record) == "differs"
    greek = register_lookup.Record(number=LEI, registered_as="123", jurisdiction="GR")
    assert register_lookup.compare("EL 123", greek) == "agrees"


def test_a_read_grant_cannot_look_up(client, user, other_user, switched_on):
    from postulo.jobs.models import Share

    company = acme(other_user)
    Share.objects.create(company=company, grantee_user=user, scope=Share.Scope.READ)
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "data-register-lookups" not in html
    response = client.post(reverse("jobs:company_register", args=[company.pk]))
    assert response.status_code == 404


# ------------------------------------------------------------------ paperwork


def test_the_record_of_processing_lists_the_registers_only_while_it_is_on(db):
    assert gdpr.record_of_processing()["services"] == []

    PluginPolicy.objects.filter(plugin=REGISTER_LOOKUP, person=None).delete()
    forget_decisions()

    services = {row["key"]: row for row in gdpr.record_of_processing()["services"]}
    assert set(services) == {adapter.key for adapter in adapters.ADAPTERS}
    assert services["gleif"]["hosts"] == ["api.gleif.org"]
    assert services["fi-ytj"]["licence"] == "CC BY 4.0"


def test_the_record_page_names_each_register(admin_client, switched_on):
    html = admin_client.get(reverse("server:record_of_processing")).content.decode()

    assert 'data-outside-service="no-enhetsregisteret"' in html
    assert "data.brreg.no" in html
