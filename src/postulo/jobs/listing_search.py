"""Searching a job service by criteria, when a person presses for it (#1253).

A `source` plugin reads one advert from one address. Nothing asked a service *which adverts
it has* for some words and a place, and several public employment services answer exactly
that, openly and under a licence that lets the answer be kept. This is the one place that
asks. Never on a page load, a save, an import or a schedule: a person fills in the search
page, presses *Search*, sees what came back, and presses *Review* on the one they want, which
makes a capture that goes through the review every capture goes through.

**Postulo makes the request; a search only describes it.** A `ListingSearch` names a service,
the hosts it answers on, which of the criteria it honours, the address to ask and the
parameters to send for a search and for one advert, and how to read the JSON that comes back.
It never writes a query string: it hands back an address and a mapping, and Postulo encodes
the mapping, so what a person typed reaches a parameter's value and nothing else. The request
goes through the public-only client (rule 5 of the threat model) with Postulo's user agent, a
deadline, a body limit and no redirects, to one of the search's own hosts over https. So a
search installed from another package (`postulo.listing_searches`, the open group beside
``postulo.register_adapters``) can add a service, and cannot make the server dial anywhere its
declaration does not name.

**What it sends is what was searched for, and nothing else.** The words, the place and the
filters, and for a review the service's own number for the advert: no name, no account, no
cookie, no referrer. The service learns that somebody on this instance searched for that,
which is why the whole of it sits behind a feature that is off until an administrator switches
it on (`listing-search`), a switch for each service under it, a per-account allowance
(``POSTULO_LISTING_SEARCH_RATE``) and a press.

**What comes back is a stranger's text, and none of it is stored.** Every hit is a
`JobPostingData`, validated as a capture's is, with its strings normalised, stripped of control
and bidirectional-formatting characters and cut to the column they might go in, and its
vocabularies held to Postulo's own. The results are drawn and forgotten. *Review* asks the
service again for that one advert by its number and builds the capture from that answer, never
from anything the page sent back.

**Nothing here needs Django set up to be imported**: no model and no translated string at
module level, so that a package installed beside Postulo can import `ListingSearch` from the
plugin surface while its entry points are being checked (#1299).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from django.utils.translation import gettext as _

from postulo.plugins import http
from postulo.plugins.base import JobPostingData

logger = logging.getLogger(__name__)

#: The plugin that decides whether any of this is offered, and the setting that bounds it.
FEATURE = "listing-search"
RATE_SETTING = "POSTULO_LISTING_SEARCH_RATE"

#: The criteria a search may honour, as `ListingSearch.supports` names them: the words, the
#: place, *remote only*, and *published in the last N days*.
WORDS = "words"
PLACE = "place"
REMOTE = "remote"
SINCE = "since"
FILTERS = (WORDS, PLACE, REMOTE, SINCE)

#: How many adverts one page of results holds, and how far a person may page. Twenty a press
#: is a screen; a hundred pages is JobTech's own ceiling of two thousand, and far past what
#: anybody reads.
PAGE_SIZE = 20
MAX_PAGE = 100

#: A page of twenty adverts with their text is some hundred kilobytes; one advert is ten.
#: Two megabytes is an answer nobody meant.
MAX_BYTES = 2 * 1024 * 1024
SECONDS = 15.0

#: How long each kind of text may be: what was typed, and what the columns hold.
MAX_WORDS = 200
MAX_PLACE = 120
MAX_SINCE_DAYS = 365
MAX_TITLE = 250
MAX_COMPANY = 200
MAX_LOCATION = 200
MAX_SOURCE = 120
MAX_DESCRIPTION = 40_000
#: A query string longer than this is not a search anybody typed.
MAX_QUERY = 2000
MAX_VALUE = 300

#: A sub-plugin's id has this shape and length (`SUB_PLUGIN_ID`), and each service is one.
_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,29}")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_COUNTRY = re.compile(r"[A-Z]{2}")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*")
#: A service's own number for an advert, as it goes back to the service in a path: letters,
#: digits and the four marks a path segment takes unescaped, never a slash or a percent sign.
_ADVERT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,79}")
_PARAMETER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}")
_CURRENCY = re.compile(r"[A-Z]{3}")

#: What a posting may say of where and how the work is done, in Postulo's own words
#: (`RemoteType`, `EmploymentType` and `SalaryPeriod`, which a model module holds and this
#: one may not import).
REMOTE_TYPES = ("onsite", "hybrid", "remote")
EMPLOYMENT_TYPES = (
    "full_time",
    "part_time",
    "contract",
    "freelance",
    "internship",
    "apprenticeship",
)
SALARY_PERIODS = ("year", "month", "day", "hour")


class SearchRefused(Exception):
    """Why nothing came back, in a sentence a person can read."""


# ------------------------------------------------------------------- the contract
#
# What a search is, what it is handed and what it hands back. On the plugin surface as
# `ListingSearch`, `SearchCriteria`, `SearchHit` and `SearchResults`, so a package for another
# service builds the same objects Postulo's own searches do.


@dataclass(frozen=True)
class Criteria:
    """What a person searched for, as the search page collected it.

    A search is handed only what it said it honours (`ListingSearch.supports`): the rest
    arrives empty. ``page`` counts from one, ``size`` is how many adverts a page holds and
    ``offset`` how many come before this page.
    """

    words: str = ""
    place: str = ""
    remote: bool = False
    #: Published in the last so many days, or ``None`` for any time.
    since_days: int | None = None
    page: int = 1
    size: int = PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    def is_empty(self) -> bool:
        return not (self.words or self.place or self.remote or self.since_days)


@dataclass(frozen=True)
class Hit:
    """One advert a search found: the service's own number for it, and the posting."""

    #: What `ListingSearch.one` is handed to ask for this advert again.
    key: str
    posting: JobPostingData


