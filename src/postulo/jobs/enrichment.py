"""A company's details read from open sources, when a person presses for it (#1256).

A company is entered by name, and more of it is written down somewhere a person could look:
its website's own markup, an open data set, a register. *Find details* on a company's page
asks the sources the person ticks, shows what each said and where it read it, and keeps only
what is ticked on the review. This module is the contract a source is written to, the
registry of the ones installed, and the one place that asks them. Never on a page load, a
save, an import, a merge or a schedule.

**Postulo makes every request; a source only describes it.** A source (`Source`) says what of
a company it starts from, which facts it may offer, where it may be asked and how the answers
are read. The requests go through the public-only client (rule 5 of the threat model) with
Postulo's user agent, a deadline and a body limit, to the hosts the source declared and
nowhere else -- or, for a source that reads the company's own website, to the host of the
address the person recorded. So a source installed from another package
(``postulo.company_enrichment``, an open group as ``postulo.register_adapters`` is) can add
somewhere to read, and cannot make the server dial anywhere its declaration does not name.

**A source is told only what it starts from.** The `Subject` it is handed holds the company's
website, its name, its country or one of its identifiers only where the source's
``starts_from`` names them: a source that reads a website never sees the name. Nothing about
the person asking is sent: no cookie, no referrer, no account.

**Each plugin of this kind is its own switch.** There is no feature in front of the kind, as
there is in front of the register adapters: a source is listed and asked only while its
plugin is on for the person (`plugins.policy`), and a press spends the person's allowance.

**What comes back is a stranger's text.** Every offered value is cleaned here: text
normalised and cut, an address held to ``http`` and ``https`` on a host name, a country only
if Postulo knows it, a NACE division only if the classification has it, an identifier only
if its scheme accepts it, and a field only if the source said it offers it. A value that does
not say where it was read is dropped. A source that raises is that source's line on the
review, never an error page and never another source's failure.

This module imports no model and translates nothing when it is imported, so a package that
imports the contract at module level loads wherever Postulo's plugin surface does. What the
review offers and how a ticked value is kept is `enrichment_offers`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.plugins import http

from . import register_lookup
from .register_lookup import Address

logger = logging.getLogger(__name__)

#: The kind of plugin that holds sources, and the setting that bounds the fixed-host ones.
KIND = "company-enrichment"
RATE_SETTING = "POSTULO_ENRICHMENT_RATE"

#: The facts a source may offer. A value for a field not in this list is dropped and logged.
FIELDS = (
    # how it is reached
    "website",
    "careers_url",
    "email",
    "phone",
    "social",
    # what it is called
    "name",
    "acronym",
    "identifier",
    # where it is, what it does, whether it still exists
    "address",
    "industry",
    "legal_state",
    # said, and kept nowhere yet: a company has no column for either
    "founded",
    "size",
    "logo",
)

#: What of a company a source may start from, and be handed.
STARTS = ("website", "name", "country", "wikidata", "register", "lei")
#: The ones that are an identifier of the company, by the scheme's key.
IDENTIFIER_STARTS = ("wikidata", "register", "lei")

#: How an answer is read: JSON from a fixed host, or a page through capture's own reader.
READS = ("json", "page")

#: One source, one press: its own addresses and one more round, and no more than this.
MAX_REQUESTS = 4
#: An answer about one company is some kilobytes. Two megabytes is a document nobody meant.
MAX_BYTES = 2 * 1024 * 1024
SECONDS = 15.0
MAX_REDIRECTS = 3
#: How many values one source may offer for one company.
MAX_VALUES = 40
#: How much of what came back an errand's row may hold until the review is answered, and
#: the key it is held under in the errand's outcome.
MAX_HELD_BYTES = 64 * 1024
HELD = "held"

MAX_ADDRESS = 500
MAX_ORIGIN = 120
MAX_CREDIT = 200
MAX_EMAIL = 254
MAX_PHONE = 40
#: The most people a company may be said to employ: more than any does.
MAX_HEADCOUNT = 10_000_000

_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,39}")
#: A year, a year and a month, or a day: what a founding date or an "as of" may be.
_WHEN = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


class Refused(Exception):
    """Why a source gave nothing, in a sentence a person can read."""


# ------------------------------------------------------------------- the contract
#
# What a source is, what it is handed and what it hands back. On the plugin surface as
# `EnrichmentSource`, `EnrichmentSubject`, `EnrichmentAnswer` and `OfferedValue`, so a
# package builds the same objects Postulo's own sources do. An address is a
# `RegisterAddress`, the one a register adapter returns.


@dataclass(frozen=True)
class Subject:
    """What a source is told about the company: only what its ``starts_from`` names.

    ``language`` is the language the page is being read in, as a BCP 47 tag, for a source
    whose service answers in one; a source that sends it says so in its ``sends``.
    """

    name: str = ""
    website: str = ""
    #: ISO 3166-1 alpha-2, upper case: the country of the company's primary address.
    country: str = ""
    #: The company's identifiers the source starts from, by scheme: ``{"wikidata": "Q95"}``.
    identifiers: Mapping[str, str] = field(default_factory=dict)
    language: str = ""


@dataclass(frozen=True)
class Answer:
    """One request's answer, as Postulo read it."""

    #: The address that answered, after any redirect.
    url: str
    status: int
    media_type: str = ""
    #: The parsed document of a source that ``reads="json"``, where the answer was a 200.
    json: Any = None
    #: The HTML of a source that ``reads="page"``, at most two megabytes of it.
    text: str = ""


