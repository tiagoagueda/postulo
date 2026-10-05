"""Contacts as vCard 4.0: what the rows say, and what a file's cards would become (#660).

`core.vcard` is the mapping and holds no model. This is the half that knows Postulo's
rows: it turns a contact, a company and the person into the plain values that mapping
writes, and it takes the cards that mapping read and, for the ones a person chose, makes
the records -- every value through the same rule the form a person would type it in applies
(`core.phones`, `core.postal`, `core.web_links`, `core.messaging_handles`), so that a card
is not a way round a refusal.

**Nothing arrives unchosen.** An address book is not a job search. `review` says what each
card would become and what of it is not kept, and `apply` makes only the cards that were
named, never overwrites an existing contact, and reports a value that was refused while the
rest of its card is still imported.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.core import (
    addresses,
    link_services,
    messaging_handles,
    phone_numbers,
    phones,
    postal,
    slugs,
    vcard,
    web_links,
)
from postulo.core.models import PhoneNumber, PostalAddress

from .models import Company, Contact, Department

#: Where a file that has been read waits to be confirmed: what was read of it, which is not
#: the file.
SESSION_KEY = "vcard_file"

_url = URLValidator(schemes=["http", "https"])


# ------------------------------------------------------------------------ writing


def instance_name() -> str:
    """What tells this Postulo from another, for the identifier a card is given."""
    public = getattr(settings, "POSTULO_PUBLIC_URL", "")
    return public or hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:16]


def contacts_of(queryset):
    """``queryset`` of contacts with everything a card reads, fetched once."""
    return queryset.select_related("company", "department").prefetch_related(
        "phone_numbers", "postal_addresses", "web_links", "messaging_handles"
    )


def _phones(holder) -> tuple[vcard.Phone, ...]:
    return tuple(
        vcard.Phone(row.number, row.kind if row.kind in vcard.TEL_TYPES else "", row.is_primary)
        for row in holder.phone_numbers.all()
    )


def _addresses(holder) -> tuple[vcard.Address, ...]:
    return tuple(
        vcard.Address(
            street=row.street,
            postcode=row.postcode,
            municipality=row.municipality,
            region=row.region,
            country=row.country,
            kind=row.kind if row.kind in vcard.ADR_TYPES else "",
            primary=row.is_primary,
        )
        for row in holder.postal_addresses.all()
    )


def _impps(holder) -> tuple[str, ...]:
    uris = (vcard.impp_for(row.service, row.handle) for row in holder.messaging_handles.all())
    return tuple(uri for uri in uris if uri)


def card_for_contact(contact: Contact, *, notes: bool = False) -> vcard.Card:
    """One contact as a card. The notes are Postulo's private ones, and go only when asked."""
    return vcard.Card(
        name=contact.name,
        company=contact.company.name if contact.company_id else "",
        department=contact.department.name if contact.department_id else "",
        role=contact.role,
        email=contact.email,
        phones=_phones(contact),
        addresses=_addresses(contact),
        links=tuple(vcard.Link(row.url) for row in contact.web_links.all()),
        impps=_impps(contact),
        notes=contact.notes if notes else "",
        uid=vcard.uid_for(instance_name(), "contact", contact.pk),
        revised=contact.updated_at,
    )


def card_for_company(company: Company, *, notes: bool = False) -> vcard.Card:
    """A company as a ``KIND:org`` card. Its location waits for a company to have an
    address (#329)."""
    return vcard.Card(
        name=company.name,
        kind=vcard.KIND_ORG,
        company=company.name,
        links=(vcard.Link(company.website),) if company.website else (),
        notes=company.notes if notes else "",
        uid=vcard.uid_for(instance_name(), "company", company.pk),
        revised=company.updated_at,
    )


