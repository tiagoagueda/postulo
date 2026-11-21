"""Arbetsförmedlingen's JobTech JobSearch: Sweden's public employment service, searched (#1253).

Confirmed on **17 November 2026** against the service's own documentation and against real
answers, fetched by hand and not by Postulo; the recorded answers in
``tests/test_listing_search_sweden.py`` are cut down from those.

**The service.** JobSearch, version 1.37.0 of its OpenAPI document
(https://jobsearch.api.jobtechdev.se/swagger.json), searches the adverts currently open on
Platsbanken. ``GET https://jobsearch.api.jobtechdev.se/search`` takes ``q`` (free text, read
for occupations, skills and places: "utvecklare Göteborg" is read as the occupation
*systemutvecklare* and the place *göteborg*), ``remote`` (``true`` keeps the adverts whose text
matches phrases about working from home), ``published-after`` (a date, or a number of minutes),
``offset`` (at most 2000; past it the service answers 400) and ``limit`` (at most 100); ``GET
/ad/<id>`` answers one advert, or 404 with a message for an advert it does not have. The
``X-Fields`` header narrows what a search answers to the fields named, as the Getting Started
guide (gitlab.com/arbetsformedlingen/job-ads/jobsearch-apis, ``docs/GettingStartedJobSearchEN.md``)
describes it; ``/ad/<id>`` answers whole whatever it says.

**No key.** The data portal's page for the service
(https://data.arbetsformedlingen.se/dataservice/jobsearch/) says "API nyckel eller
registrering: Nej" and "Åtkomsträttigheter: Publik"; every request made on 17 November 2026
was answered without one, and the OpenAPI document declares no security scheme. (The Getting
Started guide still says a key is needed to try the Swagger page, which is out of date.)

**The licence.** The OpenAPI document's ``info.license`` is "Ads are licensed under CC0", and
the portal page says "Licens: Creative Commons CC0". The service asks that it not be used to
collect every advert, for which JobStream exists: Postulo asks for one page of twenty per
press. A 429 is documented ("Rate limit exceeded") with no figure given, and is said to the
person, never retried.

**What is read.** ``headline``, ``employer.name`` (else ``employer.workplace``),
``workplace_address`` (``municipality``, ``region``, and ``country`` where it is not Sweden),
``description.text`` (plain text), ``publication_date``, ``application_deadline``,
``webpage_url`` (the advert on Platsbanken), and three concepts of the JobTech Taxonomy, by
their identifier and never by their Swedish label: ``working_hours_type`` (``Heltid`` full
time, ``Deltid`` part time), ``duration`` and ``employment_type`` (a fixed term, a temporary
post or a seasonal one is a contract), and ``workplace_model`` (``Distansarbete`` remote,
``Hybridarbete`` hybrid, ``Arbete på plats`` on site), each checked against the taxonomy's own
list (``taxonomy.api.jobtechdev.se/v1/taxonomy/main/concepts?type=...``). The salary is a type
and free text with no amounts, so none is read. **Never read**: ``application_contacts``,
the employer's telephone and e-mail address and ``application_details``, which name people,
and the logo. An advert marked ``removed`` is left out.

**What is sent.** The words and the place, together in ``q``; ``remote`` when ticked;
``published-after`` in minutes; the page as ``offset`` and ``limit``. For *Review*, the
advert's number in the path. Nothing about the person.
"""

from __future__ import annotations

import datetime as dt
import re

from postulo.plugins.api import JobPostingData, ListingSearch, SearchHit, SearchResults

HOST = "jobsearch.api.jobtechdev.se"
SOURCE = "Arbetsförmedlingen"
#: Where JobSearch stops paging: an offset past it is refused.
LAST_OFFSET = 2000

#: What a search answers, and no more: the adverts' text is read on *Review*, not here.
FIELDS = (
    "total{value}, hits{id, headline, webpage_url, removed, publication_date, "
    "application_deadline, employer{name, workplace}, "
    "workplace_address{municipality, region, country}, working_hours_type{concept_id}, "
    "duration{concept_id}, employment_type{concept_id}, workplace_model{concept_id}}"
)

#: The JobTech Taxonomy's concepts, by identifier (checked 2026-11-17).
FULL_TIME = "6YE1_gAC_R2G"  # worktime-extent: Heltid
PART_TIME = "947z_JGS_Uk2"  # worktime-extent: Deltid
UNTIL_FURTHER_NOTICE = "a7uU_j21_mkL"  # employment-duration: Tills vidare
FIXED_TERM = {
    "sTu5_NBQ_udq",  # employment-type: Tidsbegränsad anställning
    "gro4_cWF_6D7",  # employment-type: Vikariat
    "EBhX_Qm2_8eX",  # employment-type: Säsongsanställning
}
WORKPLACE = {
    "L4T8_Z9y_DkN": "remote",  # work-place-model: Distansarbete
    "Vbpe_rGe_vCW": "hybrid",  # work-place-model: Hybridarbete
    "ZxwM_gtz_n61": "onsite",  # work-place-model: Arbete på plats
}

