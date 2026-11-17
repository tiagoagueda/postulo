"""The published earnings statistics that describe a listing's occupation and country (#1257).

A listing has an ISCO-08 unit group, worked out from its title, and a place. A statistics
office publishes what employees of a group of occupations earned in a country. This lays the
second beside the first, with no model and nothing fetched: the enabled `earnings-source`
plugins are asked (`postulo.core.earnings`) and their answers are put into words here.

**What the card may say, and what it may not.** Every figure is drawn with its measure (a
mean, a median, a decile, a minimum wage), gross or net, its period, its year, the group of
occupations it describes and the country, and each source with its publisher, its citation
and its licence. Nothing here converts a currency or brings a month to a year, nothing
compares a figure with the listing's salary or with an offer, and nothing recommends one:
`Offer` promises "no score and no recommendation", and a statistic about a tenth of a
country's employees is not a verdict on one job.

**Nothing is drawn where nothing is known.** No source enabled, no country to be read from
the listing's place, or no figure for that country: `panel` answers ``None`` and the page
has no card. A listing whose title matches no group of occupations gets the figures a source
has for all employees, and one sentence saying so.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils.formats import number_format
from django.utils.translation import gettext_lazy as _

from postulo.core import earnings, place_display

from . import places
from .models import split_location

#: What each measure is called on the page. A measure is always said: a mean and a median of
#: the same earnings are different numbers, and a bare amount would be read as either.
MEASURES = {
    "mean": _("Mean (the average)"),
    "median": _("Median (half earned less, half more)"),
    "p10": _("One in ten earned less than"),
    "p25": _("One in four earned less than"),
    "p75": _("One in four earned more than"),
    "p90": _("One in ten earned more than"),
    "minimum": _("Minimum wage"),
}

#: The amount with everything that belongs in the same breath: its currency, its period,
#: gross or net, and the year. One pattern per period and basis, so a catalogue sets the
#: order and the case itself (#392).
VALUES = {
    ("year", "gross"): _("%(amount)s %(currency)s per year, gross (%(year)s)"),
    ("month", "gross"): _("%(amount)s %(currency)s per month, gross (%(year)s)"),
    ("hour", "gross"): _("%(amount)s %(currency)s per hour, gross (%(year)s)"),
    ("year", "net"): _("%(amount)s %(currency)s per year, net (%(year)s)"),
    ("month", "net"): _("%(amount)s %(currency)s per month, net (%(year)s)"),
    ("hour", "net"): _("%(amount)s %(currency)s per hour, net (%(year)s)"),
}


@dataclass(frozen=True)
class Row:
    """One figure, in words: what is measured, and the amount with what it is."""

    label: str
    value: str
    note: str


@dataclass(frozen=True)
class Group:
    """The figures that describe one population, under its name."""

    name: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Block:
    """One source's figures, with what cites it."""

    key: str
    label: str
    citation: str
    licence: str
    scope: str
    url: str
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class EarningsPanel:
    """What the card draws: the country, and each source's figures."""

    country: str
    country_name: str
    #: Whether the listing's title matches a group of occupations. Where it does not, the
    #: figures are for all employees and the card says so.
    matched: bool
    blocks: tuple[Block, ...]


def country_of(posting) -> str:
    """The country a listing is in, as an ISO 3166-1 alpha-2 code, or ``""``.

    Read from the place the listing states, the way the map reads one, and as the name of a
    country where it names no town. A listing that states no place is where its company's
    address is. A place that is not one ("Remote", a town the table does not know) gives no
    country, and the company's address is not read in its stead: the listing said where it
    is, and it is not there.
    """
    text = " ".join((posting.location or "").split())
    if not text:
        return posting.company.location_parts()[1]
    return split_location(text)[1] or places.country_named(text)


def _amount(value: Decimal) -> str:
    """The amount as published, grouped for the reader: whole where it is whole."""
    whole = value == value.to_integral_value()
    return number_format(value, decimal_pos=0 if whole else 2, use_l10n=True, force_grouping=True)


def _row(figure: earnings.Figure) -> Row:
    value = VALUES[(figure.period, figure.basis)] % {
        "amount": _amount(figure.amount),
        "currency": figure.currency,
        "year": figure.year,
    }
    return Row(label=str(MEASURES[figure.measure]), value=str(value), note=str(figure.note))


def _groups(figures) -> tuple[Group, ...]:
    """The figures under the name of the group each describes, in the order they came."""
    gathered: dict[str, list[Row]] = {}
    for figure in figures:
        gathered.setdefault(str(figure.group), []).append(_row(figure))
    return tuple(Group(name=name, rows=tuple(rows)) for name, rows in gathered.items())


def panel(posting, user, *, stated: tuple[str, str] | None = None) -> EarningsPanel | None:
    """What the enabled sources publish for ``posting``'s occupation and country, or ``None``.

    ``stated`` is the period and the currency of the pay the page states, where that is not
    the listing's own salary (an offer's, on the application's page). They choose among the
    statistics a source has; nothing is converted to them. The sources are those on for
    ``user``, the reader, whoever owns the listing (#896).
    """
    if not earnings.sources(user):
        return None
    country = country_of(posting)
    if not country:
        return None
    period, currency = stated if stated is not None else stated_salary(posting)
    found = earnings.references(
        posting.isco_code, country, user=user, period=period, currency=currency
    )
    if not found:
        return None
    return EarningsPanel(
        country=country,
        country_name=place_display.country_name(country),
        matched=bool(posting.isco_code),
        blocks=tuple(
            Block(
                key=reference.source.key,
                label=str(reference.source.label),
                citation=str(reference.source.citation),
                licence=str(reference.source.licence),
                scope=str(reference.source.scope),
                url=str(reference.source.url),
                groups=_groups(reference.figures),
            )
            for reference in found
        ),
    )


def stated_salary(posting) -> tuple[str, str]:
    """The period and currency of the salary a listing states; two empty strings where it
    states none, whatever its fields default to."""
    if posting.salary_min is None and posting.salary_max is None:
        return "", ""
    return posting.salary_period or "", posting.salary_currency or ""


def stated_offer(offers) -> tuple[str, str] | None:
    """The period and currency of the latest of ``offers`` (newest first), where it states
    base pay; ``None`` where there is no offer or its pay is not written down, and the
    listing's own salary is what the page states."""
    latest = next(iter(offers), None)
    if latest is None or latest.base_amount is None:
        return None
    return latest.period or "", latest.currency or ""