def card_for_person(user) -> vcard.Card:
    """The person's own card: what the default CV prints, and nothing else of the profile.

    Their first and last name, which is why this is the one card with an ``N``; the address
    their documents are sent from, their primary number, their links and their location.
    The street is not on it, as it is not on a CV. Export only, and never read back into the
    profile: the sign-in address and the verified numbers are not a card's to change.
    """
    from postulo.documents import printing

    printed = printing.resolve(user, None)
    profile = getattr(user, "profile", None)
    kind = ""
    if printed.phone and profile is not None:
        row = phone_numbers.primary_for(profile)
        if row is not None and row.number == printed.phone and row.kind in vcard.TEL_TYPES:
            kind = row.kind
    located = postal.primary_for(profile) if profile is not None else None
    # Only the town, the region and the country: that is what a CV prints of where one is.
    where = (
        (
            vcard.Address(
                municipality=located.municipality,
                region=located.region,
                country=located.country,
                kind=located.kind if located.kind in vcard.ADR_TYPES else "",
                primary=True,
            ),
        )
        if located is not None and (located.municipality or located.country)
        else ()
    )
    return vcard.Card(
        name=user.get_full_name() or user.display_name,
        given=(user.first_name or "").strip(),
        family=(user.last_name or "").strip(),
        role=getattr(profile, "headline", "") or "",
        email=printed.email,
        phones=(vcard.Phone(printed.phone, kind, True),) if printed.phone else (),
        addresses=where,
        links=tuple(
            vcard.Link(url) for url in (printed.website, printed.social, printed.repository) if url
        ),
        pronouns=(getattr(profile, "pronouns", "") or "").strip(),
        uid=vcard.uid_for(instance_name(), "person", user.pk),
    )


# ------------------------------------------------------------------------ reading


@dataclass
class Entry:
    """One card of a file, as the review page draws it."""

    index: int
    label: str
    becomes: str
    #: ``add``, ``present`` (already here, never overwritten), ``repeat`` (an earlier card
    #: of this file was the same one) or ``nameless``.
    outcome: str = "add"
    keeps: list[str] = field(default_factory=list)
    not_kept: list[str] = field(default_factory=list)

    @property
    def can_choose(self) -> bool:
        return self.outcome == "add"


@dataclass
class Report:
    contacts: int = 0
    companies: int = 0
    #: One sentence per value or card that was refused, each saying whose it was.
    problems: list[str] = field(default_factory=list)


def hold(read: vcard.Read) -> dict:
    return {"cards": [card.to_dict() for card in read.cards], "skipped": read.skipped}


def held_cards(held) -> list[vcard.Card]:
    """The cards a session holds, or nothing for anything that is not what `hold` left."""
    if not isinstance(held, dict) or not isinstance(held.get("cards"), list):
        return []
    cards = []
    for entry in held["cards"][: vcard.MAX_CARDS]:
        try:
            cards.append(vcard.Card.from_dict(entry))
        except (ValueError, TypeError):
            return []
    return cards


def _existing(user) -> tuple[set, set]:
    contacts = {
        (slugs.name_key(name), company_key or "")
        for name, company_key in Contact.objects.for_user(user).values_list(
            "name", "company__name_key"
        )
    }
    companies = set(Company.objects.for_user(user).values_list("name_key", flat=True))
    return contacts, companies


def _keeps(card: vcard.Card) -> list[str]:
    parts = []
    if card.role:
        parts.append(_("a job title"))
    if card.email:
        parts.append(_("an email address"))
    if card.phones:
        parts.append(
            ngettext("%(total)s telephone number", "%(total)s telephone numbers", len(card.phones))
            % {"total": len(card.phones)}
        )
    if card.addresses:
        parts.append(
            ngettext("%(total)s address", "%(total)s addresses", len(card.addresses))
            % {"total": len(card.addresses)}
        )
    if card.links:
        parts.append(
            ngettext("%(total)s link", "%(total)s links", len(card.links))
            % {"total": len(card.links)}
        )
    if any(vcard.impp_read(uri) for uri in card.impps):
        parts.append(_("messaging handles"))
    if card.notes:
        parts.append(_("notes"))
    return parts


def _not_kept(card: vcard.Card) -> list[str]:
    left = list(card.dropped)
    if card.pronouns:
        left.append("PRONOUNS")
    if any(vcard.impp_read(uri) is None for uri in card.impps):
        left.append("IMPP")
    if card.is_org:
        # A company has a name, a website and notes, and no number, address or address
        # for mail (#329): what a card of one carries beside those is left out.
        for present, name in (
            (card.email, "EMAIL"),
            (card.phones, "TEL"),
            (card.addresses, "ADR"),
            (card.role, "TITLE"),
            (card.impps, "IMPP"),
            (len(card.links) > 1, "URL"),
        ):
            if present:
                left.append(name)
    return list(dict.fromkeys(left))


