"""What is known offline about each country's company register (#935).

Two things, both without a request: the **check digit** of the numbers a register issues, where
its rule is public, and the **address** of the register's own page for a number. Nothing here
asks a register whether a number exists; that is a lookup, which an identifier scheme may not
do (`IdentifierPlugin`).

Every rule is a few lines of arithmetic, written out here with the page that documents it
rather than taken from a library: python-stdnum covers five of the six countries, but it is a
library of some two hundred number formats for the sake of six short sums (#935 records the
decision), and it has no rule for Poland's KRS.

A country that is not in `CHECKS` is not refused anything beyond its two-letter prefix, and a
country that is not in `LINKS` leads to the EU business registers search (BRIS) where it is
one of the countries that search covers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import quote

#: The EU's "Find a company" search over every EU register plus Iceland, Liechtenstein and
#: Norway. It has no address for one company, so it is where a register without a page of its
#: own leads.
BRIS = (
    "https://e-justice.europa.eu/topics/registers-business-insolvency-land/"
    "business-registers-search-company-eu/general-information-find-company_en"
)

#: Where BRIS reaches, by the code a register number begins with (Greece is EL).
_EU = "AT BE BG HR CY CZ DK EE FI FR DE EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE"
BRIS_COUNTRIES = frozenset({*_EU.split(), "IS", "LI", "NO"})


def _number(value: str) -> str:
    """What follows the country: ``FR 552 081 317`` is ``552 081 317``."""
    return value[3:] if value[2:3] == " " else value[2:]


def _compact(number: str) -> str:
    return re.sub(r"[ .\-/]", "", number)


def _weighted(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(digit) * weight for digit, weight in zip(digits, weights, strict=True))


# ------------------------------------------------------------------------ check digits
#
# Each takes the number with its country, spaces and hyphens removed, and says whether it
# is well formed and its check digit agrees. They are tested against numbers the registers
# themselves publish.


def _luhn(digits: str) -> bool:
    total = 0
    for index, digit in enumerate(reversed(digits)):
        n = int(digit) * (2 if index % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def france(digits: str) -> bool:
    """SIREN (9 digits) and SIRET (14, a SIREN and a 5-digit establishment): Luhn.

    INSEE, https://www.sirene.fr/sirene/public/static/immatriculation. The one exception is
    La Poste's SIREN 356000000, whose establishments' SIRET (the head office, ...48, apart) have
    digits that add up to a multiple of 5.
    """
    if len(digits) not in (9, 14) or not _luhn(digits[:9]):
        return False
    if len(digits) == 14 and digits.startswith("356000000") and digits != "35600000000048":
        return sum(map(int, digits)) % 5 == 0
    return len(digits) == 9 or _luhn(digits)


def czechia(digits: str) -> bool:
    """IČO: eight digits, the last a check digit of the first seven weighted 8 down to 2.

    Czech Statistical Office / ARES, "Algoritmus kontroly IČO": ``r = sum mod 11``; the check
    digit is 1 when r is 0, 0 when r is 1, and 11 - r otherwise.
    """
    if len(digits) != 8:
        return False
    r = _weighted(digits[:7], (8, 7, 6, 5, 4, 3, 2)) % 11
    return int(digits[7]) == {0: 1, 1: 0}.get(r, 11 - r)


def norway(digits: str) -> bool:
    """Organisasjonsnummer: nine digits, weights 3 2 7 6 5 4 3 2, modulus 11.

    Brønnøysundregistrene, "Organisasjonsnummeret"
    (https://www.brreg.no/produkter-og-tjenester/): 11 - (sum mod 11), where 11 gives 0 and
    10 is never issued.
    """
    if len(digits) != 9:
        return False
    check = 11 - _weighted(digits[:8], (3, 2, 7, 6, 5, 4, 3, 2)) % 11
    return check != 10 and int(digits[8]) == check % 11


def finland(digits: str) -> bool:
    """Y-tunnus: seven digits, a hyphen and a check digit; weights 7 9 10 5 8 4 2, modulus 11.

    PRH, "Y-tunnuksen tarkistusmerkki": 11 - (sum mod 11), where a remainder of 0 gives 0 and
    a remainder of 1 is never issued.
    """
    if len(digits) != 8:
        return False
    r = _weighted(digits[:7], (7, 9, 10, 5, 8, 4, 2)) % 11
    return r != 1 and int(digits[7]) == (0 if r == 0 else 11 - r)


def estonia(digits: str) -> bool:
    """Registrikood: eight digits, the first 1, 7, 8 or 9; a modulus-11 check in two passes.

    Äriregister, "Registrikoodi kontrollnumber": weights 1..7, and when the remainder is 10 a
    second pass with 3..9, where a remainder of 10 again gives 0.
    """
    if len(digits) != 8 or digits[0] not in "1789":
        return False
    r = _weighted(digits[:7], (1, 2, 3, 4, 5, 6, 7)) % 11
    if r == 10:
        r = _weighted(digits[:7], (3, 4, 5, 6, 7, 8, 9)) % 11
        r = 0 if r == 10 else r
    return int(digits[7]) == r


def poland(digits: str) -> bool:
    """KRS number: ten digits, issued in sequence with no check digit, so only the shape."""
    return len(digits) == 10


#: Country code -> its rule. A country missing here is held to its prefix only.
CHECKS: dict[str, Callable[[str], bool]] = {
    "FR": france,
    "CZ": czechia,
    "NO": norway,
    "FI": finland,
    "EE": estonia,
    "PL": poland,
}


def checks_out(value: str) -> bool | None:
    """Whether a register number passes its country's rule; nothing where there is none."""
    rule = CHECKS.get(value[:2])
    if rule is None:
        return None
    digits = _compact(_number(value))
    return digits.isascii() and digits.isdigit() and rule(digits)


# ----------------------------------------------------------------------------- links
#
# The register's own page for a number, where the register has a stable address for one. Each
# was opened against the register's site when it was added (#935). Every number put in an
# address has first been reduced to digits or letters and digits, and is quoted besides.


def _digits(number: str, *lengths: int) -> str:
    digits = _compact(number)
    return digits if digits.isascii() and digits.isdigit() and len(digits) in lengths else ""


def _france(number: str) -> str:
    digits = _digits(number, 9, 14)
    kind = "entreprise" if len(digits) == 9 else "etablissement"
    # annuaire-entreprises.data.gouv.fr: /entreprise/<SIREN> and /etablissement/<SIRET>.
    return f"https://annuaire-entreprises.data.gouv.fr/{kind}/{digits}" if digits else ""


def _norway(number: str) -> str:
    digits = _digits(number, 9)
    # Brønnøysundregistrene's own search, "Enhetsregisteret".
    return f"https://virksomhet.brreg.no/oppslag/enheter/{digits}" if digits else ""


def _estonia(number: str) -> str:
    digits = _digits(number, 8)
    return f"https://ariregister.rik.ee/eng/company/{digits}" if digits else ""


def _united_kingdom(number: str) -> str:
    code = _compact(number).upper()
    if not re.fullmatch(r"[A-Z0-9]{1,8}", code):
        return ""
    if code.isdigit():
        code = code.zfill(8)  # Companies House writes 445790 as 00445790
    return f"https://find-and-update.company-information.service.gov.uk/company/{quote(code)}"


def _netherlands(number: str) -> str:
    digits = _digits(number, 8)
    # The KVK search page; the register's own company pages are paid extracts.
    return f"https://www.kvk.nl/zoeken/?source=all&q={digits}" if digits else ""


def _denmark(number: str) -> str:
    digits = _digits(number, 8)
    # Virk's CVR search, the address the CVR documentation gives for a company.
    return f"https://datacvr.virk.dk/enhed/virksomhed/{digits}" if digits else ""


def _belgium(number: str) -> str:
    digits = _digits(number, 9, 10)
    if not digits:
        return ""
    # KBO Public Search writes the enterprise number with its leading 0.
    return (
        "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html"
        f"?ondernemingsnummer={digits.zfill(10)}"
    )


LINKS: dict[str, Callable[[str], str]] = {
    "FR": _france,
    "NO": _norway,
    "EE": _estonia,
    "GB": _united_kingdom,
    "NL": _netherlands,
    "DK": _denmark,
    "BE": _belgium,
}


def link_for(value: str) -> str:
    """Where a register number leads: its register's page, else the EU search, else nowhere."""
    country = value[:2]
    page = LINKS[country](_number(value)) if country in LINKS else ""
    if page:
        return page
    return BRIS if country in BRIS_COUNTRIES else ""
