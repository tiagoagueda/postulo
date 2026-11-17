"""What the review of *Find details* offers, and how a ticked value is kept (#1256).

`enrichment` asks the sources and cleans what they say. This module sets that beside what the
company already has and sorts each value into one of three places:

- **offered**, a control that starts unticked: a value for a field that is blank, or one more
  of something a company holds several of (an address, a number, a profile, an industry, an
  acronym, an identifier of a scheme it has none of);
- **said**, a sentence and no control: a value for a field that holds one and is filled. A
  typed value is never overwritten, so a website that differs from the one recorded is told
  and not offered;
- **also said**: a fact Postulo has nowhere to keep yet (when it was founded, how many it
  employs), and what a source calls the company.

Two sources that give the same value are one line naming both. The offers are worked out
again from the held answer when *Keep* is pressed, and a tick is matched against them by its
identifier, so a hand-written POST can keep only what the page could have offered.

**The takers** write what is kept, each through the path the forms use, with the source's
name as ``via`` in the company's history. *Register lookup* writes an address, a state, an
industry, an acronym and a register number through the same ones (`register_views`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from postulo.core import card_rows, link_services, phone_numbers, phones, web_links
from postulo.core.models import PostalAddress

from . import enrichment, identifiers, industries, trail
from .models import Company, CompanyName, Industry, LegalState

#: The fields that hold one value. A blank one is offered a value; a filled one is only told.
SINGLE = ("website", "careers_url", "email", "logo")
#: The one field whose value is replaced where the person chooses to: a state always has a
#: value, so the choice starts on leaving it as it is.
REPLACED = ("legal_state",)
#: The fields a company holds several of: a new value is added beside the ones it has.
SEVERAL = ("phone", "social", "acronym", "identifier", "address", "industry")
#: What is said and never offered: Postulo has no column for the last two yet.
ONLY_SAID = ("name", "founded", "size")

#: What each field is called on the review, in the order the review draws them.
LABELS = {
    "website": gettext_lazy("Website"),
    "careers_url": gettext_lazy("Careers page"),
    "email": gettext_lazy("Email address"),
    "phone": gettext_lazy("Telephone number"),
    "social": gettext_lazy("Social profile"),
    "name": gettext_lazy("Name"),
    "acronym": gettext_lazy("Acronym or short name"),
    "identifier": gettext_lazy("Identifier"),
    "address": gettext_lazy("Address"),
    "industry": gettext_lazy("Industry"),
    "legal_state": gettext_lazy("Legal state"),
    "founded": gettext_lazy("Founded"),
    "size": gettext_lazy("People employed"),
    "logo": gettext_lazy("Logo"),
}


class NotKept(Exception):
    """A value that could not be written. The message, where there is one, says why."""


# ------------------------------------------------------------------- the review


@dataclass(frozen=True)
class Origin:
    """One source's word for one value: who said it, and where they read it."""

    source: str
    origin: str
    url: str = ""
    licence: str = ""
    licence_url: str = ""
    credit: str = ""
    as_of: str = ""
    #: Whether the source read the company's own website, which a logo kept from it records.
    own_site: bool = False


@dataclass
class Choice:
    """One value the review offers, with everybody who said it."""

    id: str
    field: str
    text: str
    value: object
    origins: list[Origin] = field(default_factory=list)
    #: What the taker needs beside the value: an unsaved address row, a service's key.
    extra: object = None

    @property
    def via(self) -> str:
        return ", ".join(dict.fromkeys(origin.source for origin in self.origins))[:120]


@dataclass
class Group:
    """The choices for one field. ``radio`` is a choice of one, starting on none of them."""

    field: str
    label: str
    choices: list[Choice]
    radio: bool = False


@dataclass
class Said:
    """A value that is told and not offered. ``current`` is what the company already has in
    its place; ``why`` another reason it cannot be kept; neither, and there is nowhere to
    keep it."""

    label: str
    text: str
    origins: list[Origin]
    current: str = ""
    why: str = ""