@dataclass(frozen=True)
class Results:
    """One page of what a search found.

    ``total`` is how many adverts the service says match, where it says, and ``more``
    whether there is a page after this one.
    """

    hits: tuple[Hit, ...] = ()
    total: int | None = None
    more: bool = False


@dataclass(frozen=True)
class ListingSearch:
    """How one job service is searched, and how one of its adverts is asked for (#1253).

    ``countries`` are the ISO 3166-1 codes of where its adverts are, two capitals each, and
    ``languages`` the BCP 47 tags they are written in; both may be empty. ``hosts`` are the
    names the service answers on, and every request goes to one of them over ``https``.
    ``supports`` says which of `FILTERS` the service honours; the search page draws no other.

    ``request(criteria)`` gives the address to ask and the parameters to send, and
    ``one(key)`` the same for one advert. The address carries no query of its own and does
    not depend on what was typed: the parameters are a mapping of names to a string, a
    number, a truth value or a sequence of strings, and Postulo encodes them.
    ``read(document, criteria)`` turns the JSON that came back into `Results`, and
    ``read_one(document, key)`` into one `JobPostingData`, or ``None`` for "no such advert".

    ``source`` is the name written into a listing's *found via*, and ``licence``,
    ``licence_url`` and ``terms_url`` what the service lets its adverts be used under, shown
    beside the results. ``headers`` adds what the service's documentation asks for.
    """

    key: str
    label: Any
    countries: tuple[str, ...]
    languages: tuple[str, ...]
    hosts: tuple[str, ...]
    supports: frozenset[str]
    request: Callable[[Criteria], tuple[str, Mapping[str, Any]]]
    read: Callable[[Any, Criteria], Results]
    one: Callable[[str], tuple[str, Mapping[str, Any]]]
    read_one: Callable[[Any, str], JobPostingData | None]
    source: Any
    licence: str = ""
    licence_url: str = ""
    terms_url: str = ""
    headers: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ------------------------------------------------------------------ the registry


def _usable(search) -> bool:
    """Whether something a plugin handed over can be asked at all. Checked once, here."""
    return (
        isinstance(search, ListingSearch)
        and isinstance(search.key, str)
        and bool(_KEY.fullmatch(search.key))
        and isinstance(search.countries, tuple)
        and all(isinstance(code, str) and _COUNTRY.fullmatch(code) for code in search.countries)
        and isinstance(search.languages, tuple)
        and all(isinstance(tag, str) and _LANGUAGE.fullmatch(tag) for tag in search.languages)
        and isinstance(search.hosts, tuple)
        and bool(search.hosts)
        and all(isinstance(host, str) and _HOST.fullmatch(host) for host in search.hosts)
        and isinstance(search.supports, frozenset)
        and bool(search.supports)
        and search.supports <= set(FILTERS)
        and all(callable(call) for call in (search.request, search.read))
        and all(callable(call) for call in (search.one, search.read_one))
        and isinstance(search.headers, tuple)
        and all(
            isinstance(pair, tuple)
            and len(pair) == 2
            and all(isinstance(part, str) and part.isprintable() for part in pair)
            and bool(_PARAMETER.fullmatch(pair[0]))
            for pair in search.headers
        )
    )


_merged: tuple[list, dict[str, ListingSearch]] | None = None