@dataclass(frozen=True)
class Offered:
    """One value a source offers for one field, and where it read it.

    ``value`` is text for most fields; a `RegisterAddress` for ``address``; a
    ``(scheme, value)`` pair for ``identifier``; a NACE division's two digits for
    ``industry``; ``"active"``, ``"administration"`` or ``"dissolved"`` for ``legal_state``
    (with ``as_of`` the day it became so); a year, a year and month, or an ISO date for
    ``founded``; a ``(low, high)`` pair of head counts for ``size``, ``high`` being ``None``
    for "and more"; and the address of the image for ``logo``.

    ``origin`` says where on the source the value was read, in a few words -- a property's
    name, the element it was in -- and a value without one is dropped. ``url`` is the address
    a person opens to check it. ``licence``, ``licence_url`` and ``credit`` are for a value
    whose licence is not the source's own; left empty, the source's are shown.
    """

    field: str
    value: Any
    origin: str
    url: str = ""
    licence: str = ""
    licence_url: str = ""
    credit: str = ""
    #: BCP 47, for a name.
    language: str = ""
    #: The date the source says it was true: a head count "as of 2023".
    as_of: str = ""


@dataclass(frozen=True)
class Source:
    """One place a company's details can be read from (#1256).

    ``starts_from`` names what of a company the source needs (from `STARTS`); a company that
    lacks one is not asked. ``offers`` names the fields it may return (from `FIELDS`);
    anything else it returns is dropped. ``addresses(subject)`` gives the addresses to ask,
    in order, and ``read(answers, subject)`` turns the answers into `Offered` values.
    ``follow(answers, subject)``, where given, may name more addresses once the first
    answers are in -- one more round, and never more than `MAX_REQUESTS` in all.

    Exactly one of ``hosts`` and ``own_site`` says where it may be asked. With ``hosts``
    every address is ``https`` on one of them, and a redirect is followed only among them.
    With ``own_site`` the source reads the company's own website: every address is on the
    host of the website the person recorded (so ``starts_from`` has to name ``website``),
    and the page is read as a capture's is, ``robots.txt`` honoured.

    ``reads`` is ``"json"`` or ``"page"``. ``sends`` says in words what the request carries
    and ``source`` who is named beside what came back; ``licence`` and ``licence_url`` are
    the terms its data are under, shown with every value that names none of its own.
    """

    key: str
    label: Any
    starts_from: tuple[str, ...]
    offers: tuple[str, ...]
    addresses: Callable[[Subject], Sequence[str]]
    read: Callable[[Sequence[Answer], Subject], Sequence[Offered]]
    sends: Any
    source: Any
    hosts: tuple[str, ...] = ()
    own_site: bool = False
    reads: str = "json"
    follow: Callable[[Sequence[Answer], Subject], Sequence[str]] | None = None
    licence: str = ""
    licence_url: str = ""
    #: Extra request headers the service's documentation asks for (a versioned ``Accept``).
    headers: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Result:
    """What asking one source came to: its cleaned values, or why there are none."""

    source: Source
    values: tuple[Offered, ...] = ()
    error: str = ""


# ------------------------------------------------------------------ the registry


def _names(values, among) -> bool:
    return (
        isinstance(values, tuple)
        and bool(values)
        and all(isinstance(value, str) and value in among for value in values)
        and len(set(values)) == len(values)
    )