def review(user, cards: list[vcard.Card]) -> list[Entry]:
    """What each card would become, worked out against the account as it stands."""
    contacts, companies = _existing(user)
    seen: set[str] = set()
    entries = []
    for index, card in enumerate(cards):
        label = card.name or _("A card with no name")
        entry = Entry(index, label, "", keeps=_keeps(card), not_kept=_not_kept(card))
        company_key = slugs.name_key(card.company) if card.company else ""
        if not card.name.strip():
            entry.outcome, entry.becomes = "nameless", _("Nothing: the card has no name.")
        elif card.uid and card.uid in seen:
            entry.outcome = "repeat"
            entry.becomes = _("Nothing: an earlier card in this file is the same one.")
        elif card.is_org and slugs.name_key(card.name) in companies:
            entry.outcome = "present"
            entry.becomes = _("Nothing: you already have this company, and it is not changed.")
        elif not card.is_org and (slugs.name_key(card.name), company_key) in contacts:
            entry.outcome = "present"
            entry.becomes = _(
                "Nothing: you already have a contact of this name there, and it is not changed."
            )
        elif card.is_org:
            entry.becomes = _("A company")
        elif card.company and card.department:
            entry.becomes = _("A contact at %(company)s, in %(department)s") % {
                "company": card.company,
                "department": card.department,
            }
        elif card.company:
            entry.becomes = _("A contact at %(company)s") % {"company": card.company}
        else:
            entry.becomes = _("A contact at no company")
        if card.uid:
            seen.add(card.uid)
        entries.append(entry)
    return entries


def apply(user, cards: list[vcard.Card], chosen: set[int]) -> Report:
    """Make the cards that were chosen, and only those.

    Looked at again as they are made: what was here when the page was drawn may not be what
    is here now, and an existing contact is never overwritten. One card's trouble is that
    card's -- reported, and the others go on.
    """
    report = Report()
    contacts, companies = _existing(user)
    for index in sorted(chosen):
        if not 0 <= index < len(cards):
            continue
        card = cards[index]
        name = card.name.strip()
        if not name:
            continue
        try:
            with transaction.atomic():
                if card.is_org:
                    if slugs.name_key(name) in companies:
                        continue
                    _make_company(user, card, report)
                    companies.add(slugs.name_key(name))
                else:
                    key = (
                        slugs.name_key(name),
                        slugs.name_key(card.company) if card.company else "",
                    )
                    if key in contacts:
                        continue
                    _make_contact(user, card, report)
                    contacts.add(key)
        except (ValidationError, ValueError) as error:
            report.problems.append(
                _("%(name)s was not added: %(why)s")
                % {"name": name[:80], "why": "; ".join(getattr(error, "messages", [str(error)]))}
            )
    return report


def _make_company(user, card: vcard.Card, report: Report) -> None:
    website = ""
    for link in card.links:
        try:
            _url(link.url)
        except ValidationError:
            report.problems.append(
                _("%(name)s: the address %(url)s is not a web address, and was left out.")
                % {"name": card.name[:80], "url": link.url[:80]}
            )
            continue
        website = link.url
        break
    Company.objects.create(
        owner=user, name=card.name.strip()[:200], website=website[:200], notes=card.notes
    )
    report.companies += 1


def _make_contact(user, card: vcard.Card, report: Report) -> None:
    from postulo.applications.services import get_or_create_company
    from postulo.jobs import structure

    who = card.name[:80]
    company = get_or_create_company(user, card.company[:200]) if card.company else None
    department = None
    if company is not None and card.department and structure.structure_allowed(user):
        department = Department.objects.filter(
            owner=user, company=company, name_key=slugs.name_key(card.department)
        ).first() or Department.objects.create(
            owner=user, company=company, name=card.department[:120]
        )
    email = card.email
    if email:
        try:
            validate_email(email)
        except ValidationError:
            report.problems.append(
                _("%(name)s: %(value)s is not an email address, and was left out.")
                % {"name": who, "value": email[:80]}
            )
            email = ""
    contact = Contact.objects.create(
        owner=user,
        company=company,
        department=department,
        name=card.name.strip()[:200],
        role=card.role[:200],
        email=email,
        notes=card.notes,
    )
    _numbers(user, contact, card, report)
    _postal(user, contact, card, report)
    _links(user, contact, card, report)
    _handles(user, contact, card, report)
    report.contacts += 1


