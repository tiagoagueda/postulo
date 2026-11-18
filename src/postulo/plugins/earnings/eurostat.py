"""Eurostat's earnings tables, read from JSON-stat and reduced to the file the plugin reads (#1257).

Pure functions and nothing of Postulo's: `fetch_earnings` hands these the answers it got, the
tests hand them recorded ones, and the reader validates the file it loads with `problems`.

**What is asked, and why these slices.** The Structure of Earnings Survey is four-yearly
(2018, 2022; 2026's results are due about 2028) and publishes occupation at the ISCO-08 major
group, one digit, not the unit group. For the latest wave, per major group, it publishes the
**mean** only (``earn_sesYY_28`` annual, ``_21`` monthly, ``_14`` hourly, in euro and in the
national currency); the **median and the first and ninth deciles** of that wave exist for the
three wider groups and for all employees, in the main cubes (``earn_ses_annual``,
``earn_ses_monthly``, ``earn_ses_hourly``, euro only). The main cubes are read for the wave
and the one before it, so the file keeps the earlier wave's single-group medians; the reader
draws only the latest wave (#1257, D4). The minimum wage is ``earn_mw_cur``, the latest
half-year.

**What is changed, and only that.** The slices named above are kept, every value unchanged;
Eurostat's ``EL`` is stored as ``GR``; aggregates (EU27_2020, EA20 ...) and every country
outside the EU, EFTA and the candidate countries are left out, because Eurostat's notice
forbids commercial reuse of data on those (the United Kingdom and the United States are the
two in these tables). Amounts are keyed by the ISO 4217 code they were published in: the
national-currency figure under the code the country used in the reference period (Croatia's
2022 figures are kuna), never converted.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Mapping

#: The version of the file's shape the reader knows.
FORMAT = 1

#: The wave the shipped file holds. A new wave is a run with ``--wave``.
WAVE = 2022

PUBLISHER = "Eurostat"
LICENCE = "CC BY 4.0"
DATABROWSER = "https://ec.europa.eu/eurostat/databrowser/view/{code}/default/table"
API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"

#: The countries whose data may be reused commercially: the EU, EFTA, and the acceding and
#: candidate countries (Eurostat's copyright notice). Eurostat's own codes, so Greece is EL.
ALLOWED = frozenset(
    "AT BE BG CY CZ DE DK EE EL ES FI FR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK "
    "IS LI NO CH "
    "AL BA GE MD ME MK RS TR UA".split()
)
#: Eurostat's code to ISO 3166-1, where they differ.
RENAMED = {"EL": "GR"}

#: The ``isco08`` codes, to the keys the file uses: the major group's digit, a wider group as
#: its range, and ``T`` for all employees.
GROUPS = {
    "TOTAL": "T",
    "OC1-5": "1-5",
    "OC6-8": "6-8",
    "OC7-9": "7-9",
    **{f"OC{digit}": str(digit) for digit in range(10)},
}
#: The main cubes' indicators, to the file's measures.
INDICATORS = {"MED_E_EUR": "median", "D1_E_EUR": "p10", "D9_E_EUR": "p90"}
PERIODS = ("year", "month", "hour")
MEASURES = ("mean", "median", "p10", "p90")


def detailed(wave: int) -> dict[str, str]:
    """The tables of a wave's means per group, by the period each is for."""
    short = f"{wave % 100:02d}"
    return {
        "year": f"earn_ses{short}_28",
        "month": f"earn_ses{short}_21",
        "hour": f"earn_ses{short}_14",
    }


#: The main cubes, by period, with the working time each is read at: annual and monthly
#: earnings with part-time pay brought to full time, as the detailed tables' means are; hourly
#: for all employees.
MAIN = {
    "year": ("earn_ses_annual", "TOT_FTE"),
    "month": ("earn_ses_monthly", "TOT_FTE"),
    "hour": ("earn_ses_hourly", "TOTAL"),
}
MINIMUM = "earn_mw_cur"


