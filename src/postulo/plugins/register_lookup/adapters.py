"""The registers *Register lookup* ships an adapter for, and what each was checked against.

Five national registers that answer without a key, and GLEIF for any company with an LEI
(#1021). Every endpoint, answer and licence below was confirmed on **27 October 2026**
against the register's own documentation, and against one answer the register gave for a
well-known company, fetched by hand and not by Postulo; the recorded answers in
``tests/test_register_lookup.py`` are cut down from those.

**France: API Recherche d'entreprises (DINUM), over Sirene (INSEE) and the RNE (INPI).**
``GET https://recherche-entreprises.api.gouv.fr/search?q=<SIREN>&minimal=true&include=siege``.
The OpenAPI document (https://recherche-entreprises.api.gouv.fr/openapi.json) gives ``q`` as a
name or a SIREN/SIRET, ``minimal`` with ``include=siege`` for the head office alone -- which
keeps the directors, who are people, out of the answer -- and at most 7 requests a second per
address. A result carries ``siren``, ``nom_raison_sociale``, ``nom_complet``,
``etat_administratif`` (``A`` active, ``C`` ceased), ``date_fermeture``,
``activite_principale`` (NAF rév. 2) and ``activite_principale_naf25`` (NAF 2025, which is
NACE 2.1 at its first two digits), ``statut_diffusion``, and ``siege`` with ``numero_voie``,
``type_voie``, ``libelle_voie``, ``code_postal`` and ``libelle_commune``. A company whose
``statut_diffusion`` is not ``O`` has asked for its details to be withheld, and its address is
not offered. The data.gouv.fr page for the service
(https://www.data.gouv.fr/dataservices/api-recherche-dentreprises) says no key and 7 calls a
second; the site's content is under the Licence Ouverte 2.0.

**Norway: Enhetsregisteret (Brønnøysundregistrene).**
``GET https://data.brreg.no/enhetsregisteret/api/enheter/<organisasjonsnummer>``, documented
at https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html: no key, 404 for no
such entity, 410 for one removed for legal reasons (which copies are to remove too, so nothing
is kept), and an entity struck off still answers, with ``slettedato``. The answer has
``navn``, ``forretningsadresse`` (``adresse`` lines, ``postnummer``, ``poststed``,
``landkode``), ``naeringskode1`` (``kode``, ``beskrivelse``), ``konkurs``, ``underAvvikling``
and ``underTvangsavviklingEllerTvangsopplosning``. Norwegian Licence for Open Government Data
(NLOD) 2.0, which asks that the source be named.

**Finland: YTJ open data, version 3 (PRH).**
``GET https://avoindata.prh.fi/opendata-ytj-api/v3/companies?businessId=<Y-tunnus>``. The
OpenAPI document behind https://avoindata.prh.fi/en/ytj/swagger-ui ("Opendata YTJ-Api", v3)
gives ``companies[]`` with ``businessId.value``, ``names[]`` (``type`` 1 is the company name,
an ``endDate`` an old one), ``mainBusinessLine`` (``type``, the TOL code, and ``descriptions``
by language, 3 being English), ``addresses[]`` (``type`` 1 the street address, 2 the postal;
``street``, ``buildingNumber``, ``postCode``, ``postOffices[].city``), ``companySituations[]``
(``SELTILA`` liquidation, ``SANE`` reorganisation, ``KONK`` bankruptcy, from the code list
``/v3/description?code=SELTILA,SANE,KONK``), ``tradeRegisterStatus`` (code list ``REK_KDI``:
1 registered, 2 removed from the register, 4 ceased) and ``endDate``. CC BY 4.0, the source to
be named, and no PRH logo or look-alike layout (https://avoindata.prh.fi/en/info/swagger-ui).

**Czechia: ARES (Ministry of Finance).**
``GET https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/<IČO>``, from the
OpenAPI document at https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/v3/api-docs ("ARES: REST
API - veřejné", 1.4): ``ico``, ``obchodniJmeno``, ``sidlo`` (``nazevUlice``, ``cisloDomovni``,
``cisloOrientacni``, ``psc``, ``nazevObce``, ``kodStatu``), ``datumZaniku`` and lists of
CZ-NACE codes with none marked as the main one, so no activity is offered. The Ministry's
open-data page (https://data.mf.gov.cz/api/ares.html) says anyone may use it who respects the
operating conditions, may block more than 500 queries a minute, and names no licence; the
data are informative, and the source is named.

**Poland: KRS (Ministry of Justice).**
``GET https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/<KRS>?rejestr=P&format=json``, then
``rejestr=S`` (associations) where the first is a 404; documented on the Court Registers
Portal at https://prs.ms.gov.pl/krs/openApi (200, 404 for no such entity, 5xx). The extract has
``odpis.naglowekA.numerKRS``, ``dane.dzial1.danePodmiotu.nazwa``,
``dane.dzial1.siedzibaIAdres.adres`` and the main activity in
``dane.dzial3.przedmiotDzialalnosci.przedmiotPrzewazajacejDzialalnosci`` (PKD, whose division
is NACE's). Nothing in what was confirmed says plainly whether a company is in liquidation, so
no state is offered. The portal names no licence; the source is named.

**GLEIF, for an LEI.** ``GET https://api.gleif.org/api/v1/lei-records/<LEI>`` (JSON:API; the
documentation is linked from https://www.gleif.org/en/lei-data/gleif-api): ``data.attributes
.entity`` has ``legalName.name``, ``legalAddress``, ``registeredAt.id``, ``registeredAs``,
``jurisdiction`` and ``status`` (``ACTIVE`` or ``INACTIVE``). The data are CC0
(https://www.gleif.org/en/meta/lei-data-terms-of-use), and nothing may suggest GLEIF endorses
what is made of them. ``INACTIVE`` is shown and not offered as a state: it covers a merger as
well as a dissolution.

**Estonia is not here.** Every query service of the e-Business Register asks for an agreement
with the RIK and a user name and password, or a key, even where it is free
(https://avaandmed.ariregister.rik.ee/en/open-data-api/introduction-api-services); the one
service it names as needing none, an autocomplete, has no documented address or answer, so
it is left for the keyed registers' issue rather than guessed at.
"""

