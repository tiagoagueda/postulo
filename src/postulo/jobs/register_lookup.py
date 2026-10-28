"""Asking a company register about a number a person already recorded (#1021, #935).

A company can carry a register number (``FR 552081317``) and a Legal Entity Identifier, and
the identifier module checks both offline: their shape, their check digits, and where each
leads. Whether the register *has* the number, and what it says the company is called, where it
is, whether it is still trading and what it does, is a question for the register, and this is
the one place that asks it. Never on a page load, a save, an import or a schedule: a person
presses *Look up* beside the number, sees what came back, and ticks what to keep.

**Postulo makes the request; an adapter only describes it.** An adapter (`Adapter`) names a
scheme and a country, the hosts its register answers on, the address to ask for one number
and how to read the JSON that comes back. The request goes through the public-only client
(rule 5 of the threat model) with Postulo's user agent, a deadline, a body limit and no
redirects, to one of the adapter's own hosts and nowhere else. So an adapter installed from
another package (`postulo.register_adapters`, the open group beside ``postulo.link_services``)
can add a country, and cannot make the server dial anywhere its declaration does not name.

**What it sends is the company's number, and nothing else.** No name, no address, nothing
about the person asking: the number is in the address asked for, and the request carries the
instance's user agent and no cookie, no referrer and no account. The register learns that
somebody on this instance is interested in that company, which is why the whole of it sits
behind a feature that is off until an administrator switches it on (`register-lookup`), a
per-account allowance (``POSTULO_REGISTER_LOOKUP_RATE``) and a press.

**What comes back is a stranger's text.** Every string is normalised, stripped of control and
bidirectional-formatting characters and cut to the column it might go in; a country is kept
only when it is one Postulo knows, a NACE division only when the classification has it, and a
date only when it is a date. Nothing is kept until the person ticks it on the review page.

**GLEIF answers for any country.** An LEI record carries the company's local register number
(``registeredAs``), its register (``registeredAt``) and its jurisdiction, so a company with an
LEI and no register number is offered one, and a company with both is told whether they agree
-- the cross-check the identifier module cannot make offline, because an LEI's first four
characters name the body that issued it and not a country (#935).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
from django.utils.translation import gettext as _

from postulo.plugins import http

logger = logging.getLogger(__name__)

#: The plugin that decides whether any of this is offered, and the setting that bounds it.
FEATURE = "register-lookup"
RATE_SETTING = "POSTULO_REGISTER_LOOKUP_RATE"

#: The two schemes a lookup starts from, as `postulo.jobs.identifiers` names them.
REGISTER = "register"
LEI = "lei"
SCHEMES = (REGISTER, LEI)

#: A register's answer about one company is some kilobytes; Poland's full extract of a large
#: company with every branch listed is a few hundred. Two megabytes is a document nobody meant.
MAX_BYTES = 2 * 1024 * 1024
SECONDS = 15.0

#: How long each kind of text may be, as the column it could go in holds it.
MAX_NAME = 200
MAX_STREET = 300
MAX_POSTCODE = 20
MAX_PLACE = 120
MAX_WORDS = 200
MAX_CODE = 20
MAX_NUMBER = 100

_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_DIVISION = re.compile(r"[0-9]{2}")

#: What a register says about whether a company still exists, in `LegalState`'s words: the
#: three a register can state. *Acquired* is never one: a register says a company ended, not
#: who took it over.
ACTIVE = "active"
ADMINISTRATION = "administration"
DISSOLVED = "dissolved"
STATES = (ACTIVE, ADMINISTRATION, DISSOLVED)


class LookupRefused(Exception):
    """Why nothing came back, in a sentence a person can read."""


# ------------------------------------------------------------------- the contract
#
# What an adapter is, and what it hands back. On the plugin surface as `RegisterAdapter`,
# `RegisterRecord` and `RegisterAddress`, so a package for another country builds the same
# objects Postulo's own adapters do.


@dataclass(frozen=True)
class Address:
    """Where the register says the company is: its registered office."""

    street: str = ""
    postcode: str = ""
    municipality: str = ""
    region: str = ""
    #: ISO 3166-1 alpha-2, upper case.
    country: str = ""

    def is_empty(self) -> bool:
        return not any((self.street, self.postcode, self.municipality, self.region))


@dataclass(frozen=True)
class Record:
    """What one register said about one number. Every field may be empty.

    ``state`` is one of `STATES` where the register says it plainly, and ``state_words`` is
    what the register itself called it. ``activity`` is the code as the register writes it,
    in its own classification, and ``division`` the NACE division that code falls in, where
    the classification is NACE's or a national version of it. ``registered_as``,
    ``registered_at`` and ``jurisdiction`` are GLEIF's: the local register number, the
    register's identifier and where the entity is incorporated.
    """

    number: str
    name: str = ""
    address: Address | None = None
    state: str = ""
    state_words: str = ""
    state_since: date | None = None
    activity: str = ""
    activity_words: str = ""
    division: str = ""
    registered_as: str = ""
    registered_at: str = ""
    jurisdiction: str = ""


@dataclass(frozen=True)
class Adapter:
    """How one register is asked about one number (#1021).

    ``scheme`` is the identifier a lookup starts from (``register`` or ``lei``) and ``country``
    the two letters a register number begins with, or empty for a scheme that is not national.
    ``addresses(number)`` gives the address or addresses to ask, in order -- the next is asked
    only when one answers that it has no such entry -- and every one has to be ``https`` on
    one of ``hosts``. ``read(document, number)`` turns the JSON that came back into a
    `Record`, or ``None`` for "no such entry here". ``page(number)`` is the register's own page
    for the number, linked from the review, and ``source``, ``licence`` and ``licence_url``
    are the attribution the register's terms ask for, shown beside what came back.
    """

    key: str
    label: Any
    scheme: str
    country: str
    hosts: tuple[str, ...]
    addresses: Callable[[str], Sequence[str]]
    read: Callable[[Any, str], Record | None]
    source: Any
    licence: str = ""
    licence_url: str = ""
    page: Callable[[str], str] | None = None
    #: Extra request headers the register's documentation asks for (an ``Accept`` version).
    headers: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ------------------------------------------------------------------ the registry


def _usable(adapter) -> bool:
    """Whether something a plugin handed over can be asked at all. Checked once, here."""
    return (
        isinstance(adapter, Adapter)
        and isinstance(adapter.key, str)
        and bool(_KEY.fullmatch(adapter.key))
        and adapter.scheme in SCHEMES
        and (
            bool(re.fullmatch(r"[A-Z]{2}", adapter.country or ""))
            if adapter.scheme == REGISTER
            else adapter.country == ""
        )
        and bool(adapter.hosts)
        and all(isinstance(host, str) and _HOST.fullmatch(host) for host in adapter.hosts)
        and callable(adapter.addresses)
        and callable(adapter.read)
        and (adapter.page is None or callable(adapter.page))
    )


def _slot(adapter: Adapter) -> tuple[str, str]:
    return (adapter.scheme, adapter.country)


_merged: tuple[list, dict[str, Adapter]] | None = None


def adapters() -> dict[str, Adapter]:
    """Every adapter this instance knows, by key, from every installed registry.

    **The registry Postulo ships is read first**, so a country it answers for is answered by
    it: a package installed beside it adds countries and cannot quietly send France's numbers
    somewhere else. Between two packages the first to claim a key or a country keeps it, and
    the second is logged and left out, which is the rule link services follow (#305).
    """
    global _merged

    from postulo.plugins import registry as plugin_registry

    installed = plugin_registry.plugins("register-adapter")
    if (
        _merged is not None
        and len(_merged[0]) == len(installed)
        and all(was is now for was, now in zip(_merged[0], installed, strict=True))
    ):
        return dict(_merged[1])
    shipped = tuple(plugin_registry.builtins().get("register-adapter", ()))
    found: dict[str, Adapter] = {}
    slots: set[tuple[str, str]] = set()
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        try:
            offered = list(getattr(plugin, "adapters", ()) or ())
        except Exception:
            logger.exception("Plugin %r's register adapters could not be read", plugin)
            continue
        for adapter in offered:
            if not _usable(adapter):
                logger.warning(
                    "Plugin %r offered a register adapter that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    adapter,
                )
            elif adapter.key in found or _slot(adapter) in slots:
                logger.warning(
                    "Register adapter %r was offered for a register already answered for; "
                    "the first one keeps it",
                    adapter.key,
                )
            else:
                found[adapter.key] = adapter
                slots.add(_slot(adapter))
    _merged = (installed, found)
    return dict(found)


def find(key: str) -> Adapter | None:
    """One adapter by key, or nothing for a key no installed registry knows any more."""
    return adapters().get(key or "")


def adapter_for(scheme: str, value: str) -> Adapter | None:
    """The adapter that answers for an identifier, or nothing.

    A register number's two first letters are its country; an LEI is answered by whichever
    adapter takes the scheme without a country.
    """
    if scheme not in SCHEMES or not value:
        return None
    country = value[:2].upper() if scheme == REGISTER else ""
    for adapter in adapters().values():
        if adapter.scheme == scheme and adapter.country == country:
            return adapter
    return None


def number_of(scheme: str, value: str) -> str:
    """What is sent: a register number without its country, or the LEI as it is."""
    value = (value or "").strip()
    if scheme == REGISTER:
        return value[3:] if value[2:3] == " " else value[2:]
    return value


# ------------------------------------------------------------------ the request


def user_agent() -> str:
    """Postulo's, naming where its source and its makers are, as the Wikidata request does."""
    from . import wikidata

    return wikidata.user_agent()


def lookup(adapter: Adapter, value: str) -> Record:
    """Ask ``adapter``'s register about one identifier. Raises `LookupRefused` with the reason."""
    number = number_of(adapter.scheme, value)
    if not number:
        raise LookupRefused(_("There is no number to look up."))
    try:
        addresses = [str(address) for address in adapter.addresses(number)]
    except Exception as error:
        logger.exception("Register adapter %r could not build its address", adapter.key)
        raise LookupRefused(_("That number cannot be asked of this register.")) from error
    if not addresses:
        raise LookupRefused(_("That number cannot be asked of this register."))
    for address in addresses:
        parts = urlsplit(address)
        if parts.scheme != "https" or (parts.hostname or "") not in adapter.hosts:
            logger.warning(
                "Register adapter %r asked for %s, outside its hosts", adapter.key, address
            )
            raise LookupRefused(_("The register's address is not one it declared."))
    for address in addresses:
        document = _get(adapter, address)
        if document is None:
            continue
        try:
            found = adapter.read(document, number)
        except Exception as error:
            logger.exception("Register adapter %r could not read its answer", adapter.key)
            raise LookupRefused(_("The register's answer could not be read.")) from error
        if found is None:
            continue
        if not isinstance(found, Record):
            raise LookupRefused(_("The register's answer could not be read."))
        return cleaned(found)
    raise LookupRefused(_("The register has no entry with that number."))


def _get(adapter: Adapter, url: str):
    """The JSON at ``url``, or ``None`` where the register says it has no such entry."""
    label = str(adapter.label)
    deadline = http.deadline_in(SECONDS)
    headers = {"User-Agent": user_agent(), "Accept": "application/json"}
    headers.update(dict(adapter.headers))
    try:
        with (
            http.public_only_client(
                timeout=SECONDS, follow_redirects=False, headers=headers
            ) as client,
            http.until(deadline),
            client.stream("GET", url, headers={"Accept-Encoding": http.ACCEPT_ENCODING}) as answer,
        ):
            if answer.is_redirect:
                raise LookupRefused(
                    _("%(register)s sent the request somewhere else.") % {"register": label}
                )
            if answer.status_code == 404:
                return None
            if answer.status_code == 410:
                raise LookupRefused(_("%(register)s has removed this entry.") % {"register": label})
            if answer.status_code == 429:
                raise LookupRefused(
                    _("%(register)s asked for fewer requests. Try again later.")
                    % {"register": label}
                )
            if answer.status_code != 200:
                raise LookupRefused(
                    _("%(register)s answered %(status)s.")
                    % {"register": label, "status": answer.status_code}
                )
            body = http.read_body(answer, limit=MAX_BYTES, deadline=deadline)
    except LookupRefused:
        raise
    except http.DestinationRefused as error:
        raise LookupRefused(str(error)) from error
    except http.BodyTooLarge as error:
        raise LookupRefused(
            _("%(register)s's answer was larger than one company's should be.")
            % {"register": label}
        ) from error
    except (http.BodyTooSlow, httpx.TimeoutException) as error:
        raise LookupRefused(
            _("%(register)s took too long to answer.") % {"register": label}
        ) from error
    except http.BodyRefused as error:
        raise LookupRefused(str(error)) from error
    except httpx.HTTPError as error:
        raise LookupRefused(
            _("%(register)s could not be reached.") % {"register": label}
        ) from error
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LookupRefused(
            _("%(register)s's answer could not be read.") % {"register": label}
        ) from error


# ------------------------------------------------------------- a stranger's text


def text(value, limit: int = MAX_WORDS) -> str:
    """Plain text on one line: normalised, no control or formatting characters, cut."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    kept = "".join(
        " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in value
    )
    return " ".join(kept.split())[:limit]


def _country(code) -> str:
    from postulo.core.phones import COUNTRY_CODES

    code = text(code, 2).upper()
    return code if code in COUNTRY_CODES else ""


def _division(code) -> str:
    from . import industries

    code = text(code, 2)
    return code if _DIVISION.fullmatch(code) and industries.name_for(code, "en") else ""


def cleaned(record: Record) -> Record:
    """The record as Postulo will hold it: every string tidied, every code checked."""
    address = record.address if isinstance(record.address, Address) else None
    if address is not None:
        address = Address(
            street=text(address.street, MAX_STREET),
            postcode=text(address.postcode, MAX_POSTCODE),
            municipality=text(address.municipality, MAX_PLACE),
            region=text(address.region, MAX_PLACE),
            country=_country(address.country),
        )
        if address.is_empty():
            address = None
    state = record.state if record.state in STATES else ""
    since = record.state_since if isinstance(record.state_since, date) else None
    return Record(
        number=text(record.number, MAX_NUMBER),
        name=text(record.name, MAX_NAME),
        address=address,
        state=state,
        state_words=text(record.state_words),
        state_since=since if state else None,
        activity=text(record.activity, MAX_CODE),
        activity_words=text(record.activity_words),
        division=_division(record.division),
        registered_as=text(record.registered_as, MAX_NUMBER),
        registered_at=text(record.registered_at, MAX_CODE),
        jurisdiction=text(record.jurisdiction, 6).upper(),
    )


def held(record: Record) -> dict:
    """The record as the session holds it: plain JSON."""
    found = asdict(record)
    found["state_since"] = record.state_since.isoformat() if record.state_since else None
    return found


def from_held(data: dict) -> Record:
    """A record back out of the session, cleaned again: the session is not a trusted store."""
    data = dict(data or {})
    address = data.get("address")
    since = data.get("state_since")
    try:
        since = date.fromisoformat(since) if isinstance(since, str) else None
    except ValueError:
        since = None
    return cleaned(
        Record(
            number=data.get("number") or "",
            name=data.get("name") or "",
            address=Address(**{k: address.get(k) or "" for k in Address.__dataclass_fields__})
            if isinstance(address, dict)
            else None,
            state=data.get("state") or "",
            state_words=data.get("state_words") or "",
            state_since=since,
            activity=data.get("activity") or "",
            activity_words=data.get("activity_words") or "",
            division=data.get("division") or "",
            registered_as=data.get("registered_as") or "",
            registered_at=data.get("registered_at") or "",
            jurisdiction=data.get("jurisdiction") or "",
        )
    )


# ---------------------------------------------------------- GLEIF and the register

#: Where a register number and an LEI record spell one country two ways (Greece is EL in a
#: register and GR in an LEI record, as `crosschecks` says for OpenCorporates).
_ALIASES = {"EL": "GR", "UK": "GB"}


def _alnum(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (value or "").casefold())


def _same_country(one: str, other: str) -> bool:
    one, other = one[:2].upper(), other[:2].upper()
    return _ALIASES.get(one, one) == _ALIASES.get(other, other)


def compare(register_value: str, record: Record) -> str:
    """Whether a stored register number and an LEI record's agree.

    ``"agrees"``, ``"differs"``, or empty where the LEI record names no register number to
    compare with. Spacing, punctuation and case are not a difference: ``HRB 32283`` and
    ``HRB32283`` are one number.
    """
    if not record.registered_as or not register_value:
        return ""
    if record.jurisdiction and not _same_country(register_value, record.jurisdiction):
        return "differs"
    same = _alnum(number_of(REGISTER, register_value)) == _alnum(record.registered_as)
    return "agrees" if same else "differs"


def register_value_from(record: Record) -> str:
    """The register number an LEI record offers, as a register number is stored, or nothing.

    The jurisdiction's country, a space, and ``registeredAs``, held to the register scheme's
    own rules -- its shape and, where the country has one, its check digit -- so that a
    number GLEIF holds in a form the scheme would refuse is said and not offered.
    """
    from django.core.exceptions import ValidationError

    from . import identifiers

    country = (record.jurisdiction or "")[:2].upper()
    if not record.registered_as or not re.fullmatch(r"[A-Z]{2}", country):
        return ""
    try:
        return identifiers.clean(REGISTER, f"{country} {record.registered_as}")
    except ValidationError:
        return ""


# --------------------------------------------------- what the instance may call


def offered_anywhere() -> bool:
    """Whether the feature is on for the instance, or for any account: the record of
    processing lists the registers then, because somebody's press can reach them."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.policy import decide

    if decide(FEATURE, None).on:
        return True
    return PluginPolicy.objects.filter(
        plugin=FEATURE,
        person__isnull=False,
        state__in=(PluginPolicy.State.AVAILABLE, PluginPolicy.State.FORCED_ON),
    ).exists()


def services() -> list[dict]:
    """Every register this instance may ask, for the record of processing (#297, #1021):
    who, on which hosts, what is sent, and under what licence."""
    found = []
    for adapter in sorted(adapters().values(), key=lambda item: (item.scheme, item.country)):
        found.append(
            {
                "key": adapter.key,
                "label": str(adapter.label),
                "source": str(adapter.source),
                "hosts": list(adapter.hosts),
                "sends": _("the company's LEI")
                if adapter.scheme == LEI
                else _("the company's register number"),
                "licence": adapter.licence,
                "licence_url": adapter.licence_url,
            }
        )
    return found