def problems(source) -> list[str]:
    """What is wrong with a source, in words for its author; empty where it can be asked.

    Checked once, here, for the registry and for the published contract check alike.
    """
    if not isinstance(source, Source):
        return [f"{source!r} is not an EnrichmentSource"]
    found: list[str] = []
    if not isinstance(source.key, str) or not _KEY.fullmatch(source.key):
        found.append("key must be lower-case letters, digits and hyphens, at most 40")
    if not _names(source.starts_from, STARTS):
        found.append(f"starts_from must be a tuple of one or more of {STARTS}")
    if not _names(source.offers, FIELDS):
        found.append(f"offers must be a tuple of one or more of {FIELDS}")
    if not callable(source.addresses) or not callable(source.read):
        found.append("addresses and read must be callable")
    if source.follow is not None and not callable(source.follow):
        found.append("follow must be callable, or left out")
    if source.reads not in READS:
        found.append(f"reads must be one of {READS}")
    hosts = source.hosts if isinstance(source.hosts, tuple) else None
    if hosts is None or not all(isinstance(host, str) and _HOST.fullmatch(host) for host in hosts):
        found.append("hosts must be a tuple of host names in lower case")
    elif bool(hosts) == bool(source.own_site):
        found.append("exactly one of hosts and own_site says where it may be asked")
    elif source.own_site and (
        not isinstance(source.starts_from, tuple) or "website" not in source.starts_from
    ):
        found.append("a source that reads the company's own site starts from its website")
    try:
        headers_fit = all(
            isinstance(name, str) and isinstance(value, str) and _HEADER.fullmatch(name)
            for name, value in source.headers
        )
    except (TypeError, ValueError):
        headers_fit = False
    if not headers_fit:
        found.append("headers must be pairs of a header's name and its value")
    return found


def _usable(source) -> bool:
    """Whether something a plugin handed over can be asked at all."""
    try:
        return not problems(source)
    except Exception:  # pragma: no cover - a hostile object
        return False


_merged: tuple[list, list[tuple[str, Source]]] | None = None


def registered() -> list[tuple[str, Source]]:
    """Every usable source this instance knows, each with the name of the plugin it is from.

    **The sources Postulo ships are read first**, then the others in the order they were
    installed; the first to claim a key keeps it, and a second is logged and left out,
    which is the rule link services and register adapters follow (#305). A source that
    cannot be used -- a key of the wrong shape, no host and no site, a field nobody knows --
    is logged and left out too. Whether a plugin is on is not asked here: `sources` does.
    """
    global _merged

    from postulo.plugins import registry as plugin_registry

    installed = plugin_registry.plugins(KIND)
    if (
        _merged is not None
        and len(_merged[0]) == len(installed)
        and all(was is now for was, now in zip(_merged[0], installed, strict=True))
    ):
        return list(_merged[1])
    shipped = tuple(plugin_registry.builtins().get(KIND, ()))
    found: list[tuple[str, Source]] = []
    keys: set[str] = set()
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        name = str(getattr(plugin, "name", "") or "")
        try:
            offered = list(getattr(plugin, "sources", ()) or ())
        except Exception:
            logger.exception("Plugin %r's enrichment sources could not be read", plugin)
            continue
        for source in offered:
            if not _usable(source):
                logger.warning(
                    "Plugin %r offered an enrichment source that cannot be used: %r",
                    name or plugin,
                    source,
                )
            elif source.key in keys:
                logger.warning(
                    "Enrichment source %r was offered twice; the first one keeps it", source.key
                )
            else:
                keys.add(source.key)
                found.append((name, source))
    _merged = (installed, found)
    return list(found)


def sources(person=None) -> dict[str, Source]:
    """The sources that may be asked for ``person``, by key: those whose plugin is on.

    With no person, the instance's own decision: what a source is for somebody nobody has
    decided otherwise about.
    """
    from postulo.plugins.policy import decide

    return {source.key: source for name, source in registered() if name and decide(name, person).on}


def find(key: str, person=None) -> Source | None:
    """One source by key, or nothing for a key that is not installed or not on."""
    return sources(person).get(key or "")


# ------------------------------------------------------- what a company starts with


def _country_of(company) -> str:
    rows = sorted(company.postal_addresses.all(), key=lambda row: (not row.is_primary, row.pk))
    return next((row.country for row in rows if row.country), "")


