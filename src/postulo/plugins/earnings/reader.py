"""Reading the Eurostat file, and answering for a listing's occupation and country (#1257).

The file ships in this package's ``data/`` and is refreshed by ``manage.py fetch_earnings``
into `POSTULO_EARNINGS_DIR` (the package's ``data/`` unless an administrator says otherwise;
the data volume in the image). The one whose ``retrieved`` is the later is read, once per
process, so an instance works with no network and a refresh outlives an image pull. A file in
that directory that cannot be read is passed over for the shipped one, and the system check
says so.

**What is answered.** For a unit group, its major group's mean (the latest wave publishes no
single-group median), the median and deciles of the wider group that holds it, and the
national minimum wage; for no unit group, the figures for all employees. Each in the period
asked where the survey has that period (a year, a month, an hour); a listing paid by the day
gets the monthly and hourly figures under their own names, and one stating no pay the yearly
ones. In the currency asked where the figure was published in it, else in euro, as published:
nothing is converted. Only the latest wave is answered with; the earlier wave's medians stay in
the file and are not drawn beside it (#1257, D4).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from django.utils.text import format_lazy
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from postulo.core import configuration
from postulo.plugins.api import EarningsFigure

from . import eurostat

logger = logging.getLogger(__name__)

FILE = "eurostat-earnings.json"
SHIPPED_DIR = Path(__file__).resolve().parent / "data"


def data_dir() -> Path:
    """Where `fetch_earnings` writes and where a refreshed file is looked for."""
    return Path(configuration.read("POSTULO_EARNINGS_DIR"))


#: What each group of the file is called: as wide as it is, with its ISCO-08 codes, because
#: the grain is the major group and a name that sounded narrower would claim a precision the
#: survey does not have.
GROUP_NAMES = {
    "T": _("All employees"),
    "1-5": _("Non-manual workers (ISCO-08 major groups 1 to 5)"),
    "6-8": _("Skilled manual workers (ISCO-08 major groups 6 to 8)"),
    "7-9": _("Manual workers (ISCO-08 major groups 7 to 9)"),
    "1": _("Managers (ISCO-08 major group 1)"),
    "2": _("Professionals (ISCO-08 major group 2)"),
    "3": _("Technicians and associate professionals (ISCO-08 major group 3)"),
    "4": _("Clerical support workers (ISCO-08 major group 4)"),
    "5": _("Service and sales workers (ISCO-08 major group 5)"),
    "6": _("Skilled agricultural, forestry and fishery workers (ISCO-08 major group 6)"),
    "7": _("Craft and related trades workers (ISCO-08 major group 7)"),
    "8": _("Plant and machine operators and assemblers (ISCO-08 major group 8)"),
    "9": _("Elementary occupations (ISCO-08 major group 9)"),
}
MINIMUM_GROUP = _("National minimum wage")

#: The wider group a major group sits in, as Eurostat publishes the median for it.
WIDER = {"1": "1-5", "2": "1-5", "3": "1-5", "4": "1-5", "5": "1-5"}
WIDER.update({"6": "6-8", "7": "6-8", "8": "6-8", "9": "7-9"})

#: Where Eurostat states a wage paid in fourteen instalments a year as twelve: Portugal's
#: 920 is published as 1,073 (920 x 14 / 12), and Spain's and Greece's the same way.
FOURTEEN = frozenset({"PT", "ES", "GR"})
FOURTEEN_NOTE = _(
    "Paid in fourteen instalments a year; Eurostat states it spread over twelve months."
)

SCOPE = _(
    "Employees of enterprises with ten or more employees in industry, construction and "
    "services, without public administration; part-time pay brought to full time."
)
LICENCE = _(
    "CC BY 4.0, Eurostat. Figures unchanged. Data on countries outside the EU, EFTA and the "
    "candidate countries is not included, as Eurostat does not allow its commercial reuse."
)


def _read(path: Path) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None if eurostat.problems(document) else document


def candidates() -> list[tuple[Path, dict | None]]:
    """Each place the file may be, with what was read there (``None`` where it is not one)."""
    found = []
    for directory in dict.fromkeys((data_dir(), SHIPPED_DIR)):
        path = directory / FILE
        if path.is_file():
            found.append((path, _read(path)))
    return found


def _stamps() -> tuple:
    """What tells the files here from ones that replace them: each one's path, when it was
    written and its size, so a refresh is read by a running server without a restart."""
    found = []
    for directory in dict.fromkeys((data_dir(), SHIPPED_DIR)):
        try:
            status = (directory / FILE).stat()
        except OSError:
            continue
        found.append((str(directory / FILE), status.st_mtime_ns, status.st_size))
    return tuple(found)


def document() -> dict | None:
    """The file to answer from: the readable one retrieved last, or ``None`` where none is.

    Read once per process, and again only when a file here is written, replaced or removed."""
    return _document(_stamps())


@lru_cache(maxsize=1)
def _document(_stamps: tuple) -> dict | None:
    readable = [read for _path, read in candidates() if read is not None]
    if not readable:
        logger.warning("No readable %s; the earnings plugin has nothing to show", FILE)
        return None
    return max(readable, key=lambda read: dt.date.fromisoformat(read["retrieved"]))


def forget() -> None:
    """Read the file again on the next question, as after a refresh."""
    _document.cache_clear()


def countries() -> tuple[str, ...]:
    """The countries the file holds figures of the latest wave for."""
    found = document()
    if found is None:
        return ()
    wave = str(found["wave"])
    return tuple(
        code
        for code, entry in found["countries"].items()
        if any(
            wave in years
            for periods in entry["groups"].values()
            for measures in periods.values()
            for years in measures.values()
        )
    )


def citation() -> str:
    found = document() or {}
    codes = ", ".join(found.get("datasets", {}))
    return format_lazy(
        _(
            "Structure of Earnings Survey {wave} and minimum wages ({codes}), retrieved on "
            "{retrieved}."
        ),
        wave=found.get("wave", ""),
        codes=codes,
        retrieved=found.get("retrieved", ""),
    )


def _year_words(when: str) -> str:
    """A half-year as Eurostat writes it, ``2026-S2``, in words."""
    year, _sep, half = when.partition("-S")
    if half == "1":
        return gettext("first half of %(year)s") % {"year": year}
    if half == "2":
        return gettext("second half of %(year)s") % {"year": year}
    return when


def _pick(amounts: dict, currency: str) -> tuple[str, Decimal] | None:
    """The amount in ``currency`` where it was published in it, else the euro one."""
    for code in (currency, "EUR"):
        if code and code in amounts:
            return code, Decimal(str(amounts[code]))
    return None


def _periods(period: str) -> tuple[str, ...]:
    if period in eurostat.PERIODS:
        return (period,)
    if period == "day":
        return ("month", "hour")
    return ("year",)


def figures(isco_code: str, country: str, period: str = "", currency: str = ""):
    """The figures for ``isco_code``'s groups in ``country``; see the module."""
    found = document()
    entry = (found or {}).get("countries", {}).get(country)
    if entry is None:
        return []
    wave = str(found["wave"])
    major = isco_code[:1] if isco_code[:1].isdigit() and isco_code[:1] != "0" else ""
    if major:
        asked = [(major, ("mean",)), (WIDER[major], ("median", "p10", "p90"))]
    else:
        asked = [("T", ("mean", "median", "p10", "p90"))]
    answer = []
    for chosen in _periods(period):
        for group, measures in asked:
            published = entry["groups"].get(group, {}).get(chosen, {})
            for measure in measures:
                picked = _pick(published.get(measure, {}).get(wave, {}), currency)
                if picked is None:
                    continue
                code, amount = picked
                answer.append(
                    EarningsFigure(
                        measure=measure,
                        amount=amount,
                        currency=code,
                        period=chosen,
                        basis="gross",
                        year=wave,
                        group=str(GROUP_NAMES[group]),
                    )
                )
    minimum = entry.get("minimum")
    if answer and minimum:
        picked = _pick(minimum["month"], currency)
        if picked is not None:
            code, amount = picked
            answer.append(
                EarningsFigure(
                    measure="minimum",
                    amount=amount,
                    currency=code,
                    period="month",
                    basis="gross",
                    year=_year_words(minimum["when"]),
                    group=str(MINIMUM_GROUP),
                    note=str(FOURTEEN_NOTE) if country in FOURTEEN else "",
                )
            )
    return answer