def _numbers(user, contact: Contact, card: vcard.Card, report: Report) -> None:
    # A number with no country in front is read in the country of the card's own address.
    country = next((a.country for a in card.addresses if a.country), "")
    made = None
    wanted = None
    seen: set[str] = set()
    for phone in card.phones:
        number = phones.combine(phone.number, country)[:40]
        verdict = phones.check(number)
        if verdict.impossible:
            report.problems.append(
                _("%(name)s: the number %(value)s was left out. %(why)s")
                % {"name": card.name[:80], "value": phone.number[:40], "why": verdict.message}
            )
            continue
        key = phones.normalise(number) or number
        if key in seen:
            continue
        seen.add(key)
        refused = phone_numbers.refusal(user, number)
        if refused:
            report.problems.append(
                _("%(name)s: the number %(value)s was left out. %(why)s")
                % {"name": card.name[:80], "value": phone.number[:40], "why": refused}
            )
            continue
        row = PhoneNumber.objects.create(
            owner=user,
            holder=contact,
            number=number,
            kind=phone.kind if phone.kind in PhoneNumber.Kind.values else "",
        )
        made = made or row
        if phone.primary and wanted is None:
            wanted = row
    if wanted is not None:
        phone_numbers.set_primary(wanted)
    elif made is not None:
        phone_numbers.ensure_one_primary(contact)


def _postal(user, contact: Contact, card: vcard.Card, report: Report) -> None:
    wanted = None
    made = None
    for address in card.addresses:
        data = {
            "kind": address.kind,
            "street": address.street,
            "postcode": address.postcode,
            "municipality": address.municipality,
            "region": address.region,
            "country": address.country,
        }
        if not any(data[part] for part in ("street", "postcode", "municipality", "region")):
            continue
        instance = PostalAddress(owner=user, holder=contact)
        form = postal.PostalAddressForm(data=data, instance=instance, person=user)
        if not form.is_valid():
            report.problems.append(
                _("%(name)s: an address was left out. %(why)s")
                % {
                    "name": card.name[:80],
                    "why": " ".join(m for errors in form.errors.values() for m in errors),
                }
            )
            continue
        if PostalAddress.objects.filter(owner=user, comparable=instance.comparable_form()).exists():
            report.problems.append(
                _("%(name)s: an address was left out, because you list it already.")
                % {"name": card.name[:80]}
            )
            continue
        row = form.save()
        made = made or row
        if address.primary and wanted is None:
            wanted = row
    if wanted is not None:
        postal.make_primary(wanted)
    elif made is not None:
        postal.ensure_one_primary(contact)


def _kind_of(url: str, social: bool) -> str:
    if social:
        return web_links.Kind.SOCIAL
    for kind in (web_links.Kind.SOCIAL, web_links.Kind.REPOSITORY):
        if link_services.guess(url, kind) is not None:
            return kind
    return web_links.Kind.WEBSITE


def _links(user, contact: Contact, card: vcard.Card, report: Report) -> None:
    rows, seen = [], set()
    for link in card.links:
        try:
            addresses.web_address(link.url)
            checked = web_links.checked_rows(
                [{"kind": _kind_of(link.url, link.social), "url": link.url}]
            )
        except (ValueError, ValidationError) as error:
            why = " ".join(getattr(error, "messages", [str(error)]))
            report.problems.append(
                _("%(name)s: the link %(url)s was left out. %(why)s")
                % {"name": card.name[:80], "url": link.url[:80], "why": why}
            )
            continue
        key = (checked[0]["kind"], checked[0]["url"])
        if key not in seen:
            seen.add(key)
            rows.append(checked[0])
    if rows:
        web_links.add_links(contact, user, rows)


def _handles(user, contact: Contact, card: vcard.Card, report: Report) -> None:
    rows, seen = [], set()
    for uri in card.impps:
        read = vcard.impp_read(uri)
        if read is None:
            continue
        service, handle = read
        try:
            checked = messaging_handles.checked_rows([{"service": service, "handle": handle}])
        except ValidationError as error:
            report.problems.append(
                _("%(name)s: the messaging address %(value)s was left out. %(why)s")
                % {"name": card.name[:80], "value": uri[:80], "why": " ".join(error.messages)}
            )
            continue
        key = (checked[0]["service"], checked[0]["handle"])
        if key not in seen:
            seen.add(key)
            rows.append(checked[0])
    if rows:
        messaging_handles.add_handles(contact, user, rows)