def searches() -> dict[str, ListingSearch]:
    """Every job service this instance knows how to search, by key, from every registry.

    **The registry Postulo ships is read first**, so a key it holds stays its own: a package
    installed beside it adds services and cannot quietly send a search meant for one of
    Postulo's somewhere else. Between two packages the first to claim a key keeps it, and the
    second is logged and left out, which is the rule the company registers follow (#1021).
    """
    global _merged

    from postulo.plugins import registry as plugin_registry

    installed = plugin_registry.plugins("listing-search")
    if (
        _merged is not None
        and len(_merged[0]) == len(installed)
        and all(was is now for was, now in zip(_merged[0], installed, strict=True))
    ):
        return dict(_merged[1])
    shipped = tuple(plugin_registry.builtins().get("listing-search", ()))
    found: dict[str, ListingSearch] = {}
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        try:
            offered = list(getattr(plugin, "searches", ()) or ())
        except Exception:
            logger.exception("Plugin %r's listing searches could not be read", plugin)
            continue
        for search in offered:
            if not _usable(search):
                logger.warning(
                    "Plugin %r offered a listing search that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    search,
                )
            elif search.key in found:
                logger.warning(
                    "Listing search %r was offered under a key already taken; the first one "
                    "keeps it",
                    search.key,
                )
            else:
                found[search.key] = search
    _merged = (installed, found)
    return dict(found)


def find(key: str) -> ListingSearch | None:
    """One search by key, or nothing for a key no installed registry knows any more."""
    return searches().get(key or "")


def switch_of(key: str) -> str:
    """The name the policy holds for one service's own switch: the feature's sub-plugin."""
    return f"{FEATURE}:{key}"


def offered_to(person) -> list[ListingSearch]:
    """The services ``person`` may search: the feature is on for them, and so is the service.

    Both are asked, because a service whose switch nobody has declared yet answers as on:
    the feature is the door, and each service's switch closes one room behind it.
    """
    from postulo.plugins.policy import decide

    if not decide(FEATURE, person).on:
        return []
    return [search for key, search in searches().items() if decide(switch_of(key), person).on]


# ------------------------------------------------------------------ the request


def user_agent() -> str:
    """Postulo's, naming where its source and its makers are, as the register lookup does."""
    from . import wikidata

    return wikidata.user_agent()


def honoured(search: ListingSearch, criteria: Criteria) -> Criteria:
    """``criteria`` as ``search`` is handed it: tidied, bounded, and only what it honours."""
    days = criteria.since_days if isinstance(criteria.since_days, int) else None
    page = criteria.page if isinstance(criteria.page, int) else 1
    return Criteria(
        words=text(criteria.words, MAX_WORDS) if WORDS in search.supports else "",
        place=text(criteria.place, MAX_PLACE) if PLACE in search.supports else "",
        remote=bool(criteria.remote) and REMOTE in search.supports,
        since_days=(
            min(days, MAX_SINCE_DAYS) if days and days > 0 and SINCE in search.supports else None
        ),
        page=min(max(page, 1), MAX_PAGE),
        size=PAGE_SIZE,
    )


def left_out(search: ListingSearch, criteria: Criteria) -> list[str]:
    """Which of the criteria a person gave that ``search`` does not honour, as `FILTERS`."""
    given = {
        WORDS: bool(criteria.words),
        PLACE: bool(criteria.place),
        REMOTE: bool(criteria.remote),
        SINCE: bool(criteria.since_days),
    }
    return [name for name in FILTERS if given[name] and name not in search.supports]


def search(service: ListingSearch, criteria: Criteria) -> Results:
    """Ask ``service`` for one page of adverts. Raises `SearchRefused` with the reason."""
    criteria = honoured(service, criteria)
    if criteria.is_empty():
        raise SearchRefused(_("Say what to search for."))
    try:
        built = service.request(criteria)
    except Exception as error:
        logger.exception("Listing search %r could not describe its request", service.key)
        raise SearchRefused(_("That search cannot be asked of this service.")) from error
    document = _get(service, _address(service, built))
    if document is None:
        raise SearchRefused(
            _("%(service)s has nothing at the address it was asked at.")
            % {"service": str(service.label)}
        )
    try:
        found = service.read(document, criteria)
    except Exception as error:
        logger.exception("Listing search %r could not read its answer", service.key)
        raise SearchRefused(_("The service's answer could not be read.")) from error
    if not isinstance(found, Results):
        raise SearchRefused(_("The service's answer could not be read."))
    hits = []
    for hit in list(found.hits)[:PAGE_SIZE]:
        if not isinstance(hit, Hit) or not _is_advert(hit.key):
            continue
        try:
            hits.append(Hit(key=hit.key, posting=cleaned(service, hit.posting)))
        except (TypeError, ValueError):
            # One advert that cannot be shown costs that advert and not the page.
            logger.debug("Listing search %r gave a hit that cannot be shown", service.key)
    total = found.total if isinstance(found.total, int) and found.total >= 0 else None
    return Results(
        hits=tuple(hits), total=total, more=bool(found.more) and criteria.page < MAX_PAGE
    )


