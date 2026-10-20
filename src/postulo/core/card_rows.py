"""A holder's telephone numbers, postal addresses and web links as plain values (#328).

What a plugin on the surface reads: no model, no manager, nothing to save by accident. Each
row is the kind, the name the person gave it when none of the kinds fits, the value, and
whether it is the holder's primary. **Read through the person's switches**: with *Several
telephone numbers* off, a holder's numbers are its primary alone, and a link kind whose
feature is off shows its primary alone -- the rows that are kept and not shown stay where
they are, and are neither read nor written here (`phone_numbers.numbers_for`,
`web_links.links_for`).

Identifiers are not here, and messaging handles are not: RFC 6350 has no property for a
registry number or a Wikidata id, and a handle is a plugin's own business (#328).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import phone_numbers, postal, web_links
from .models import PhoneNumber, PostalAddress

#: The kinds a number and an address may be, for a plugin to name a row's ``kind`` by.
PhoneNumberKind = PhoneNumber.Kind
PostalAddressKind = PostalAddress.Kind


@dataclass(frozen=True)
class PhoneRow:
    """One telephone number. ``kind`` is a `PhoneNumberKind` value, or blank."""

    number: str
    kind: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass(frozen=True)
class AddressRow:
    """One postal address. ``kind`` is a `PostalAddressKind` value, or blank; ``country`` an
    ISO 3166-1 alpha-2 code."""

    street: str = ""
    postcode: str = ""
    municipality: str = ""
    region: str = ""
    country: str = ""
    kind: str = ""
    label: str = ""
    is_primary: bool = False
    on_map: bool = False


@dataclass(frozen=True)
class LinkRow:
    """One address on the web. ``kind`` is a `LinkKind` value; ``service`` the key of the
    service it is on (`link_service_of`), blank for *Other*."""

    url: str
    kind: str = "website"
    service: str = ""
    label: str = ""
    is_primary: bool = False


def numbers_for(holder, person) -> tuple[PhoneRow, ...]:
    """``holder``'s numbers as ``person`` is shown them: all, or the primary alone."""
    return tuple(
        PhoneRow(row.number, row.kind, row.label, row.is_primary)
        for row in phone_numbers.numbers_for(holder, person)
    )


def addresses_for(holder, person=None) -> tuple[AddressRow, ...]:
    """``holder``'s postal addresses, the primary first. ``person`` is accepted so the call
    reads like its neighbours; there is no switch for several addresses."""
    return tuple(
        AddressRow(
            row.street,
            row.postcode,
            row.municipality,
            row.region,
            row.country,
            row.kind,
            row.label,
            row.is_primary,
            row.on_map,
        )
        for row in postal.for_holder(holder)
    )


def links_for(holder, person, kind: str | None = None) -> tuple[LinkRow, ...]:
    """``holder``'s web links as ``person`` is shown them: per kind, all or the primary."""
    return tuple(
        LinkRow(row.url, row.kind, row.service, row.label, row.is_primary)
        for row in web_links.links_for(holder, person, kind)
    )
