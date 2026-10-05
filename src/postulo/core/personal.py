"""The personal details nobody has to give: when and where somebody was born, and what
they are a citizen of (#679, #680).

**Optional, never worked out from anything, and never printed unless a CV says so.** They
are the most identifying things a profile can hold and exactly what an identity check asks
for, so every rule about them is written here once, and the form, the API, the candidate
file and the archive importer all hold a value to it. A column's validator is this
module's function; nothing else decides what a date of birth is.

**A date of birth is the text of an ISO 8601 reduced form**: ``1990``, ``1990-03`` or
``1990-03-12``. That is what the archive, the candidate file, the API and vCard (``BDAY``)
already speak, a person may know only the year, and nothing sorts or filters by it. It must
be a real date, not in the future and not before 1900. An age is never worked out, stored or
shown.

**A place of birth is the text typed and a country's code**, from the list the address form
offers. There is no lookup and no coordinate, and it is never offered to the map: a village
a gazetteer lacks is still where somebody was born.
"""

from __future__ import annotations

import datetime as dt
import re

from django.core.exceptions import ValidationError
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from . import country_sets, phones

#: The earliest year a date of birth may name.
EARLIEST_YEAR = 1900
#: ``1990``, ``1990-03``, ``1990-03-12``: every digit an ASCII one (``\d`` would take others).
_REDUCED = re.compile(r"(?P<year>[0-9]{4})(?:-(?P<month>[0-9]{2})(?:-(?P<day>[0-9]{2}))?)?")
#: The column's length: a whole date.
BIRTH_DATE_LENGTH = 10
BIRTH_PLACE_LENGTH = 120


def _not_a_date() -> ValidationError:
    return ValidationError(
        _("Give a year, a year and month, or a whole date, such as 1990, 1990-03 or 1990-03-12."),
        code="birth_date",
    )


def parse_birth_date(value: str) -> tuple[int, int | None, int | None]:
    """The year, the month and the day a date of birth says, the last two where it does not
    go so far. Raises `ValidationError` for anything that is not a real, past date."""
    text = value if isinstance(value, str) else ""
    found = _REDUCED.fullmatch(text)
    if found is None:
        raise _not_a_date()
    year = int(found["year"])
    month = int(found["month"]) if found["month"] else None
    day = int(found["day"]) if found["day"] else None
    try:
        dt.date(year, 1 if month is None else month, 1 if day is None else day)
    except ValueError:
        raise _not_a_date() from None
    if year < EARLIEST_YEAR:
        raise ValidationError(
            _("A date of birth is not before %(year)s."),
            code="too_early",
            params={"year": EARLIEST_YEAR},
        )
    today = timezone.localdate()
    if (year, month or 1, day or 1) > (today.year, today.month, today.day):
        raise ValidationError(_("A date of birth cannot be in the future."), code="future")
    return year, month, day


def validate_birth_date(value: str) -> None:
    """The column's validator: blank is nothing, anything else is a reduced ISO date."""
    if value:
        parse_birth_date(value)


def birth_date_from_parts(day: str, month: str, year: str) -> str:
    """The three boxes of the form as the text the column holds, or `ValidationError`.

    Nothing typed is nothing. A day needs a month and a month needs a year, because a date
    that leaves out the bigger part says nothing a reader could hold on to.
    """
    day, month, year = (part.strip() for part in (day, month, year))
    if not (day or month or year):
        return ""
    if not year or (day and not month):
        raise ValidationError(
            _("Give the year, and the month before the day: a year alone is enough."),
            code="birth_parts",
        )
    if not all(part.isascii() and part.isdigit() for part in (day, month, year) if part):
        raise _not_a_date()
    text = year.zfill(4)
    if month:
        text += f"-{month.zfill(2)}"
    if day:
        text += f"-{day.zfill(2)}"
    validate_birth_date(text)
    return text


def birth_parts(value: str) -> tuple[str, str, str]:
    """A stored date as the day, month and year boxes hold it; blank where it stops short."""
    found = _REDUCED.fullmatch(value or "")
    if found is None:
        return "", "", ""
    return (
        str(int(found["day"])) if found["day"] else "",
        str(int(found["month"])) if found["month"] else "",
        found["year"],
    )