@dataclass
class Attribution:
    """One source that was asked: who is named, under what terms, and what went wrong."""

    label: str
    source: str
    licence: str = ""
    licence_url: str = ""
    own_site: bool = False
    error: str = ""
    count: int = 0


@dataclass
class Review:
    groups: list[Group] = field(default_factory=list)
    said: list[Said] = field(default_factory=list)
    also: list[Said] = field(default_factory=list)
    sources: list[Attribution] = field(default_factory=list)

    def choices(self) -> list[Choice]:
        return [choice for group in self.groups for choice in group.choices]


def _same(one: str, other: str) -> bool:
    return " ".join((one or "").split()).casefold() == " ".join((other or "").split()).casefold()


def _id(name: str, key) -> str:
    """A choice's identifier: its field and a digest of what makes it that choice, so that a
    tick names the same value however the list around it has moved since it was drawn."""
    digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]
    return f"{name}-{digest}"


def address_row(company, address, label: str) -> PostalAddress:
    """An unsaved row for an address a source gave, so it is compared as a stored one is."""
    return PostalAddress(
        content_type=ContentType.objects.get_for_model(Company),
        object_id=company.pk,
        kind=PostalAddress.Kind.OTHER,
        label=str(label)[:60],
        street=address.street,
        postcode=address.postcode,
        municipality=address.municipality,
        region=address.region,
        country=address.country,
    )


def headcount(value) -> str:
    """A head count as words: one number, a range, or "and more"."""
    low, high = value
    if high is None:
        return ngettext("%(count)s person or more", "%(count)s people or more", low) % {
            "count": low
        }
    if low == high:
        return str(low)
    return _("%(low)s to %(high)s") % {"low": low, "high": high}


def _origin(result, value) -> Origin:
    return Origin(
        source=str(result.source.label),
        origin=value.origin,
        url=value.url,
        licence=value.licence,
        licence_url=value.licence_url,
        credit=value.credit,
        as_of=value.as_of,
        own_site=result.source.own_site,
    )