def advert(service: ListingSearch, key: str) -> JobPostingData:
    """Ask ``service`` for the one advert it calls ``key``, cleaned. Raises `SearchRefused`."""
    gone = _("%(service)s no longer has that advert.") % {"service": str(service.label)}
    if not _is_advert(key):
        raise SearchRefused(_("That is not an advert this service could have named."))
    try:
        built = service.one(key)
    except Exception as error:
        logger.exception("Listing search %r could not describe its request", service.key)
        raise SearchRefused(_("That advert cannot be asked of this service.")) from error
    document = _get(service, _address(service, built))
    if document is None:
        raise SearchRefused(gone)
    try:
        found = service.read_one(document, key)
    except Exception as error:
        logger.exception("Listing search %r could not read its answer", service.key)
        raise SearchRefused(_("The service's answer could not be read.")) from error
    if found is None:
        raise SearchRefused(gone)
    try:
        return cleaned(service, found)
    except (TypeError, ValueError) as error:
        raise SearchRefused(_("The service's answer could not be read.")) from error


def _is_advert(key) -> bool:
    return isinstance(key, str) and bool(_ADVERT.fullmatch(key))


def _address(service: ListingSearch, built) -> str:
    """The address Postulo asks: the search's own, with Postulo's encoding of its parameters.

    The search names an address and hands over a mapping; the query string is written here
    and nowhere else, so nothing typed can end a parameter, start another, or reach the path
    or the host.
    """
    outside = SearchRefused(_("The service's address is not one it declared."))
    try:
        address, parameters = built
        parts = urlsplit(str(address))
        port = parts.port
    except (TypeError, ValueError) as error:
        raise outside from error
    if (
        parts.scheme != "https"
        or (parts.hostname or "") not in service.hosts
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in str(address)
        or "#" in str(address)
        or not str(address).isprintable()
        or " " in str(address)
        or "\\" in str(address)
    ):
        logger.warning("Listing search %r asked for %s, outside its hosts", service.key, address)
        raise outside
    query = urlencode(_pairs(parameters))
    if len(query) > MAX_QUERY:
        raise SearchRefused(_("That search is longer than a service can be asked."))
    return f"{address}?{query}" if query else str(address)


def _pairs(parameters) -> list[tuple[str, str]]:
    """A search's parameters as the names and values to encode, in the order it gave them."""
    unreadable = SearchRefused(_("That search cannot be asked of this service."))
    if not isinstance(parameters, Mapping):
        raise unreadable
    pairs: list[tuple[str, str]] = []
    for name, value in parameters.items():
        if not isinstance(name, str) or not _PARAMETER.fullmatch(name):
            raise unreadable
        values = value if isinstance(value, (list, tuple)) else [value]
        for one in values:
            if isinstance(one, bool):
                pairs.append((name, "true" if one else "false"))
            elif isinstance(one, (int, float)):
                pairs.append((name, str(one)))
            elif isinstance(one, str):
                pairs.append((name, text(one, MAX_VALUE)))
            else:
                raise unreadable
    return pairs


def _get(service: ListingSearch, url: str):
    """The JSON at ``url``, or ``None`` where the service says there is nothing there."""
    label = str(service.label)
    deadline = http.deadline_in(SECONDS)
    headers = {"User-Agent": user_agent(), "Accept": "application/json"}
    headers.update(dict(service.headers))
    try:
        with (
            http.public_only_client(
                timeout=SECONDS, follow_redirects=False, headers=headers
            ) as client,
            http.until(deadline),
            client.stream("GET", url, headers={"Accept-Encoding": http.ACCEPT_ENCODING}) as answer,
        ):
            if answer.is_redirect:
                raise SearchRefused(
                    _("%(service)s sent the request somewhere else.") % {"service": label}
                )
            if answer.status_code in (404, 410):
                return None
            if answer.status_code == 400:
                raise SearchRefused(
                    _("%(service)s did not accept that search.") % {"service": label}
                )
            if answer.status_code == 429:
                # Said, and never tried again: the next press is the person's to make.
                raise SearchRefused(
                    _("%(service)s asked for fewer requests. Try again later.") % {"service": label}
                )
            if answer.status_code != 200:
                raise SearchRefused(
                    _("%(service)s answered %(status)s.")
                    % {"service": label, "status": answer.status_code}
                )
            body = http.read_body(answer, limit=MAX_BYTES, deadline=deadline)
    except SearchRefused:
        raise
    except http.DestinationRefused as error:
        raise SearchRefused(str(error)) from error
    except http.BodyTooLarge as error:
        raise SearchRefused(
            _("%(service)s's answer was larger than a page of adverts should be.")
            % {"service": label}
        ) from error
    except (http.BodyTooSlow, httpx.TimeoutException) as error:
        raise SearchRefused(
            _("%(service)s took too long to answer.") % {"service": label}
        ) from error
    except http.BodyRefused as error:
        raise SearchRefused(str(error)) from error
    except httpx.HTTPError as error:
        raise SearchRefused(_("%(service)s could not be reached.") % {"service": label}) from error
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SearchRefused(
            _("%(service)s's answer could not be read.") % {"service": label}
        ) from error