def _has(company) -> dict[str, str]:
    """What of ``company`` a source could start from, by the names in `STARTS`."""
    found = {
        "website": (company.website or "").strip(),
        "name": (company.name or "").strip(),
        "country": _country_of(company),
    }
    for row in company.identifiers.all():
        if row.scheme in IDENTIFIER_STARTS and row.value:
            found.setdefault(row.scheme, row.value)
    return found


def missing(source: Source, company) -> list[str]:
    """What ``source`` starts from that ``company`` lacks; empty where it can be asked."""
    has = _has(company)
    return [start for start in source.starts_from if not has.get(start)]


def subject_for(company, source: Source) -> Subject:
    """The company as ``source`` is told of it: what it starts from, and nothing else."""
    from postulo.core import languages

    has = _has(company)
    starts = set(source.starts_from)
    return Subject(
        name=has["name"] if "name" in starts else "",
        website=has["website"] if "website" in starts else "",
        country=has["country"] if "country" in starts else "",
        identifiers={
            scheme: has[scheme]
            for scheme in IDENTIFIER_STARTS
            if scheme in starts and has.get(scheme)
        },
        language=languages.current(),
    )


def usable_for(company, person=None, fields: Sequence[str] = ()) -> list[Source]:
    """The sources on for ``person`` that ``company`` has enough for, offering any of
    ``fields`` where some are named."""
    return [
        source
        for source in sources(person).values()
        if (not fields or set(fields) & set(source.offers)) and not missing(source, company)
    ]


def host_of(address: str) -> str:
    """The host an address names, lower-cased and in its ASCII form; empty where it has none."""
    try:
        host = (urlsplit((address or "").strip()).hostname or "").lower().rstrip(".")
        return host.encode("idna").decode("ascii") if host else ""
    except (ValueError, UnicodeError):
        return ""


def asked_on(source: Source, company) -> list[str]:
    """The hosts a press would reach for this company: the source's own, or its website's."""
    if source.hosts:
        return list(source.hosts)
    host = host_of(company.website)
    return [host] if host else []


# ------------------------------------------------------------------ the requests


def user_agent() -> str:
    """Postulo's, naming where its source and its makers are, as the Wikidata request does."""
    from . import wikidata

    return wikidata.user_agent()


def ask(company, keys: Sequence[str] | None = None, *, person=None, fields=()) -> list[Result]:
    """Ask the sources named by ``keys`` about ``company``, and say what each came to.

    Only sources that are on for ``person``, that the company has enough for and, where
    ``fields`` are named, that offer one of them; ``keys=None`` is every such source. A
    source that cannot be reached, answers nonsense or raises is a `Result` with its
    ``error`` and no values, and the next one is asked all the same.
    """
    found = sources(person)
    wanted = list(found) if keys is None else [key for key in keys if key in found]
    results: list[Result] = []
    for key in dict.fromkeys(wanted):
        source = found[key]
        if fields and not set(fields) & set(source.offers):
            continue
        if missing(source, company):
            continue
        try:
            values = ask_source(source, subject_for(company, source))
        except Refused as refusal:
            results.append(Result(source, error=str(refusal)))
            continue
        if fields:
            values = [value for value in values if value.field in fields]
        results.append(Result(source, values=tuple(values)))
    return results


def _named(source: Source) -> dict:
    return {"source": str(source.label)}


def ask_source(source: Source, subject: Subject) -> list[Offered]:
    """Make ``source``'s requests for one company and read what came back, cleaned.

    Raises `Refused` with the reason. The addresses are the source's; the requests are
    made here, each held to where the source may be asked.
    """
    allowed = set(source.hosts) if source.hosts else {host_of(subject.website)} - {""}
    try:
        first = [str(address) for address in source.addresses(subject) or ()]
    except Exception as error:
        logger.exception("Enrichment source %r could not build its addresses", source.key)
        raise Refused(
            _("%(source)s cannot be asked about this company.") % _named(source)
        ) from error
    if not first:
        raise Refused(_("%(source)s cannot be asked about this company.") % _named(source))
    _within_the_allowance(source, len(first))
    for address in first:
        _hold(source, address, allowed)
    answers = [_fetch(source, address, allowed) for address in first]
    if source.follow is not None:
        try:
            more = [str(address) for address in source.follow(tuple(answers), subject) or ()]
        except Exception as error:
            logger.exception("Enrichment source %r could not follow its answers", source.key)
            raise Refused(_("%(source)s's answer could not be read.") % _named(source)) from error
        _within_the_allowance(source, len(first) + len(more))
        for address in more:
            _hold(source, address, allowed)
        answers += [_fetch(source, address, allowed) for address in more]
    try:
        offered = list(source.read(tuple(answers), subject) or ())
    except Exception as error:
        logger.exception("Enrichment source %r could not read its answers", source.key)
        raise Refused(_("%(source)s's answer could not be read.") % _named(source)) from error
    return cleaned_all(offered, source)