from __future__ import annotations

import re
from datetime import date

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import RegisterAdapter, RegisterAddress, RegisterRecord

ACTIVE, ADMINISTRATION, DISSOLVED = "active", "administration", "dissolved"


def _digits(number: str, *lengths: int) -> str:
    digits = re.sub(r"[ .\-/]", "", number or "")
    return digits if digits.isascii() and digits.isdigit() and len(digits) in lengths else ""


def _day(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _joined(*parts) -> str:
    return " ".join(
        str(part).strip() for part in parts if part not in (None, "") and str(part).strip()
    )


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------- France


def _france_addresses(number: str) -> list[str]:
    digits = _digits(number, 9, 14)
    if not digits:
        return []
    return [
        "https://recherche-entreprises.api.gouv.fr/search"
        f"?q={digits[:9]}&minimal=true&include=siege&page=1&per_page=5"
    ]


def _france(document, number: str):
    siren = _digits(number, 9, 14)[:9]
    found = next(
        (row for row in _list(_dict(document).get("results")) if _dict(row).get("siren") == siren),
        None,
    )
    if found is None:
        return None
    seat = _dict(found.get("siege"))
    address = None
    # `O` is a company whose details may be published; any other has asked for them to be
    # withheld, which is a choice a lookup keeps.
    if found.get("statut_diffusion") == "O" and not seat.get("code_pays_etranger"):
        address = RegisterAddress(
            street=_joined(
                seat.get("numero_voie"), seat.get("type_voie"), seat.get("libelle_voie")
            ),
            postcode=seat.get("code_postal") or "",
            municipality=seat.get("libelle_commune") or "",
            country="FR",
        )
    ceased = found.get("etat_administratif") == "C"
    code = found.get("activite_principale_naf25") or found.get("activite_principale") or ""
    return RegisterRecord(
        number=siren,
        name=found.get("nom_raison_sociale") or found.get("nom_complet") or "",
        address=address,
        state=DISSOLVED if ceased else ACTIVE if found.get("etat_administratif") == "A" else "",
        state_words="Cessée" if ceased else "Active" if found.get("etat_administratif") else "",
        state_since=_day(found.get("date_fermeture")) if ceased else None,
        activity=code,
        division=code[:2] if isinstance(code, str) else "",
    )


FRANCE = RegisterAdapter(
    key="fr-recherche-entreprises",
    label="Annuaire des Entreprises",
    scheme="register",
    country="FR",
    hosts=("recherche-entreprises.api.gouv.fr",),
    addresses=_france_addresses,
    read=_france,
    source=_("INSEE (Sirene) and INPI (RNE), through the DINUM's API Recherche d'entreprises"),
    licence="Licence Ouverte 2.0",
    licence_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
    page=lambda number: (
        f"https://annuaire-entreprises.data.gouv.fr/entreprise/{_digits(number, 9, 14)[:9]}"
        if _digits(number, 9, 14)
        else ""
    ),
)


# ---------------------------------------------------------------------------- Norway


def _norway(document, number: str):
    document = _dict(document)
    digits = _digits(number, 9)
    if not digits or document.get("organisasjonsnummer") != digits:
        return None
    office = _dict(document.get("forretningsadresse")) or _dict(document.get("postadresse"))
    address = None
    if office:
        address = RegisterAddress(
            street=", ".join(str(line) for line in _list(office.get("adresse")) if line),
            postcode=office.get("postnummer") or "",
            municipality=office.get("poststed") or "",
            country=office.get("landkode") or "",
        )
    if document.get("slettedato"):
        state, words = DISSOLVED, "Slettet"
    elif document.get("konkurs"):
        state, words = ADMINISTRATION, "Konkurs"
    elif document.get("underTvangsavviklingEllerTvangsopplosning"):
        state, words = ADMINISTRATION, "Under tvangsavvikling eller tvangsoppløsning"
    elif document.get("underAvvikling"):
        state, words = ADMINISTRATION, "Under avvikling"
    else:
        state, words = ACTIVE, ""
    since = {
        DISSOLVED: _day(document.get("slettedato")),
        ADMINISTRATION: _day(document.get("konkursdato"))
        or _day(document.get("underAvviklingDato")),
    }.get(state)
    activity = _dict(document.get("naeringskode1"))
    code = activity.get("kode") or ""
    return RegisterRecord(
        number=digits,
        name=document.get("navn") or "",
        address=address,
        state=state,
        state_words=words,
        state_since=since,
        activity=code,
        activity_words=activity.get("beskrivelse") or "",
        division=code[:2] if isinstance(code, str) else "",
    )


NORWAY = RegisterAdapter(
    key="no-enhetsregisteret",
    label="Enhetsregisteret",
    scheme="register",
    country="NO",
    hosts=("data.brreg.no",),
    addresses=lambda number: (
        [f"https://data.brreg.no/enhetsregisteret/api/enheter/{_digits(number, 9)}"]
        if _digits(number, 9)
        else []
    ),
    read=_norway,
    source=_("Brønnøysund Register Centre (Enhetsregisteret)"),
    licence="NLOD 2.0",
    licence_url="https://data.norge.no/nlod/en/2.0",
    page=lambda number: (
        f"https://virksomhet.brreg.no/oppslag/enheter/{_digits(number, 9)}"
        if _digits(number, 9)
        else ""
    ),
)


# --------------------------------------------------------------------------- Finland

#: The words of PRH's code lists, in English, as `/v3/description` gives them.
_FINNISH_SITUATIONS = {
    "SELTILA": "Liquidation",
    "SANE": "Company re-organisation",
    "KONK": "Bankrupt",
}
_FINNISH_REGISTER = {"1": "Registered", "2": "Removed from register", "4": "Ceased"}


def _business_id(number: str) -> str:
    digits = _digits(number, 8)
    return f"{digits[:7]}-{digits[7]}" if digits else ""


def _finland(document, number: str):
    wanted = _business_id(number)
    found = next(
        (
            row
            for row in _list(_dict(document).get("companies"))
            if _dict(_dict(row).get("businessId")).get("value") == wanted
        ),
        None,
    )
    if found is None or not wanted:
        return None
    names = [
        _dict(row)
        for row in _list(found.get("names"))
        if str(_dict(row).get("type")) == "1" and not _dict(row).get("endDate")
    ]
    addresses = sorted(
        (_dict(row) for row in _list(found.get("addresses"))),
        key=lambda row: row.get("type") != 1,
    )
    address = None
    if addresses:
        office = addresses[0]
        towns = [_dict(row) for row in _list(office.get("postOffices"))]
        town = next((row for row in towns if str(row.get("languageCode")) == "1"), None) or (
            towns[0] if towns else {}
        )
        address = RegisterAddress(
            street=_joined(
                office.get("street"),
                office.get("buildingNumber"),
                office.get("entrance"),
                office.get("apartmentNumber"),
            ),
            postcode=office.get("postCode") or "",
            municipality=town.get("city") or "",
            country=office.get("country") or "FI",
        )
    situations = [
        _dict(row)
        for row in _list(found.get("companySituations"))
        if not _dict(row).get("endDate") and _dict(row).get("type") in _FINNISH_SITUATIONS
    ]
    status = str(found.get("tradeRegisterStatus") or "")
    if found.get("endDate") or status in ("2", "4"):
        state, words = DISSOLVED, _FINNISH_REGISTER.get(status, "Ceased")
        since = _day(found.get("endDate"))
    elif situations:
        state, words = ADMINISTRATION, _FINNISH_SITUATIONS[situations[0]["type"]]
        since = _day(situations[0].get("registrationDate"))
    elif status == "1":
        state, words, since = ACTIVE, _FINNISH_REGISTER["1"], None
    else:
        state, words, since = "", "", None
    line = _dict(found.get("mainBusinessLine"))
    code = str(line.get("type") or "")
    english = next(
        (
            _dict(row).get("description")
            for row in _list(line.get("descriptions"))
            if str(_dict(row).get("languageCode")) == "3"
        ),
        "",
    )
    return RegisterRecord(
        number=wanted,
        name=names[0].get("name", "") if names else "",
        address=address,
        state=state,
        state_words=words,
        state_since=since,
        activity=code,
        activity_words=english or "",
        division=code[:2],
    )


FINLAND = RegisterAdapter(
    key="fi-ytj",
    label="YTJ",
    scheme="register",
    country="FI",
    hosts=("avoindata.prh.fi",),
    addresses=lambda number: (
        [
            "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"
            f"?businessId={_business_id(number)}"
        ]
        if _business_id(number)
        else []
    ),
    read=_finland,
    source=_("Finnish Patent and Registration Office (PRH), Business Information System (YTJ)"),
    licence="CC BY 4.0",
    licence_url="https://creativecommons.org/licenses/by/4.0/",
)


# --------------------------------------------------------------------------- Czechia


def _czechia(document, number: str):
    document = _dict(document)
    digits = _digits(number, 8)
    if not digits or document.get("ico") != digits:
        return None
    seat = _dict(document.get("sidlo"))
    house = seat.get("cisloDomovni") or ""
    if seat.get("cisloOrientacni"):
        house = f"{house}/{seat['cisloOrientacni']}{seat.get('cisloOrientacniPismeno') or ''}"
    postcode = str(seat.get("psc") or "")
    address = None
    if seat:
        address = RegisterAddress(
            street=_joined(seat.get("nazevUlice") or seat.get("nazevCastiObce"), house),
            postcode=f"{postcode[:3]} {postcode[3:]}" if len(postcode) == 5 else postcode,
            municipality=seat.get("nazevObce") or "",
            country=seat.get("kodStatu") or "",
        )
    ended = _day(document.get("datumZaniku"))
    return RegisterRecord(
        number=digits,
        name=document.get("obchodniJmeno") or "",
        address=address,
        state=DISSOLVED if ended else "",
        state_words="Zaniklý" if ended else "",
        state_since=ended,
    )


CZECHIA = RegisterAdapter(
    key="cz-ares",
    label="ARES",
    scheme="register",
    country="CZ",
    hosts=("ares.gov.cz",),
    addresses=lambda number: (
        [
            "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/"
            f"{_digits(number, 8)}"
        ]
        if _digits(number, 8)
        else []
    ),
    read=_czechia,
    source=_("ARES, Ministry of Finance of the Czech Republic"),
)


# ---------------------------------------------------------------------------- Poland


def _poland(document, number: str):
    extract = _dict(_dict(document).get("odpis"))
    digits = _digits(number, 10)
    if not digits or _dict(extract.get("naglowekA")).get("numerKRS") != digits:
        return None
    data = _dict(extract.get("dane"))
    first = _dict(data.get("dzial1"))
    seat = _dict(_dict(first.get("siedzibaIAdres")).get("adres"))
    address = None
    if seat:
        house = seat.get("nrDomu") or ""
        if seat.get("nrLokalu"):
            house = f"{house}/{seat['nrLokalu']}"
        address = RegisterAddress(
            street=_joined(seat.get("ulica"), house),
            postcode=seat.get("kodPocztowy") or "",
            municipality=seat.get("miejscowosc") or "",
            country="PL" if (seat.get("kraj") or "POLSKA") == "POLSKA" else "",
        )
    business = _dict(_dict(data.get("dzial3")).get("przedmiotDzialalnosci"))
    main = next(iter(_list(business.get("przedmiotPrzewazajacejDzialalnosci"))), {})
    main = _dict(main)
    division = str(main.get("kodDzial") or "")
    code = ".".join(
        str(part) for part in (division, main.get("kodKlasa"), main.get("kodPodklasa")) if part
    )
    return RegisterRecord(
        number=digits,
        name=_dict(first.get("danePodmiotu")).get("nazwa") or "",
        address=address,
        activity=code,
        activity_words=main.get("opis") or "",
        division=division,
    )


POLAND = RegisterAdapter(
    key="pl-krs",
    label="KRS",
    scheme="register",
    country="PL",
    hosts=("api-krs.ms.gov.pl",),
    addresses=lambda number: (
        [
            f"https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{_digits(number, 10)}"
            f"?rejestr={register}&format=json"
            for register in ("P", "S")
        ]
        if _digits(number, 10)
        else []
    ),
    read=_poland,
    source=_("National Court Register (KRS), Ministry of Justice of Poland"),
)


# ----------------------------------------------------------------------------- GLEIF

_LEI = re.compile(r"[0-9A-Z]{18}[0-9]{2}")


def _gleif(document, number: str):
    data = _dict(_dict(document).get("data"))
    if not _LEI.fullmatch(number or "") or data.get("id") != number:
        return None
    entity = _dict(_dict(data.get("attributes")).get("entity"))
    office = _dict(entity.get("legalAddress"))
    address = None
    if office:
        address = RegisterAddress(
            street=", ".join(str(line) for line in _list(office.get("addressLines")) if line),
            postcode=office.get("postalCode") or "",
            municipality=office.get("city") or "",
            country=office.get("country") or "",
        )
    status = entity.get("status")
    return RegisterRecord(
        number=number,
        name=_dict(entity.get("legalName")).get("name") or "",
        address=address,
        state=ACTIVE if status == "ACTIVE" else "",
        state_words={"ACTIVE": "Active", "INACTIVE": "Inactive"}.get(status, ""),
        registered_as=entity.get("registeredAs") or "",
        registered_at=_dict(entity.get("registeredAt")).get("id") or "",
        jurisdiction=entity.get("jurisdiction") or "",
    )


GLEIF = RegisterAdapter(
    key="gleif",
    label="GLEIF",
    scheme="lei",
    country="",
    hosts=("api.gleif.org",),
    addresses=lambda number: (
        [f"https://api.gleif.org/api/v1/lei-records/{number}"] if _LEI.fullmatch(number) else []
    ),
    read=_gleif,
    source=_("Global Legal Entity Identifier Foundation (GLEIF)"),
    licence="CC0 1.0",
    licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
    page=lambda number: (
        f"https://search.gleif.org/#/record/{number}" if _LEI.fullmatch(number) else ""
    ),
    headers=(("Accept", "application/vnd.api+json"),),
)

#: Every adapter Postulo ships, in the order the record of processing lists them.
ADAPTERS = (FRANCE, NORWAY, FINLAND, CZECHIA, POLAND, GLEIF)