def queries(wave: int) -> dict[str, str]:
    """Every table asked, with the query string that asks for the slice kept: seven GETs."""
    common = "format=JSON&lang=en&sex=T&age=TOTAL"
    found = dict.fromkeys(
        detailed(wave).values(),
        f"{common}&indic_se=ERN&unit=EUR&unit=NAC&sizeclas=GE10&time={wave}",
    )
    indicators = "".join(f"&indic_se={name}" for name in INDICATORS)
    for code, worktime in MAIN.values():
        found[code] = (
            f"{common}&nace_r2=B-S_X_O{indicators}&worktime={worktime}&time={wave - 4}&time={wave}"
        )
    found[MINIMUM] = "format=JSON&lang=en&currency=EUR&currency=NAC&lastTimePeriod=1"
    return found


class Refused(ValueError):
    """An answer, or a file, that is not what the reader can use."""


def cells(document: Mapping) -> Iterator[tuple[dict[str, str], object]]:
    """Every value of a JSON-stat 2.0 dataset, as ``({dimension: code}, value)``.

    The values are a row-major array over ``size``, the last dimension varying fastest;
    Eurostat sends them as an object keyed by position, leaving out the empty cells.
    """
    try:
        ids = list(document["id"])
        sizes = [int(size) for size in document["size"]]
        codes = []
        for name in ids:
            index = document["dimension"][name]["category"]["index"]
            codes.append(sorted(index, key=index.get) if isinstance(index, dict) else list(index))
        values = document["value"]
    except (KeyError, TypeError, ValueError) as error:
        raise Refused(f"not a JSON-stat dataset: {error!r}") from error
    if len(ids) != len(sizes) or any(len(c) != s for c, s in zip(codes, sizes, strict=True)):
        raise Refused("the dimensions and their sizes disagree")
    items = values.items() if isinstance(values, dict) else enumerate(values)
    for position, value in items:
        if value is None:
            continue
        rest = int(position)
        key: dict[str, str] = {}
        for name, size, ordered in zip(
            reversed(ids), reversed(sizes), reversed(codes), strict=True
        ):
            rest, at = divmod(rest, size)
            key[name] = ordered[at]
        yield key, value


def _amount(value) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise Refused(f"{value!r} is not a positive amount")
    return value


def _national(country: str, day: dt.date) -> str:
    """The currency ``country`` used on ``day``, from CLDR; ``""`` where it says none."""
    from babel.numbers import get_territory_currencies

    found = get_territory_currencies(RENAMED.get(country, country), day, tender=True)
    return found[0] if found else ""


def build(answers: Mapping[str, Mapping], wave: int, retrieved: dt.date) -> dict:
    """The file, from the seven answers `queries` asks for, keyed by table code."""
    tables = detailed(wave)
    missing = [code for code in queries(wave) if code not in answers]
    if missing:
        raise Refused(f"no answer for {', '.join(missing)}")
    countries: dict[str, dict] = {}

    def place(geo: str) -> dict | None:
        if geo not in ALLOWED:
            return None
        return countries.setdefault(RENAMED.get(geo, geo), {"groups": {}})

    reference = dt.date(wave, 10, 1)  # the survey's reference month, October for most
    for period, code in tables.items():
        for key, value in cells(answers[code]):
            entry = place(key["geo"])
            group = GROUPS.get(key.get("isco08", ""))
            if entry is None or group is None or key.get("time") != str(wave):
                continue
            unit = key.get("unit")
            if unit == "EUR":
                currency = "EUR"
            elif unit == "NAC":
                currency = _national(key["geo"], reference)
                if not currency or currency == "EUR":
                    continue  # the euro figure again, or a currency nobody can name
            else:
                continue
            slot = entry["groups"].setdefault(group, {}).setdefault(period, {})
            slot.setdefault("mean", {}).setdefault(str(wave), {})[currency] = _amount(value)
    for period, (code, _worktime) in MAIN.items():
        for key, value in cells(answers[code]):
            entry = place(key["geo"])
            group = GROUPS.get(key.get("isco08", ""))
            measure = INDICATORS.get(key.get("indic_se", ""))
            if entry is None or group is None or measure is None:
                continue
            slot = entry["groups"].setdefault(group, {}).setdefault(period, {})
            slot.setdefault(measure, {}).setdefault(key["time"], {})["EUR"] = _amount(value)
    when = ""
    for key, value in cells(answers[MINIMUM]):
        entry = place(key["geo"])
        if entry is None:
            continue
        when = key["time"]
        currency = "EUR"
        if key.get("currency") == "NAC":
            year, _sep, half = when.partition("-S")
            start = dt.date(int(year), 7 if half == "2" else 1, 1)
            currency = _national(key["geo"], start)
        elif key.get("currency") != "EUR":
            continue
        if currency:
            entry.setdefault("minimum", {"when": when, "month": {}})["month"][currency] = _amount(
                value
            )
    for entry in countries.values():
        month = entry.get("minimum", {}).get("month", {})
        # A national figure equal to the euro one is the euro: Bulgaria's 2026 wage is
        # published as 620 in both, and the CLDR Babel carries still says lev.
        for currency in [c for c in month if c != "EUR"]:
            if month.get("EUR") == month[currency]:
                del month[currency]
    countries = {code: entry for code, entry in countries.items() if entry["groups"]}
    used = [*tables.values(), *(code for code, _w in MAIN.values()), MINIMUM]
    document = {
        "format": FORMAT,
        "retrieved": retrieved.isoformat(),
        "publisher": PUBLISHER,
        "licence": LICENCE,
        "wave": wave,
        "minimum_when": when,
        "datasets": {
            code: {
                "updated": str(answers[code].get("updated", "")),
                "url": DATABROWSER.format(code=code),
            }
            for code in used
        },
        "countries": dict(sorted(countries.items())),
    }
    found = problems(document)
    if found:
        raise Refused("; ".join(found[:5]))
    return document