class _Sorter:
    """Sorts each value into offered, said or also said, against what the company has."""

    def __init__(self, company, person) -> None:
        self.company = company
        self.person = person
        self.offered: dict[str, dict[str, Choice]] = {}
        self.said: dict[tuple, Said] = {}
        self.also: dict[tuple, Said] = {}
        self.country = enrichment._country_of(company)
        self.names = {company.name.casefold(), *(row.name.casefold() for row in company.acronyms())}
        self.identifiers = {row.scheme: row.value for row in company.identifiers.all()}
        self.addresses = {row.comparable for row in company.postal_addresses.all()}
        self.numbers = list(company.phone_numbers.all())
        self.links = list(company.web_links.filter(kind=link_services.SOCIAL))

    # ------------------------------------------------------------ where a value goes

    def offer(self, name: str, key, text: str, value, origin: Origin, extra=None) -> None:
        choices = self.offered.setdefault(name, {})
        identifier = _id(name, key)
        if identifier not in choices:
            choices[identifier] = Choice(identifier, name, text, value, extra=extra)
        if origin not in choices[identifier].origins:
            choices[identifier].origins.append(origin)

    def say(
        self, name: str, text: str, origin: Origin, *, current: str = "", why: str = ""
    ) -> None:
        kept = self.said if (current or why) else self.also
        row = kept.setdefault(
            (name, text.casefold()),
            Said(str(LABELS[name]), text, [], current=current, why=why),
        )
        if origin not in row.origins:
            row.origins.append(origin)

    # ----------------------------------------------------------------- each field

    def sort(self, value, origin: Origin) -> None:
        getattr(self, f"_{value.field}")(value.value, origin)

    def _one_value(self, name: str, held: str, value: str, origin: Origin) -> None:
        if not held:
            self.offer(name, web_links.same_address(value), value, value, origin)
        elif web_links.same_address(held) != web_links.same_address(value):
            self.say(name, value, origin, current=held)

    def _website(self, value, origin) -> None:
        self._one_value("website", self.company.website, value, origin)

    def _careers_url(self, value, origin) -> None:
        self._one_value("careers_url", self.company.careers_url, value, origin)

    def _email(self, value, origin) -> None:
        held = self.company.email
        if not held:
            self.offer("email", value.casefold(), value, value, origin)
        elif held.casefold() != value.casefold():
            self.say("email", value, origin, current=held)

    def _logo(self, value, origin) -> None:
        if not self.company.has_logo:
            self.offer("logo", value, value, value, origin)
        elif not any(name == "logo" for name, _text in self.said):
            # One line however many pictures a site declares: the logo is changed from its
            # own dialog on the edit page.
            self.say("logo", value, origin, why=_("It already has a logo."))

    def _phone(self, value, origin) -> None:
        verdict = phones.check(value, self.country)
        if verdict.impossible:
            self.say("phone", value, origin, why=verdict.message)
            return
        number = phones.combine(value, self.country)
        key = phones.normalise(number) or number
        if any((row.normalised or row.number) == key for row in self.numbers):
            return
        if self.numbers and not phone_numbers.several_allowed(self.person):
            self.say("phone", number, origin, why=_("Several telephone numbers are switched off."))
            return
        self.offer("phone", key, number, number, origin)

    def _social(self, value, origin) -> None:
        service = link_services.guess(value, link_services.SOCIAL)
        if service is None:
            self.say("social", value, origin, why=_("Not a profile on a service Postulo knows."))
            return
        key = web_links.same_address(value)
        if any(web_links.same_address(row.url) == key for row in self.links):
            return
        if web_links.one_row_per_service(link_services.SOCIAL, service.key):
            held = next((row.url for row in self.links if row.service == service.key), "")
            if held:
                self.say("social", value, origin, current=held)
                return
        if self.links and not web_links.several_allowed(self.person, link_services.SOCIAL):
            self.say("social", value, origin, why=_("Several social profiles are switched off."))
            return
        text = f"{service.label}: {value}"
        self.offer("social", key, text, value, origin, extra=service.key)

    def _name(self, value, origin) -> None:
        if value.casefold() not in self.names:
            self.say("name", value, origin, current=self.company.name)

    def _acronym(self, value, origin) -> None:
        if value.casefold() not in self.names:
            self.offer("acronym", value.casefold(), value, value, origin)

    def _identifier(self, value, origin) -> None:
        scheme, number = value
        text = f"{identifiers.label_for(scheme)}: {number}"
        held = self.identifiers.get(scheme)
        if held is None:
            self.offer("identifier", (scheme, number), text, value, origin)
        elif held != number:
            self.say("identifier", text, origin, current=held)

    def _address(self, value, origin) -> None:
        row = address_row(self.company, value, _("Head office"))
        if row.comparable_form() not in self.addresses:
            self.offer("address", row.comparable_form(), row.one_line(), value, origin, extra=row)

    def _industry(self, value, origin) -> None:
        name = industries.name_for(value)
        if name and not self.company.industries.filter(code=value).exists():
            text = _("%(industry)s (NACE %(division)s)") % {"industry": name, "division": value}
            self.offer("industry", value, text, value, origin, extra=name)

    def _legal_state(self, value, origin) -> None:
        if value == self.company.legal_state:
            return
        since = parse_date(origin.as_of) if len(origin.as_of) == 10 else None
        label = str(LegalState(value).label)
        text = (
            _("%(state)s since %(date)s") % {"state": label, "date": origin.as_of}
            if since
            else label
        )
        key = (value, origin.as_of if since else "")
        self.offer("legal_state", key, text, value, origin, extra=since)

    def _founded(self, value, origin) -> None:
        self.say("founded", value, origin)

    def _size(self, value, origin) -> None:
        self.say("size", headcount(value), origin)