def _within_the_allowance(source: Source, requests: int) -> None:
    if requests > MAX_REQUESTS:
        logger.warning(
            "Enrichment source %r asked for %d requests in one press", source.key, requests
        )
        raise Refused(
            _("%(source)s asked for more requests than one press allows.") % _named(source)
        )


def _hold(source: Source, address: str, allowed: set[str], *, moved: bool = False) -> None:
    """Refuse an address that is not where ``source`` may be asked."""
    try:
        scheme = urlsplit(address).scheme
    except ValueError:
        scheme = ""
    schemes = ("https",) if source.hosts else ("http", "https")
    if scheme in schemes and host_of(address) in allowed:
        return
    if moved:
        raise Refused(_("%(source)s sent the request somewhere else.") % _named(source))
    logger.warning("Enrichment source %r asked for %s, outside its hosts", source.key, address)
    raise Refused(_("%(source)s asked for an address it did not declare.") % _named(source))


def _fetch(source: Source, address: str, allowed: set[str]) -> Answer:
    return (
        _page(source, address, allowed)
        if source.reads == "page"
        else _json(source, address, allowed)
    )


def _page(source: Source, address: str, allowed: set[str]) -> Answer:
    """One page, read as a capture's is: public addresses only, every redirect revalidated,
    ``robots.txt`` honoured, two megabytes and thirty seconds at most (`fetching`)."""
    from postulo.plugins import fetching
    from postulo.plugins.base import CaptureError

    try:
        page = fetching.fetch_page(address)
    except CaptureError as error:
        raise Refused(str(error)) from error
    if source.own_site:
        # A site that sends its visitors to another name of its own (the bare domain to
        # ``www.``) has said where it lives: a second request may go there too.
        allowed.add(host_of(page.url))
    else:
        _hold(source, page.url, allowed, moved=True)
    return Answer(url=page.url, status=200, media_type="text/html", text=page.html)


def _json(source: Source, address: str, allowed: set[str]) -> Answer:
    """One JSON document from one of the source's hosts, or the status it answered with."""
    url, status, media_type, body = get(source, address, allowed)
    document = None
    if status == 200:
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise Refused(_("%(source)s's answer could not be read.") % _named(source)) from error
    return Answer(
        url=url,
        status=status,
        media_type=register_lookup.text(media_type.split(";")[0], 100).lower(),
        json=document,
    )


def get(source: Source, address: str, allowed: set[str]) -> tuple[str, int, str, bytes]:
    """One request for ``source``: the address that answered, its status, its media type and,
    for a 200, its body. Every hop is held to ``allowed``; raises `Refused` with the reason."""
    named = _named(source)
    deadline = http.deadline_in(SECONDS)
    headers = {**dict(source.headers), "User-Agent": user_agent()}
    headers.setdefault("Accept", "application/json")
    current = address
    body = b""
    try:
        with (
            http.public_only_client(
                timeout=SECONDS, follow_redirects=False, headers=headers
            ) as client,
            http.until(deadline),
        ):
            for hop in range(MAX_REDIRECTS + 1):
                _hold(source, current, allowed, moved=hop > 0)
                with client.stream(
                    "GET", current, headers={"Accept-Encoding": http.ACCEPT_ENCODING}
                ) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("Location", ""))
                        continue
                    if response.status_code == 429:
                        raise Refused(
                            _("%(source)s asked for fewer requests. Try again later.") % named
                        )
                    status = response.status_code
                    media_type = response.headers.get("Content-Type", "")
                    if status == 200:
                        body = http.read_body(response, limit=MAX_BYTES, deadline=deadline)
                    break
            else:
                raise Refused(_("%(source)s redirected too many times.") % named)
    except Refused:
        raise
    except http.DestinationRefused as error:
        raise Refused(str(error)) from error
    except http.BodyTooLarge as error:
        raise Refused(
            _("%(source)s's answer was larger than one company's should be.") % named
        ) from error
    except (http.BodyTooSlow, httpx.TimeoutException) as error:
        raise Refused(
            ngettext(
                "%(source)s took longer than %(seconds)s second to answer.",
                "%(source)s took longer than %(seconds)s seconds to answer.",
                int(SECONDS),
            )
            % {**named, "seconds": int(SECONDS)}
        ) from error
    except http.BodyInAnotherCoding as error:
        raise Refused(
            _(
                "%(source)s sent its answer with the content encoding “%(coding)s”, which "
                "Postulo does not unpack."
            )
            % {**named, "coding": error.coding[:40]}
        ) from error
    except http.BodyRefused as error:
        raise Refused(str(error)) from error
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        # A redirect to something that is not an address is one of these too (#607).
        raise Refused(_("%(source)s could not be reached.") % named) from error
    return current, status, media_type, body