# ------------------------------------------------------------- a stranger's text

#: The control and formatting characters a line of text never keeps: they draw nothing, and
#: the bidirectional ones reorder what stands beside them.
_UNSEEN = ("Cc", "Cf", "Zl", "Zp")


def text(value, limit: int = MAX_VALUE) -> str:
    """Plain text on one line: normalised, no control or formatting characters, cut."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    kept = "".join(" " if unicodedata.category(ch) in _UNSEEN else ch for ch in value)
    return " ".join(kept.split())[:limit]


def paragraphs(value, limit: int = MAX_DESCRIPTION) -> str:
    """Plain text that keeps its lines: the same cleaning, a line at a time."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        "".join(" " if unicodedata.category(ch) in _UNSEEN else ch for ch in line).rstrip()
        for line in value.split("\n")
    ]
    kept: list[str] = []
    for line in lines:
        # Never more than one empty line running: an advert's spacing is not its text.
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()[:limit]


def cleaned(service: ListingSearch, posting) -> JobPostingData:
    """The posting as Postulo will show and hold it: every string tidied, every word of a
    vocabulary one of Postulo's own, and the source the service's name.

    Raises ``TypeError`` for what is not a posting and ``ValueError`` for one that cannot be
    one (no title, an address that is not a page).
    """
    if not isinstance(posting, JobPostingData):
        raise TypeError("not a posting")
    currency = text(posting.salary_currency, 3).upper()
    return JobPostingData(
        title=text(posting.title, MAX_TITLE),
        company_name=text(posting.company_name, MAX_COMPANY),
        location=text(posting.location, MAX_LOCATION),
        remote_type=posting.remote_type if posting.remote_type in REMOTE_TYPES else "",
        employment_type=(
            posting.employment_type if posting.employment_type in EMPLOYMENT_TYPES else ""
        ),
        description=paragraphs(posting.description),
        salary_min=posting.salary_min,
        salary_max=posting.salary_max,
        salary_currency=currency if _CURRENCY.fullmatch(currency) else "",
        salary_period=posting.salary_period if posting.salary_period in SALARY_PERIODS else "",
        posted_at=posting.posted_at,
        closes_at=posting.closes_at,
        url=text(posting.url, 500),
        source=text(str(service.source), MAX_SOURCE),
    )


# --------------------------------------------------- what the instance may call


def offered_anywhere() -> list[ListingSearch]:
    """The services somebody on this instance can search: the record of processing lists
    them, because somebody's press can reach them, and no other.

    The instance's answer first, then that of each account an administrator's row gives the
    feature or a service to: a few accounts, asked as the page would ask for them.
    """
    from django.contrib.auth import get_user_model

    from postulo.plugins.models import PluginPolicy

    known = searches()
    reachable = {search.key for search in offered_to(None)}
    named = [FEATURE, *(switch_of(key) for key in known)]
    people = (
        PluginPolicy.objects.filter(
            person__isnull=False,
            plugin__in=named,
            state__in=(PluginPolicy.State.AVAILABLE, PluginPolicy.State.FORCED_ON),
        )
        .values_list("person", flat=True)
        .distinct()
    )
    for person in get_user_model().objects.filter(pk__in=people):
        reachable |= {search.key for search in offered_to(person)}
    return [search for key, search in known.items() if key in reachable]


def services() -> list[dict]:
    """Every job service this instance may search, for the record of processing (#297,
    #1253): who, on which hosts, what is sent, and under what licence."""
    return [
        {
            "key": search.key,
            "label": str(search.label),
            "source": str(search.source),
            "hosts": list(search.hosts),
            "sends": _("the words, the place and the filters searched for"),
            "licence": search.licence,
            "licence_url": search.licence_url,
        }
        for search in sorted(offered_anywhere(), key=lambda item: item.key)
    ]