_PLATSBANKEN = re.compile(r"https://arbetsformedlingen\.se/platsbanken/annonser/[0-9]{1,20}")


def _concept(ad, name) -> str:
    value = ad.get(name)
    return value.get("concept_id") or "" if isinstance(value, dict) else ""


def _words(value) -> str:
    return value if isinstance(value, str) else ""


def _date(value):
    """The day of a ``2026-11-26T23:59:59`` the service writes, or ``None``."""
    try:
        return dt.date.fromisoformat(_words(value)[:10])
    except ValueError:
        return None


def _employment(ad) -> str:
    duration = _concept(ad, "duration")
    if (duration and duration != UNTIL_FURTHER_NOTICE) or _concept(
        ad, "employment_type"
    ) in FIXED_TERM:
        return "contract"
    return {FULL_TIME: "full_time", PART_TIME: "part_time"}.get(
        _concept(ad, "working_hours_type"), ""
    )


def _location(ad) -> str:
    where = ad.get("workplace_address")
    if not isinstance(where, dict):
        return ""
    parts = [_words(where.get("municipality")), _words(where.get("region"))]
    country = _words(where.get("country"))
    if country and country != "Sverige":
        parts.append(country)
    named: list[str] = []
    for part in parts:
        if part and part not in named:
            named.append(part)
    return ", ".join(named)


def posting(ad) -> JobPostingData | None:
    """One advert as a posting, or ``None`` for one without a title, a number or not open."""
    if not isinstance(ad, dict) or ad.get("removed") or not _words(ad.get("headline")).strip():
        return None
    number = str(ad.get("id") or "")
    if not number.isdigit():
        return None
    page = _words(ad.get("webpage_url"))
    if not _PLATSBANKEN.fullmatch(page):
        page = f"https://arbetsformedlingen.se/platsbanken/annonser/{number}"
    employer = ad.get("employer") if isinstance(ad.get("employer"), dict) else {}
    description = ad.get("description") if isinstance(ad.get("description"), dict) else {}
    return JobPostingData(
        title=_words(ad.get("headline"))[:500],
        company_name=(_words(employer.get("name")) or _words(employer.get("workplace")))[:500],
        location=_location(ad)[:500],
        remote_type=WORKPLACE.get(_concept(ad, "workplace_model"), ""),
        employment_type=_employment(ad),
        description=_words(description.get("text")),
        posted_at=_date(ad.get("publication_date")),
        closes_at=_date(ad.get("application_deadline")),
        url=page,
        source=SOURCE,
    )


def request(criteria):
    parameters: dict = {}
    words = " ".join(part for part in (criteria.words, criteria.place) if part)
    if words:
        parameters["q"] = words
    if criteria.remote:
        parameters["remote"] = True
    if criteria.since_days:
        parameters["published-after"] = criteria.since_days * 24 * 60
    parameters["offset"] = min(criteria.offset, LAST_OFFSET)
    parameters["limit"] = criteria.size
    return f"https://{HOST}/search", parameters


def read(document, criteria) -> SearchResults:
    if not isinstance(document, dict):
        return SearchResults()
    hits = []
    for ad in document.get("hits") or ():
        found = posting(ad)
        if found is not None:
            hits.append(SearchHit(key=str(ad["id"]), posting=found))
    total = document.get("total")
    total = total.get("value") if isinstance(total, dict) else None
    total = total if isinstance(total, int) and total >= 0 else None
    shown = criteria.offset + len(document.get("hits") or ())
    # The next page has to start within the service's last offset as well as within its total.
    more = total is not None and shown < total and shown <= LAST_OFFSET
    return SearchResults(hits=tuple(hits), total=total, more=more)


def one(key):
    return f"https://{HOST}/ad/{key}", {}


def read_one(document, key) -> JobPostingData | None:
    if not isinstance(document, dict) or str(document.get("id") or "") != key:
        return None
    return posting(document)


SWEDEN = ListingSearch(
    key="se-arbetsformedlingen",
    label="Platsbanken (Arbetsförmedlingen)",
    countries=("SE",),
    languages=("sv",),
    hosts=(HOST,),
    supports=frozenset({"words", "place", "remote", "since"}),
    request=request,
    read=read,
    one=one,
    read_one=read_one,
    source=SOURCE,
    licence="CC0 1.0",
    licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
    terms_url="https://data.arbetsformedlingen.se/dataservice/jobsearch/",
    headers=(("X-Fields", FIELDS),),
)

#: Every search this package contributes.
SEARCHES = (SWEDEN,)