# ------------------------------------------------------------- a stranger's text


def web_address(value) -> str:
    """An ``http`` or ``https`` address on a host name, as plain text; empty for anything else."""
    address = register_lookup.text(value, MAX_ADDRESS + 1)
    if not address or len(address) > MAX_ADDRESS or " " in address:
        return ""
    try:
        parts = urlsplit(address)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host_of(address):
        return ""
    return address


def when(value) -> str:
    """A year, a year and a month or a day that exists, as ISO text; empty for anything else."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    found = _WHEN.fullmatch(register_lookup.text(value, 10))
    if not found:
        return ""
    year, month, day = (int(part) if part else None for part in found.groups())
    try:
        date(year, month or 1, day or 1)
    except ValueError:
        return ""
    return found[0] if 1000 <= year <= date.today().year + 1 else ""


def _email(value) -> str:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    address = register_lookup.text(value, MAX_EMAIL + 8)
    if address.lower().startswith("mailto:"):
        address = address[7:].split("?")[0]
    address = address.strip()
    try:
        validate_email(address)
    except ValidationError:
        return ""
    return address if len(address) <= MAX_EMAIL else ""


def _identifier(value) -> tuple[str, str] | None:
    from django.core.exceptions import ValidationError

    from . import identifiers

    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    scheme = register_lookup.text(value[0], 40)
    try:
        return scheme, identifiers.clean(scheme, register_lookup.text(value[1], 200))
    except (ValidationError, KeyError, ValueError):
        return None


def _address(value) -> Address | None:
    if isinstance(value, Mapping):
        try:
            value = Address(
                **{name: value.get(name) or "" for name in Address.__dataclass_fields__}
            )
        except TypeError:
            return None
    if not isinstance(value, Address):
        return None
    found = Address(
        street=register_lookup.text(value.street, register_lookup.MAX_STREET),
        postcode=register_lookup.text(value.postcode, register_lookup.MAX_POSTCODE),
        municipality=register_lookup.text(value.municipality, register_lookup.MAX_PLACE),
        region=register_lookup.text(value.region, register_lookup.MAX_PLACE),
        country=register_lookup._country(value.country),
    )
    return None if found.is_empty() else found


def _headcount(value) -> tuple[int, int | None] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    low, high = value
    if not _whole(low) or not (high is None or _whole(high)):
        return None
    if not 0 <= low <= MAX_HEADCOUNT:
        return None
    if high is not None and not low <= high <= MAX_HEADCOUNT:
        return None
    return low, high


def _whole(number) -> bool:
    return isinstance(number, int) and not isinstance(number, bool)


def _value(name: str, value):
    """``value`` as ``name`` may hold it, or ``None`` where it may not."""
    if name in ("website", "careers_url", "social", "logo"):
        return web_address(value) or None
    if name == "email":
        return _email(value) or None
    if name == "phone":
        return register_lookup.text(value, MAX_PHONE) or None
    if name in ("name", "acronym"):
        return register_lookup.text(value, register_lookup.MAX_NAME) or None
    if name == "identifier":
        return _identifier(value)
    if name == "address":
        return _address(value)
    if name == "industry":
        return register_lookup._division(value) or None
    if name == "legal_state":
        return value if value in register_lookup.STATES else None
    if name == "founded":
        return when(value) or None
    if name == "size":
        return _headcount(value)
    return None


def cleaned(offered, source: Source) -> Offered | None:
    """One offered value as Postulo will hold it, or nothing where it may not be held.

    Dropped: something that is not an `Offered`, a field the source did not say it offers,
    a value its field cannot hold, and a value that does not say where it was read.
    """
    from postulo.core import languages

    if not isinstance(offered, Offered):
        return None
    if offered.field not in FIELDS or offered.field not in source.offers:
        return None
    value = _value(offered.field, offered.value)
    origin = register_lookup.text(offered.origin, MAX_ORIGIN)
    if value is None or not origin:
        return None
    language = register_lookup.text(offered.language, 35)
    return Offered(
        field=offered.field,
        value=value,
        origin=origin,
        url=web_address(offered.url),
        licence=register_lookup.text(offered.licence, MAX_ORIGIN),
        licence_url=web_address(offered.licence_url),
        credit=register_lookup.text(offered.credit, MAX_CREDIT),
        language=languages.tag(language) if languages.well_formed(language) else "",
        as_of=when(offered.as_of),
    )


def cleaned_all(offered, source: Source) -> list[Offered]:
    """What a source returned, each value cleaned, without repeats, at most `MAX_VALUES`."""
    found: list[Offered] = []
    dropped = 0
    for one in offered if isinstance(offered, (list, tuple)) else ():
        kept = cleaned(one, source)
        if kept is None:
            dropped += 1
        elif kept not in found:
            found.append(kept)
    if dropped:
        logger.warning(
            "Enrichment source %r returned %d value(s) that could not be held", source.key, dropped
        )
    return found[:MAX_VALUES]


# ----------------------------------------------------------- held until the review


def _plain(offered: Offered) -> dict:
    row = asdict(offered)
    if isinstance(row["value"], tuple):
        row["value"] = list(row["value"])
    return row


def held(results: Sequence[Result], *, at: str = "") -> dict:
    """What came back as an errand's row holds it until the review is answered: plain JSON,
    and no more than `MAX_HELD_BYTES` of it."""
    rows = [
        {
            "key": result.source.key,
            "error": result.error[:500],
            "values": [_plain(value) for value in result.values],
        }
        for result in results
    ]
    found = {"at": at, "sources": rows}
    while len(json.dumps(found)) > MAX_HELD_BYTES:
        longest = max(rows, key=lambda row: len(row["values"]))
        if not longest["values"]:
            break
        longest["values"].pop()
    return found


def from_held(data, person=None) -> list[Result]:
    """The results back out of an errand's row, cleaned again: the row is not a trusted store.

    A source that has been removed or switched off since the press is left out with what it
    said, as it would not be asked now.
    """
    found = sources(person)
    results: list[Result] = []
    rows = data.get("sources") if isinstance(data, Mapping) else None
    for row in rows if isinstance(rows, list) else ():
        source = found.get(row.get("key") or "") if isinstance(row, Mapping) else None
        if source is None:
            continue
        values = []
        for one in row.get("values") if isinstance(row.get("values"), list) else ():
            if not isinstance(one, Mapping):
                continue
            try:
                values.append(Offered(**{name: one.get(name) for name in _OFFERED_FIELDS}))
            except TypeError:
                continue
        results.append(
            Result(
                source,
                values=tuple(cleaned_all(values, source)),
                error=register_lookup.text(row.get("error"), 500),
            )
        )
    return results


_OFFERED_FIELDS = tuple(Offered.__dataclass_fields__)


# --------------------------------------------------- what the instance may call


def offered_anywhere(plugin: str) -> bool:
    """Whether ``plugin`` is on for the instance, or for any account: the record of
    processing lists its sources then, because somebody's press can reach them."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.policy import decide

    if decide(plugin, None).on:
        return True
    return PluginPolicy.objects.filter(
        plugin=plugin,
        person__isnull=False,
        state__in=(PluginPolicy.State.AVAILABLE, PluginPolicy.State.FORCED_ON),
    ).exists()


def services() -> list[dict]:
    """Every source somebody's press can reach, for the record of processing (#297, #1256):
    who, on which hosts, what is sent, and under what licence. A source that reads a
    company's own website names no host of its own: it asks the site the person recorded."""
    found = []
    for plugin, source in registered():
        if not plugin or not offered_anywhere(plugin):
            continue
        found.append(
            {
                "key": source.key,
                "label": str(source.label),
                "source": str(source.source),
                "hosts": list(source.hosts),
                "sends": str(source.sends),
                "licence": source.licence,
                "licence_url": source.licence_url,
            }
        )
    return found
