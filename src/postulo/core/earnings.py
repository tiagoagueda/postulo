"""Published earnings statistics for an occupation and a country, and who may be asked (#1257).

A statistics office publishes what employees earned: a mean or a median, for a group of
occupations, in a country, for a year. Postulo holds a listing's occupation (its ISCO-08 unit
group) and can place it in a country, so the figure that describes that group can be drawn
beside the listing. Which figures exist, at which grain and under which licence, differs by
publisher and by country, and none of it is core's to carry. So the figures are a registry,
supplied by `earnings-source` plugins the way a field's suggestions are
(`core.suggestion_sources`).

A source says what it knows about itself:

- the **countries** it can answer for, as ISO 3166-1 alpha-2 codes;
- whether it is **offline**: figures read from a file that ships with it, which a page may ask
  on every view; or needs the **network**, on the ``hosts`` it names;
- the **licence** its data is under, the **citation** its publisher asks for, the **scope** of
  the survey in one sentence, and ``figures``, the function that answers.

**A source hands over plain values, and Postulo draws them.** A `Figure` is an amount with
everything that says what it is: the measure (a mean, a median, a decile, a minimum), gross or
net, the period, the currency it was published in, the year, and the group it describes in the
source's own words. Postulo converts nothing: not a currency, not a month into a year. It
computes no difference with anybody's salary and recommends no figure.

**Only offline sources are asked by a page.** A source that calls a service names its hosts and
is never asked on a page load (#879, #1187); this module refuses one that names none, and
`references` never asks one. There is no press that asks one yet, so such a source is not shown.

**With no source enabled nothing is drawn.** That is the state of an instance until an
administrator enables one.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from . import modules

logger = logging.getLogger(__name__)

#: The kind of plugin that supplies sources.
KIND = "earnings-source"

#: How long a source's key may be.
MAX_KEY_LENGTH = 40

#: What a figure may measure. ``p10`` is the first decile (one in ten earned less), ``p90`` the
#: ninth; ``minimum`` is a statutory minimum wage, which is a rule and not a statistic.
MEASURES = ("mean", "median", "p10", "p25", "p75", "p90", "minimum")
#: The periods a figure may be published for. Never brought from one to another.
PERIODS = ("year", "month", "hour")
#: Whether a figure is before or after tax and contributions.
BASES = ("gross", "net")

_COUNTRY = re.compile(r"[A-Z]{2}")
_CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class Figure:
    """One published amount, with everything that says what it is."""

    #: What is measured; one of `MEASURES`.
    measure: str
    #: The amount as published, unchanged.
    amount: Decimal
    #: The ISO 4217 code the amount was published in, for its reference year.
    currency: str
    #: The period it is for; one of `PERIODS`.
    period: str
    #: ``"gross"`` or ``"net"``.
    basis: str
    #: When it describes, as it should be read: ``"2022"``, or the source's words for a half-year.
    year: str
    #: The population it describes, in the source's own words, wide enough to say how wide it
    #: is: ``"Professionals (ISCO-08 major group 2)"``.
    group: str
    #: True only where the group is the unit group that was asked about, and not a wider one.
    exact: bool = False
    #: One sentence the figure needs to be read right, or nothing.
    note: str = ""


@dataclass(frozen=True)
class Source:
    """One publisher's figures.

    ``figures(isco_code, country, period="", currency="")`` returns the `Figure`s that
    describe the group of occupations ``isco_code`` (an ISCO-08 unit group, four digits, or
    ``""`` where the listing has none) belongs to in ``country``, in the order they should be
    read. ``period`` and ``currency`` are the listing's or the offer's where it states a
    salary, and hints only: a source answers with the statistic published for that period and
    in that currency where it has one, and otherwise with what it has, never a conversion.
    """

    #: The source's own key, unique across the sources installed.
    key: str
    #: The publisher's name, as it is cited.
    label: str
    #: The countries it can answer for, ISO 3166-1 alpha-2, upper case.
    countries: tuple[str, ...]
    #: Whether it answers from files it ships with, with no network.
    offline: bool
    #: The licence its data is under.
    licence: str
    #: The function that answers; see the class.
    figures: Callable[..., Sequence[Figure]]
    #: What the survey covers, in one sentence of the source's own.
    scope: str = ""
    #: The citation the publisher asks for: the survey, the data codes, the day it was read.
    citation: str = ""
    #: The dataset's page, ``https`` only.
    url: str = ""
    #: The hosts a network source asks, and the only ones it may.
    hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reference:
    """What one source answered: the source, to cite, and its figures."""

    source: Source
    figures: tuple[Figure, ...]


def source_problems(source) -> list[str]:
    """Why what a plugin handed over is not a source this module can ask; empty where it is."""
    if not isinstance(source, Source):
        return [f"{source!r} is not an EarningsSource"]
    problems = []
    if not source.key or len(str(source.key)) > MAX_KEY_LENGTH:
        problems.append(f"its key is empty or longer than {MAX_KEY_LENGTH} characters")
    if not str(source.label):
        problems.append("it has no label")
    if not str(source.licence):
        problems.append("it names no licence")
    if not callable(source.figures):
        problems.append("its figures is not a function")
    countries = source.countries if isinstance(source.countries, tuple) else ()
    if not countries or not all(
        isinstance(code, str) and _COUNTRY.fullmatch(code) for code in countries
    ):
        problems.append("its countries are not a tuple of two-letter upper-case codes")
    # A source that dials out says where, and one that does not has nothing to say.
    if bool(source.hosts) == bool(source.offline):
        problems.append("it must be offline or name its hosts, and not both")
    if source.url and not str(source.url).startswith("https://"):
        problems.append("its url is not an https address")
    return problems


def figure_problems(figure) -> list[str]:
    """Why a figure cannot be drawn; empty where it can.

    A figure that does not say what it is is not drawn at all: a number with no measure, no
    period or no group would be read as something it is not.
    """
    if not isinstance(figure, Figure):
        return [f"{figure!r} is not an EarningsFigure"]
    problems = []
    if figure.measure not in MEASURES:
        problems.append(f"its measure is {figure.measure!r}, not one of {MEASURES}")
    if figure.period not in PERIODS:
        problems.append(f"its period is {figure.period!r}, not one of {PERIODS}")
    if figure.basis not in BASES:
        problems.append(f"its basis is {figure.basis!r}, not one of {BASES}")
    if not isinstance(figure.amount, Decimal) or not figure.amount.is_finite():
        problems.append(f"its amount is {figure.amount!r}, not a Decimal")
    elif figure.amount <= 0:
        problems.append(f"its amount is {figure.amount}, which is not above zero")
    if not isinstance(figure.currency, str) or not _CURRENCY.fullmatch(figure.currency):
        problems.append(f"its currency is {figure.currency!r}, not three upper-case letters")
    if not str(figure.year).strip():
        problems.append("it does not say which year it describes")
    if not str(figure.group).strip():
        problems.append("it does not name the group it describes")
    if not isinstance(figure.exact, bool):
        problems.append("its exact is not True or False")
    return problems


def _usable(source) -> bool:
    """Whether what a plugin handed over is a source this module can ask."""
    try:
        return not source_problems(source)
    except Exception:  # pragma: no cover - a hostile object
        return False


def sources(user=None) -> list[Source]:
    """Every usable source of the enabled plugins.

    With a ``user``, the plugins switched off for that person are left out; a bundled source
    is on or off as an administrator says (`plugins.policy`). Postulo's own come first, then
    others in the order they were installed; the first to claim a key keeps it and a second is
    logged and left out.
    """
    from postulo.plugins import registry as plugin_registry

    shipped = tuple(plugin_registry.builtins().get(KIND, ()))
    found: dict[str, Source] = {}
    installed = plugin_registry.plugins(KIND)
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        if user is not None and not modules.is_on(user, plugin.name):
            continue
        try:
            offered = list(getattr(plugin, "sources", ()) or ())
        except Exception:
            logger.exception("Plugin %r's earnings sources could not be read", plugin)
            continue
        for source in offered:
            if not _usable(source):
                logger.warning(
                    "Plugin %r offered an earnings source that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    source,
                )
            elif source.key in found:
                logger.warning(
                    "Earnings source %r was offered twice; the first one keeps it", source.key
                )
            else:
                found[source.key] = source
    return list(found.values())


def references(
    isco_code: str, country: str, *, user=None, period: str = "", currency: str = ""
) -> list[Reference]:
    """What every enabled offline source publishes for an occupation's group in a country.

    One `Reference` per source that has something, in source order. A source that raises is
    logged and left out, and so is a figure that does not say what it is, so a broken plugin
    makes a page without the card and never a broken page or a misleading number.
    """
    country = (country or "").strip().upper()
    if not _COUNTRY.fullmatch(country):
        return []
    found = []
    for source in sources(user):
        if not source.offline or country not in source.countries:
            continue
        try:
            answer = list(
                source.figures(isco_code or "", country, period=period, currency=currency)
            )
        except Exception:
            logger.exception("Earnings source %r failed for %r", source.key, country)
            continue
        kept = []
        for figure in answer:
            problems = figure_problems(figure)
            if problems:
                logger.warning(
                    "Earnings source %r gave a figure that cannot be drawn: %s",
                    source.key,
                    "; ".join(problems),
                )
            else:
                kept.append(figure)
        if kept:
            found.append(Reference(source=source, figures=tuple(kept)))
    return found