def review(company, results, person) -> Review:
    """What the review page draws for ``results``, set against what ``company`` has now."""
    sorter = _Sorter(company, person)
    found = Review()
    for result in results:
        found.sources.append(
            Attribution(
                label=str(result.source.label),
                source=str(result.source.source),
                licence=str(result.source.licence),
                licence_url=result.source.licence_url,
                own_site=result.source.own_site,
                error=result.error,
                count=len(result.values),
            )
        )
        for value in result.values:
            sorter.sort(value, _origin(result, value))
    for name in enrichment.FIELDS:
        choices = list(sorter.offered.get(name, {}).values())
        if choices:
            found.groups.append(
                Group(
                    name,
                    str(LABELS[name]),
                    choices,
                    radio=name in REPLACED or (name in SINGLE and len(choices) > 1),
                )
            )
    found.said = list(sorter.said.values())
    found.also = list(sorter.also.values())
    return found


def ticked(found: Review, post) -> list[Choice]:
    """The choices a posted form ticked, among the ones the page could have offered: one at
    most for a field that holds one value."""
    asked = set(post.getlist("take"))
    for group in found.groups:
        if group.radio:
            asked.add(post.get(f"choose-{group.field}", ""))
    chosen: list[Choice] = []
    for group in found.groups:
        mine = [choice for choice in group.choices if choice.id in asked]
        chosen += mine[:1] if group.field in (*SINGLE, *REPLACED) else mine
    return chosen


# ------------------------------------------------------------------- the takers
#
# Each writes one thing to a company and returns the names its history records, or raises
# `NotKept`. The caller has already scoped the company to a grant to write.


def take_acronyms(company, names) -> list[str]:
    added = [company.add_acronym(name, CompanyName.Source.TYPED) for name in names]
    return ["names"] if any(added) else []


def take_address(company, row) -> list[str]:
    row.is_primary = not company.postal_addresses.exists()
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError as error:
        raise NotKept(_("That address could not be added.")) from error
    return [trail.BLOCKS["postal_addresses"]]


def take_state(company, state: str, since, source: str) -> list[str]:
    company.legal_state = state
    company.legal_state_since = since
    company.legal_state_source = (source or "")[:500]
    company.acquired_by = None
    company.save(
        update_fields=[
            "legal_state",
            "legal_state_since",
            "legal_state_source",
            "acquired_by",
            "updated_at",
        ]
    )
    return ["legal_state", "legal_state_since"]


def take_industry(company, user, name: str) -> list[str]:
    company.industries.add(*Industry.named(user, [name]))
    return ["industries"]


def take_identifier(company, scheme: str, value: str) -> list[str]:
    try:
        with transaction.atomic():
            # Through the company, which the caller has already scoped to a grant to write;
            # the row is owned as the company is.
            company.identifiers.create(scheme=scheme, value=value)
    except IntegrityError as error:
        raise NotKept from error
    return [trail.BLOCKS["identifiers"]]


def _take_column(company, actor, choice: Choice) -> list[str]:
    from .surface import update_company

    # Its own line in the history, written by the call (`via` names the source).
    update_company(company, actor=actor, via=choice.via, **{choice.field: choice.value})
    return []


def _take_email(company, actor, choice: Choice) -> list[str]:
    company.email = choice.value
    company.save(update_fields=["email", "updated_at"])
    return ["email"]


def _take_phone(company, actor, choice: Choice) -> list[str]:
    from .surface import save_phone_numbers

    rows = [*card_rows.numbers_for(company, actor), card_rows.PhoneRow(number=choice.value)]
    saved = save_phone_numbers(company, rows, actor=actor, via=choice.via)
    if saved.refused or getattr(saved, "held_back", 0):
        reasons = [refusal.reason for refusal in saved.refused]
        raise NotKept(
            _("%(number)s was not added: %(reason)s")
            % {"number": choice.value, "reason": " ".join(reasons)}
            if reasons
            else _("%(number)s was not added.") % {"number": choice.value}
        )
    return []