#: Fewer countries with a mean of the wave than this, and the answer is not the survey.
FEWEST_COUNTRIES = 20


def problems(document) -> list[str]:
    """Why ``document`` is not a file the reader can use; empty where it is."""
    if not isinstance(document, dict):
        return ["the file is not a JSON object"]
    found = []
    if document.get("format") != FORMAT:
        found.append(f"its format is {document.get('format')!r}, not {FORMAT}")
    try:
        dt.date.fromisoformat(str(document.get("retrieved")))
    except ValueError:
        found.append("it does not say when it was retrieved")
    wave = document.get("wave")
    if not isinstance(wave, int) or not 2000 < wave < 2100:
        found.append("it does not say which wave it holds")
    countries = document.get("countries")
    if not isinstance(countries, dict):
        return [*found, "it holds no countries"]
    allowed = {RENAMED.get(code, code) for code in ALLOWED}
    with_wave = 0
    for country, entry in countries.items():
        if country not in allowed:
            found.append(f"{country} is not a country whose data may be reused")
            continue
        groups = entry.get("groups") if isinstance(entry, dict) else None
        if not isinstance(groups, dict) or not groups:
            found.append(f"{country} has no groups")
            continue
        for group, periods in groups.items():
            if group not in GROUPS.values() or not isinstance(periods, dict):
                found.append(f"{country}: {group!r} is not a group")
                continue
            for period, measures in periods.items():
                if period not in PERIODS or not isinstance(measures, dict):
                    found.append(f"{country} {group}: {period!r} is not a period")
                    continue
                for measure, years in measures.items():
                    if measure not in MEASURES or not isinstance(years, dict):
                        found.append(f"{country} {group}: {measure!r} is not a measure")
                        continue
                    for amounts in years.values():
                        if not isinstance(amounts, dict) or not amounts:
                            found.append(f"{country} {group} {measure}: no amounts")
                            continue
                        for currency, amount in amounts.items():
                            if not (len(currency) == 3 and currency.isupper()):
                                found.append(f"{country}: {currency!r} is not a currency")
                            if isinstance(amount, bool) or not (
                                isinstance(amount, int | float) and amount > 0
                            ):
                                found.append(f"{country}: {amount!r} is not an amount")
        if any(
            str(wave) in periods.get("year", {}).get("mean", {})
            for periods in groups.values()
            if isinstance(periods, dict)
        ):
            with_wave += 1
        minimum = entry.get("minimum")
        if minimum is not None and not (
            isinstance(minimum, dict)
            and isinstance(minimum.get("month"), dict)
            and all(
                isinstance(a, int | float) and not isinstance(a, bool) and a > 0
                for a in minimum["month"].values()
            )
        ):
            found.append(f"{country}: its minimum wage is not an amount")
    if with_wave < FEWEST_COUNTRIES:
        found.append(
            f"only {with_wave} countries have a yearly mean for {wave}; "
            f"the survey has at least {FEWEST_COUNTRIES}"
        )
    return found
