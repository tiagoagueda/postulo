"""Suggesting a publication from a public registry, when a person presses for it (#688).

Typing a paper's authors, venue, volume and pages by hand is the part of adding a publication
nobody wants to do, and two public services already know them: the DOI system, which answers
for a single work whichever agency registered it, and ORCID, which lists a researcher's works.
This is the one place that asks either of them anything, and it asks only when a person
presses a button: *Look up* beside a publication's DOI, or *Suggest from my ORCID record* on
the Publications section. Never on a page load, while typing, on an import, on a schedule or
in a worker; no request is tried again after a refusal, a 429, a 503 or a timeout.

**Two requests, to two fixed hosts.**

- `from_doi`: ``GET https://doi.org/<doi>`` with ``Accept: application/vnd.citationstyles
  .csl+json``, DOI content negotiation as the DOI Foundation's citation service documents it.
  doi.org redirects to the metadata service of the agency that registered the DOI (Crossref's
  ``api.crossref.org``, DataCite's ``data.crosscite.org``, mEDRA's), which answers with one
  CSL JSON record. 2 MiB and 10 seconds in all, three redirects at most, each one https and
  each one approved by the guard.
- `from_orcid`: ``GET https://pub.orcid.org/v3.0/<orcid>/works`` with ``Accept:
  application/json`` (the default answer is XML), ORCID's Anonymous API: no credentials, which
  ORCID grants to individuals and Postulo could not ship. The works *summary*: a title, a
  type, a date, a journal and the identifiers, and no authors. 4 MiB and 10 seconds.

Both go through `postulo.plugins.http.public_only_client` inside `until(...)`, and are read
with `read_body`, which stops at the byte limit and at the deadline (rule 5 of the threat
model). The only thing of the person's in a request is the DOI or the ORCID, each checked for
its shape first (an ORCID by its check digit too) and percent-encoded into the path: no query,
no email, no account. The client's own ``User-Agent`` names the project, never the operator or
the person; the identified pools Crossref and DataCite offer want an email address, which they
log, and none is sent.

**What is kept is a short list, read as untrusted text.** Metadata is deposited by publishers
and an ORCID record is curated by its holder; either can be wrong, or hostile. So only the
type, the title, up to `MAX_AUTHORS` authors (then ``and others``), where it appeared, the
publisher, the date, the volume, the issue, the pages, the DOI and the language are read, and
nothing else of the record -- the abstract, the references, the funders, the links -- is read
into anything. The ``URL`` a record carries is ignored: a CV links the DOI. Each value is
stripped of markup (a title carries ``<i>`` and MathML), of control characters and of
bidirectional overrides, cut to its column's length, and drawn escaped. An unknown type is
*Other*, never a refusal. And nothing here saves anything: the form is the review.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

import httpx
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from postulo.core import language_names, languages
from postulo.plugins import http
from postulo.plugins.identifiers.schemes import orcid_checks_out

from . import publications

DOI_HOST = "doi.org"
DOI_URL = "https://doi.org/{doi}"
#: CSL JSON, the one format every agency's content negotiation answers in (Crossref, DataCite
#: and mEDRA by the DOI Foundation's table), and the one whose field names this reads.
CSL_JSON = "application/vnd.citationstyles.csl+json"
#: A Crossref record is some tens of kilobytes, most of it the references and the abstract; a
#: physics paper with five thousand authors was 682 KB. Two mebibytes is a record nobody meant.
DOI_BYTES = 2 * 1024 * 1024

ORCID_HOST = "pub.orcid.org"
ORCID_URL = "https://pub.orcid.org/v3.0/{orcid}/works"
ORCID_BYTES = 4 * 1024 * 1024

#: The whole of one request, redirects and the body included.
SECONDS = 10.0
MAX_REDIRECTS = 3

#: How many authors are kept before ``and others``, BibTeX's own words for the rest.
MAX_AUTHORS = 100
OTHERS = "and others"
#: How many of an ORCID record's works the review lists, and how many it adds at one press.
MAX_WORKS = 100
MAX_ADDED = 20

_ORCID = re.compile(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]")

#: Who answered, by the host doi.org sent the request on to. Names, not words: never translated.
AGENCIES = {
    "api.crossref.org": "Crossref",
    "data.crosscite.org": "DataCite",
    "api.datacite.org": "DataCite",
    "data.datacite.org": "DataCite",
    "data.medra.org": "mEDRA",
}

#: The types each service names, mapped onto `publications.TYPES`. Three vocabularies arrive:
#: CSL's (``article-journal``, what DataCite answers in), Crossref's own (``journal-article``,
#: which its CSL answer still carries) and ORCID's (``conference-paper``, ``data-set``). A name
#: not here is *Other*.
TYPE_MAP = {
    # CSL 1.0.2
    "article-journal": "article",
    "article-magazine": "article",
    "article-newspaper": "article",
    "paper-conference": "inproceedings",
    "book": "book",
    "chapter": "incollection",
    "entry-encyclopedia": "incollection",
    "entry-dictionary": "incollection",
    "thesis": "phdthesis",
    "report": "techreport",
    "dataset": "dataset",
    "software": "software",
    "webpage": "online",
    "post": "online",
    "post-weblog": "online",
    "manuscript": "unpublished",
    # Crossref's
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "proceedings": "proceedings",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "book-chapter": "incollection",
    "book-section": "incollection",
    "book-part": "incollection",
    "reference-entry": "incollection",
    "dissertation": "phdthesis",
    "report-component": "techreport",
    # ORCID's (v3.0)
    "conference-paper": "inproceedings",
    "dissertation-thesis": "phdthesis",
    "data-set": "dataset",
    "magazine-article": "article",
    "newspaper-article": "article",
    "encyclopedia-entry": "incollection",
    "dictionary-entry": "incollection",
    "website": "online",
    "manual": "manual",
}


def offered(person) -> bool:
    """Whether *Look up* and *Suggest from my ORCID record* are offered to ``person`` (#688)."""
    from postulo.plugins.policy import decide
    from postulo.plugins.publication_lookups import PUBLICATION_LOOKUPS

    return bool(decide(PUBLICATION_LOOKUPS, person).on)


class LookupRefused(Exception):
    """Why nothing came back, in a sentence a person can read."""


@dataclass
class Found:
    """What one DOI's record said, by the form's field names, and who said it."""

    service: str
    values: dict[str, str]


# ------------------------------------------------------------------- reading a value


def _limit(name: str) -> int:
    from .models import Publication

    return Publication._meta.get_field(name).max_length or 200


_TAG = re.compile(r"<[^<>]*>")


def clean(value, limit: int) -> str:
    """A value as plain text on one line: no markup, no control or formatting characters.

    A number is read as its digits; anything else that is not text is nothing. Tags are taken
    out before entities are read, so ``H<sub>2</sub>O`` is ``H2O`` and ``&lt;`` is a ``<``
    drawn escaped, never a tag. A formatting character -- a bidirectional override, a zero
    width joiner -- is dropped; a control character or a line break is a space.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    text = html.unescape(_TAG.sub("", value))
    text = unicodedata.normalize("NFC", text)
    kept = "".join(
        ""
        if unicodedata.category(ch) == "Cf"
        else " "
        if unicodedata.category(ch) in ("Cc", "Zl", "Zp")
        else ch
        for ch in text
    )
    return " ".join(kept.split())[:limit].strip()


def _first(value):
    """A CSL value that may arrive as a list of one (Crossref's own API writes titles so)."""
    if isinstance(value, list):
        return next((one for one in value if isinstance(one, str)), "")
    return value


def entry_type_for(name) -> str:
    """The type a service's name for it is, or *Other*."""
    return TYPE_MAP.get(name if isinstance(name, str) else "", "misc")


def _name(person) -> str:
    """One person of a CSL name list, as the form takes a name: ``Family, Given``."""
    if not isinstance(person, dict):
        return ""
    whole = clean(person.get("literal") or person.get("name"), 200)
    if whole:
        return whole
    family = clean(
        " ".join(
            part
            for part in (person.get("non-dropping-particle"), person.get("family"))
            if isinstance(part, str)
        ),
        200,
    )
    given = clean(person.get("given"), 200)
    if family and given:
        return f"{family}, {given}"
    return family or given


def names(people) -> str:
    """Up to `MAX_AUTHORS` names, one per line, then ``and others`` for whoever is left.

    Held to the column's length as well, a whole name at a time: five thousand authors are a
    hundred and a line saying there were more, never half a name.
    """
    if not isinstance(people, list):
        return ""
    found = [name for name in (_name(person) for person in people) if name]
    budget = _limit("authors") - len("\n" + OTHERS)
    kept: list[str] = []
    used = -1
    for name in found[:MAX_AUTHORS]:
        if used + 1 + len(name) > budget:
            break
        kept.append(name)
        used += 1 + len(name)
    if len(kept) < len(found):
        kept.append(OTHERS)
    return "\n".join(kept)


def date_from(parts) -> str:
    """``2024``, ``2024-05`` or ``2024-05-17`` from CSL's ``date-parts``; nothing if odd."""
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return ""
    numbers: list[int] = []
    for part in parts[0][:3]:
        try:
            numbers.append(int(str(part).strip()))
        except ValueError:
            break
    if not numbers:
        return ""
    text = "-".join([f"{numbers[0]:04d}", *(f"{n:02d}" for n in numbers[1:])])
    try:
        publications.validate_date(text)
    except ValidationError:
        # A month past twelve says the day is wrong too; the year alone is still the year.
        text = f"{numbers[0]:04d}"
        try:
            publications.validate_date(text)
        except ValidationError:
            return ""
    return text


def doi_from(value) -> str:
    doi = publications.normalise_doi(clean(value, publications.DOI_MAX_LENGTH))
    try:
        publications.validate_doi(doi)
    except ValidationError:
        return ""
    return doi


def language_from(value) -> str:
    """The language a record names, where it is one the form's menu holds; else nothing."""
    tag = clean(value, languages.MAX_LENGTH)
    if not tag or not languages.well_formed(tag):
        return ""
    return languages.match(tag)


def from_csl(record) -> dict[str, str]:
    """What the form takes from one CSL JSON record, by field name, blanks left out.

    Read field by field, and nothing else: an abstract, the references, the funders, the
    licence and the ``URL`` are in the record and never in what comes back.
    """
    if not isinstance(record, dict):
        return {}
    date = ""
    for key in ("issued", "published-print", "published-online", "published"):
        held = record.get(key)
        if isinstance(held, dict):
            date = date_from(held.get("date-parts"))
            if date:
                break
    values = {
        "entry_type": entry_type_for(record.get("type")),
        "title": clean(_first(record.get("title")), _limit("title")),
        "authors": names(record.get("author")),
        "container_title": clean(_first(record.get("container-title")), _limit("container_title")),
        "publisher": clean(record.get("publisher"), _limit("publisher")),
        "date": date,
        "volume": clean(record.get("volume"), _limit("volume")),
        "number": clean(record.get("issue"), _limit("number")),
        "pages": clean(record.get("page"), _limit("pages")),
        "doi": doi_from(record.get("DOI")),
        "language": language_from(record.get("language")),
    }
    return {name: value for name, value in values.items() if value}


# ------------------------------------------------------------------- filling a form


#: The order the fields are named in when the page says which were filled.
FILLED_ORDER = (
    "entry_type",
    "title",
    "authors",
    "container_title",
    "publisher",
    "date",
    "volume",
    "number",
    "pages",
    "doi",
    "language",
)


def fill(current: dict, found: dict, *, type_is_blank: bool = False) -> tuple[dict, list[str]]:
    """``current`` with each of ``found`` written where ``current`` is blank, and which were.

    Blanks only: what a person typed is never replaced, a value that differs included. The
    type always has a value, so it is filled only where the caller says the one there is a
    choice nobody made yet (a new entry still on the type it opened with).
    """
    merged = dict(current)
    filled: list[str] = []
    for name in FILLED_ORDER:
        value = found.get(name, "")
        if not value:
            continue
        blank = type_is_blank if name == "entry_type" else not str(merged.get(name) or "").strip()
        if blank and merged.get(name) != value:
            merged[name] = value
            filled.append(name)
    return merged, filled


# -------------------------------------------------------------------- ORCID's works


def works_from(document) -> tuple[list[dict], int]:
    """The public works an ORCID works summary lists, at most `MAX_WORKS`, and how many it had.

    One per group, as ORCID's own page shows them: the summary the holder ranks first (the
    highest ``display-index``). Each is a type, a title, the journal, a date and the DOI the
    work itself carries -- an identifier whose relationship is ``self``; one marked ``part-of``
    is the book's or the proceedings', not the work's. A summary has no authors.
    """
    groups = document.get("group") if isinstance(document, dict) else None
    if not isinstance(groups, list):
        raise LookupRefused(_("ORCID's answer was not a list of works."))
    works: list[dict] = []
    for group in groups:
        summaries = group.get("work-summary") if isinstance(group, dict) else None
        summaries = [one for one in summaries or [] if isinstance(one, dict)]
        if not summaries:
            continue
        summary = max(summaries, key=_display_index)
        if summary.get("visibility", "public") != "public":
            continue
        work = _work(summary)
        if work:
            works.append(work)
    return works[:MAX_WORKS], len(works)


def _display_index(summary: dict) -> int:
    try:
        return int(summary.get("display-index") or 0)
    except (TypeError, ValueError):
        return 0


def _value(holder, *path):
    """``holder[a]["value"]``-style reading that answers ``None`` for any shape it was not."""
    for key in path:
        if not isinstance(holder, dict):
            return None
        holder = holder.get(key)
    return holder


def _work(summary: dict) -> dict:
    title = clean(_value(summary, "title", "title", "value"), _limit("title"))
    if not title:
        return {}
    held: list = []
    # Year, then month, then day, each only after the one before it: a month without a year
    # is not a date.
    for unit in ("year", "month", "day"):
        part = _value(summary, "publication-date", unit, "value")
        if not part:
            break
        held.append(part)
    doi = ""
    for row in _value(summary, "external-ids", "external-id") or []:
        if (
            isinstance(row, dict)
            and str(row.get("external-id-type", "")).lower() == "doi"
            and row.get("external-id-relationship", "self") == "self"
        ):
            doi = doi_from(row.get("external-id-value"))
            if doi:
                break
    return {
        "entry_type": entry_type_for(summary.get("type")),
        "title": title,
        "container_title": clean(
            _value(summary, "journal-title", "value"), _limit("container_title")
        ),
        "date": date_from([held]) if held else "",
        "doi": doi,
    }


def is_held(work: dict, held: list) -> bool:
    """Whether one of ``held`` (publications) is this work: the same DOI, else title and year."""
    doi = (work.get("doi") or "").lower()
    title = language_names.fold(work.get("title"))
    year = publications.year_of(work.get("date") or "")
    for item in held:
        if doi and item.doi and item.doi.lower() == doi:
            return True
        if title and language_names.fold(item.title) == title:
            if publications.year_of(item.date) == year:
                return True
    return False


# ------------------------------------------------------------------- the requests


def valid_orcid(value) -> str:
    """The ORCID, if it is shaped like one and its check digit agrees; else nothing."""
    text = str(value or "").strip().upper()
    return text if _ORCID.fullmatch(text) and orcid_checks_out(text) else ""


def from_doi(doi: str) -> Found:
    """Ask doi.org for one DOI's record. Raises `LookupRefused` with the reason."""
    doi = publications.normalise_doi(doi)
    if not doi:
        raise LookupRefused(_("Type the DOI first."))
    try:
        publications.validate_doi(doi)
    except ValidationError as error:
        raise LookupRefused(_("That is not a DOI.")) from error
    body, host = _get(
        DOI_URL.format(doi=quote(doi, safe="/")),
        first_host=DOI_HOST,
        accept=CSL_JSON,
        limit=DOI_BYTES,
        service="doi.org",
        json_only=True,
    )
    record = _json(body, _("doi.org's answer was not a record Postulo can read."))
    values = from_csl(record)
    if not values.get("title") and len(values) <= 1:
        raise LookupRefused(_("doi.org's answer was not a record Postulo can read."))
    return Found(service=AGENCIES.get(host, "doi.org"), values=values)


def from_orcid(orcid: str) -> tuple[list[dict], int]:
    """Ask ORCID for one record's public works. Raises `LookupRefused` with the reason."""
    orcid = valid_orcid(orcid)
    if not orcid:
        raise LookupRefused(_("That is not an ORCID."))
    body, _host = _get(
        ORCID_URL.format(orcid=orcid),
        first_host=ORCID_HOST,
        accept="application/json",
        limit=ORCID_BYTES,
        service="ORCID",
        json_only=False,
    )
    return works_from(_json(body, _("ORCID's answer was not a list of works.")))


def _json(body: bytes, refusal: str):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LookupRefused(refusal) from error


def _get(
    url: str, *, first_host: str, accept: str, limit: int, service: str, json_only: bool
) -> tuple[bytes, str]:
    """One GET, its redirects followed by hand, each https; the body and the host that gave it.

    By hand rather than by httpx, so that a redirect's body is never read and each hop is
    seen before it is made. The guard approves every address, the first and each redirect's.
    """
    deadline = http.deadline_in(SECONDS)
    current = url
    try:
        with (
            http.public_only_client(
                timeout=SECONDS, follow_redirects=False, headers={"Accept": accept}
            ) as client,
            http.until(deadline),
        ):
            for hop in range(MAX_REDIRECTS + 1):
                parts = urlsplit(current)
                if parts.scheme != "https" or (hop == 0 and parts.hostname != first_host):
                    raise LookupRefused(
                        _("%(service)s sent the request somewhere else.") % {"service": service}
                    )
                with client.stream(
                    "GET", current, headers={"Accept-Encoding": http.ACCEPT_ENCODING}
                ) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("Location", ""))
                        continue
                    _refuse_status(response.status_code, service)
                    kind = response.headers.get("Content-Type", "").lower()
                    if json_only and "json" not in kind:
                        raise LookupRefused(
                            _("%(service)s has no record in a form Postulo reads.")
                            % {"service": service}
                        )
                    body = http.read_body(response, limit=limit, deadline=deadline)
                    return body, parts.hostname or ""
            raise LookupRefused(_("%(service)s redirected too many times.") % {"service": service})
    except LookupRefused:
        raise
    except http.DestinationRefused as error:
        raise LookupRefused(
            _("%(service)s sent the request to an address Postulo does not dial.")
            % {"service": service}
        ) from error
    except http.BodyTooLarge as error:
        raise LookupRefused(
            _("The answer from %(service)s was larger than a record should be.")
            % {"service": service}
        ) from error
    except (http.BodyTooSlow, httpx.TimeoutException) as error:
        raise LookupRefused(
            _("%(service)s took too long to answer.") % {"service": service}
        ) from error
    except http.BodyRefused as error:
        raise LookupRefused(
            _("The answer from %(service)s could not be read.") % {"service": service}
        ) from error
    except httpx.HTTPError as error:
        raise LookupRefused(
            _("%(service)s could not be reached.") % {"service": service}
        ) from error


def _refuse_status(status: int, service: str) -> None:
    """Every answer but 200 is a refusal, and none is tried again."""
    if status == 200:
        return
    if status == 404:
        raise LookupRefused(_("%(service)s knows no such identifier.") % {"service": service})
    if status in (406, 415):
        raise LookupRefused(
            _("%(service)s has no record in a form Postulo reads.") % {"service": service}
        )
    if status in (429, 503):
        raise LookupRefused(
            _("%(service)s asked for fewer requests. Try again later.") % {"service": service}
        )
    raise LookupRefused(
        _("%(service)s answered %(status)s.") % {"service": service, "status": status}
    )