def box_order() -> tuple[str, ...]:
    """Which of ``day``, ``month`` and ``year`` comes first, as the active language writes a date.

    Read from the language's short date format (``d/m/Y``, ``m/d/Y``, ``Y-m-d``), so one that
    writes the year first and one that writes the month first are drawn as they write it. A
    format that does not name all three falls back on day, month, year. The input formats are
    not asked: their first is whatever a parser takes first, and Portugal's is the ISO one.
    """
    pattern = str(formats.get_format("SHORT_DATE_FORMAT"))
    letters = {
        **dict.fromkeys("dj", "day"),
        **dict.fromkeys("mnMbFN", "month"),
        **dict.fromkeys("Yyo", "year"),
    }
    seen: list[str] = []
    for token in pattern:
        name = letters.get(token)
        if name and name not in seen:
            seen.append(name)
    return tuple(seen) if len(seen) == 3 else ("day", "month", "year")


def birth_date_text(value: str) -> str:
    """A date of birth as the active language writes it: ``12 March 1990``, ``March 1990``
    or ``1990``. Blank for a value that is not one."""
    try:
        year, month, day = parse_birth_date(value)
    except ValidationError:
        return ""
    if day:
        return formats.date_format(dt.date(year, month, day), "DATE_FORMAT")
    if month:
        return formats.date_format(dt.date(year, month, 1), "YEAR_MONTH_FORMAT")
    return str(year)


def validate_country_code(value: str) -> None:
    """A two-letter code from the list the address form offers, in capitals."""
    if value and value not in phones.BY_CODE:
        raise ValidationError(_("Choose a country from the list."), code="country")


def place_text(place: str, country: str) -> str:
    """A place of birth as it prints: the town as typed, then the country's English name,
    as `core.postal.location_line` writes where somebody is."""
    name = phones.BY_CODE[country][2] if country in phones.BY_CODE else ""
    return ", ".join(part for part in ((place or "").strip(), name) if part)


# ------------------------------------------------------------------ nationalities (#680)

#: How many a person may hold: nobody holds more, and a bound keeps a hand-written request
#: from storing a thousand.
MAX_NATIONALITIES = 10

#: What somebody says who would rather not list a country: only whether they are a citizen
#: of the EU, the EEA or Switzerland. The scope is the answer while the list is empty.
SCOPE_EU = "eu"
SCOPE_OTHER = "other"
SCOPE_CHOICES = (
    (SCOPE_EU, _("Citizen of an EU or EEA country, or of Switzerland")),
    (SCOPE_OTHER, _("Citizen of another country")),
)
SCOPES = frozenset(code for code, _label in SCOPE_CHOICES)


def clean_nationalities(value) -> list[str]:
    """A list of nationalities as it is held: known codes, in capitals, each once, in the
    order given, at most `MAX_NATIONALITIES`. Raises `ValidationError` for anything else.

    Blank (``None`` or an empty list) is an empty list. The one function every door calls: the
    form, the API, a candidate file and the archive importer.
    """
    if value in (None, "", [], ()):
        return []
    if not isinstance(value, list | tuple):
        raise ValidationError(_("Give nationalities as a list of country codes."), code="type")
    if len(value) > MAX_NATIONALITIES:
        raise ValidationError(
            _("Give at most %(most)s nationalities."),
            code="too_many",
            params={"most": MAX_NATIONALITIES},
        )
    kept: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValidationError(_("Give each nationality as a country code."), code="type")
        code = item.strip().upper()
        if not code:
            continue
        if code not in phones.BY_CODE:
            raise ValidationError(
                _("%(code)s is not a country in the list."),
                code="unknown",
                params={"code": item.strip()[:8]},
            )
        if code not in kept:
            kept.append(code)
    return kept


def validate_scope(value) -> None:
    """The scope's validator: blank, or one of the two answers."""
    if value and value not in SCOPES:
        raise ValidationError(_("Choose one of the answers in the list."), code="scope")


def validate_nationalities(value) -> None:
    """The column's validator: `clean_nationalities` finding nothing to refuse."""
    clean_nationalities(value)


def derived_scope(codes) -> str:
    """What the countries listed come to: the EU scope if any is one of the set, the other
    if there are countries and none is, and blank for an empty list."""
    held = [code for code in (codes or []) if isinstance(code, str)]
    if not held:
        return ""
    return SCOPE_EU if any(code in country_sets.EU_EEA_CH for code in held) else SCOPE_OTHER


def scope_text(scope: str) -> str:
    """The wording of a scope, or blank for none."""
    return str(dict(SCOPE_CHOICES).get(scope, ""))


def country_names(codes) -> list[str]:
    """The countries' English names, in the order held; a code the list lacks is left out."""
    return [phones.BY_CODE[code][2] for code in codes or [] if code in phones.BY_CODE]