def _take_social(company, actor, choice: Choice) -> list[str]:
    from .surface import save_web_links

    rows = [
        *card_rows.links_for(company, actor, link_services.SOCIAL),
        card_rows.LinkRow(url=choice.value, kind=link_services.SOCIAL, service=choice.extra or ""),
    ]
    saved = save_web_links(company, rows, actor=actor, via=choice.via)
    if saved.refused or getattr(saved, "held_back", 0):
        reasons = [refusal.reason for refusal in saved.refused]
        raise NotKept(
            _("%(address)s was not added: %(reason)s")
            % {"address": choice.value, "reason": " ".join(reasons)}
            if reasons
            else _("%(address)s was not added.") % {"address": choice.value}
        )
    return []


def _take_acronym(company, actor, choice: Choice) -> list[str]:
    return take_acronyms(company, [choice.value])


def _take_identifier(company, actor, choice: Choice) -> list[str]:
    scheme, value = choice.value
    try:
        return take_identifier(company, scheme, value)
    except NotKept as refusal:
        raise NotKept(
            _("Another of your companies already has %(identifier)s.") % {"identifier": choice.text}
        ) from refusal


def _take_address(company, actor, choice: Choice) -> list[str]:
    return take_address(company, choice.extra)


def _take_industry(company, actor, choice: Choice) -> list[str]:
    return take_industry(company, actor, choice.extra)


def _take_state(company, actor, choice: Choice) -> list[str]:
    source = next((origin.url for origin in choice.origins if origin.url), "")
    return take_state(company, choice.value, choice.extra, source)


def _take_logo(company, actor, choice: Choice) -> list[str]:
    from . import logos

    # Fetched, decoded and stored as a logo from anywhere is (`logos`): the address is a
    # stranger's, and `download` is the one thing that dials it. Recorded as today's are:
    # found on the company's website, or fetched from an address.
    found_on_its_site = any(origin.own_site for origin in choice.origins)
    try:
        # A fetch of an address somebody else wrote, as one typed into the logo dialog is,
        # so it spends one use of the person's fetch allowance (#407).
        logos.spend_fetch(actor)
        logos.store(
            company,
            logos.process(logos.download(choice.value)),
            source="website" if found_on_its_site else "url",
            url=choice.value,
        )
    except logos.UnusableLogo as error:
        raise NotKept(_("The logo was not kept: %(problem)s") % {"problem": error}) from error
    return []


TAKERS = {
    "website": _take_column,
    "careers_url": _take_column,
    "email": _take_email,
    "phone": _take_phone,
    "social": _take_social,
    "acronym": _take_acronym,
    "identifier": _take_identifier,
    "address": _take_address,
    "industry": _take_industry,
    "legal_state": _take_state,
    "logo": _take_logo,
}


def keep(company, actor, chosen: list[Choice]) -> tuple[list[str], list[str]]:
    """Write each chosen value; what could not be written is said, and the rest stays.

    Returns the fields that were kept and the reasons for the ones that were not. Each
    value is its own transaction and its own line in the company's history, naming the
    source or sources that said it; a logo is fetched outside any transaction (#220).
    """
    from .surface import WriteRefused

    kept: list[str] = []
    refused: list[str] = []
    for choice in chosen:
        taker = TAKERS.get(choice.field)
        if taker is None:
            continue
        try:
            if choice.field == "logo":
                taker(company, actor, choice)
            else:
                with transaction.atomic():
                    names = taker(company, actor, choice)
                    if names:
                        trail.record(company, actor, trail.EDITED, fields=names, via=choice.via)
        except (NotKept, WriteRefused) as refusal:
            refused.append(str(refusal))
            continue
        kept.append(choice.field)
    return kept, refused
