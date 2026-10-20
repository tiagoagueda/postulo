"""Company register numbers: each country's check digit and register page (#935).

The numbers are ones the registers publish for themselves (Électricité de France's SIREN,
Equinor's organisasjonsnummer, Nokia's Y-tunnus, Eesti Energia's registrikood, ...) or are
built to satisfy the documented rule; the expected addresses were opened against the
register's own site when they were added.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from postulo.core import identifiers
from postulo.jobs import identifiers as company
from postulo.plugins.identifiers import registers
from postulo.plugins.identifiers.schemes import SCHEMES

pytestmark = pytest.mark.django_db

REGISTER = SCHEMES["register"]


def clean(raw: str) -> str:
    return REGISTER.normalise(raw)


def refused(raw: str) -> ValidationError | None:
    try:
        identifiers.refuse_malformed(REGISTER, clean(raw))
    except ValidationError as error:
        return error
    return None


VALID = [
    "FR 552081317",  # SIREN (Luhn)
    "FR 552 081 317",
    "FR 55208131766522",  # SIRET
    "FR 35600000000048",  # La Poste's head office, Luhn like any other
    "FR 35600000000001",  # a La Poste branch: digits add up to a multiple of 5
    "CZ 27082440",  # IČO, remainder 1 gives 0
    "CZ 45274649",  # IČO, 11 - remainder
    "NO 923609016",
    "NO 923 609 016",
    "FI 0112038-9",
    "FI 01120389",
    "EE 10421629",
    "EE 98128698",  # needs the second pass of the weights
    "PL 0000019193",  # KRS: shape only
    "PT 501234567",  # no rule known: the prefix is enough
    "DE HRB 12345",
    "GB 00445790",
]


@pytest.mark.parametrize("raw", VALID)
def test_a_number_that_passes_its_countrys_rule_is_accepted(raw):
    assert refused(raw) is None


INVALID = [
    "FR 552081318",  # SIREN, one digit off
    "FR 5520813",  # wrong length
    "FR 55208131766523",  # SIRET, one digit off
    "FR 55208131866522",  # SIRET whose SIREN is not a SIREN
    "FR 35600000000002",  # La Poste branch whose digits do not add up to a multiple of 5
    "FR 552O81317",  # a letter
    "CZ 27082441",
    "CZ 2708244",
    "NO 923609017",
    "NO 92360901",
    "FI 0112038-8",
    "FI 0112038",
    "EE 10421628",
    "EE 20421629",  # a first digit no registrikood has
    "PL 000001919",
    "PL 00000191930",
]


@pytest.mark.parametrize("raw", INVALID)
def test_a_number_that_fails_its_countrys_rule_is_refused_with_its_message(raw):
    error = refused(raw)
    assert error is not None
    assert error.code == "checksum"
    assert "check digit" in str(error.message)


def test_a_country_without_a_rule_keeps_passing_on_its_prefix_and_a_wrong_prefix_is_refused():
    assert refused("ES B12345678") is None
    assert refused("XX 12") is not None


@pytest.mark.parametrize(
    ("value", "url"),
    [
        ("FR 552081317", "https://annuaire-entreprises.data.gouv.fr/entreprise/552081317"),
        ("FR 552 081 317", "https://annuaire-entreprises.data.gouv.fr/entreprise/552081317"),
        (
            "FR 55208131766522",
            "https://annuaire-entreprises.data.gouv.fr/etablissement/55208131766522",
        ),
        ("NO 923609016", "https://virksomhet.brreg.no/oppslag/enheter/923609016"),
        ("EE 10421629", "https://ariregister.rik.ee/eng/company/10421629"),
        (
            "GB 00445790",
            "https://find-and-update.company-information.service.gov.uk/company/00445790",
        ),
        (
            "GB 445790",
            "https://find-and-update.company-information.service.gov.uk/company/00445790",
        ),
        (
            "GB SC123456",
            "https://find-and-update.company-information.service.gov.uk/company/SC123456",
        ),
        ("NL 33011433", "https://www.kvk.nl/zoeken/?source=all&q=33011433"),
        ("DK 10150817", "https://datacvr.virk.dk/enhed/virksomhed/10150817"),
        (
            "BE 403227515",
            "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html"
            "?ondernemingsnummer=0403227515",
        ),
    ],
)
def test_a_register_number_links_to_its_own_register(value, url):
    assert company.url_for("register", value) == url


@pytest.mark.parametrize("value", ["PT 501234567", "DE HRB 12345", "CZ 27082440", "EL 123456789"])
def test_a_country_without_a_page_of_its_own_leads_to_the_eu_search(value):
    assert company.url_for("register", value) == registers.BRIS


@pytest.mark.parametrize("value", ["CH CHE-123.456.789", "US 12345", "XK 12345"])
def test_a_country_the_eu_search_does_not_cover_has_no_link(value):
    assert company.url_for("register", value) == ""


@pytest.mark.parametrize("value", ["FR 12", "NO 1/../x", "NL 1?x=1", "BE 12", "GB %2e%2e/x"])
def test_a_number_of_the_wrong_shape_never_reaches_a_register_address(value):
    url = company.url_for("register", value)
    assert url in ("", registers.BRIS)


def test_every_register_address_is_https():
    for value in ("FR 552081317", "NO 923609016", "PT 1", "GB 1", "DK 12345678"):
        assert company.url_for("register", value).startswith("https://")


def test_a_register_number_is_drawn_as_a_safe_external_link():
    from django.template.loader import render_to_string

    from postulo.jobs.models import CompanyIdentifier

    html = render_to_string(
        "cotton/identifier_badge.html",
        {"identifier": CompanyIdentifier(scheme="register", value="FR 552081317")},
    )
    assert 'href="https://annuaire-entreprises.data.gouv.fr/entreprise/552081317"' in html
    assert 'rel="noopener noreferrer external"' in html


def test_the_register_scheme_counts_as_linked():

    assert "register" in company.linked()
