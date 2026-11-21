"""One CSV of a LinkedIn archive, read by its header and never by position (#1260).

LinkedIn publishes no column specification and renames files and columns from time to time,
so a sheet is read by the names in its header row, case-insensitively, and the header row is
found by those names: whatever comes before it (the *Notes:* preamble of some files) is left
alone. A column this reader does not know is named on the review, never guessed at.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field

#: The most rows one sheet is read for.
MAX_ROWS = 5000

#: How far down the header row is looked for.
HEADER_WITHIN = 10

MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_YEAR = re.compile(r"(\d{4})")
_MONTH_YEAR = re.compile(r"([A-Za-z]+)\.? (\d{4})")
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2}) ([A-Za-z]+)\.? (\d{4})")
_ISO = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")


class Unreadable(Exception):
    """The sheet could not be read at all; the message is a key the reader puts into words."""


@dataclass
class Sheet:
    """A sheet's rows by column name (in lower case), and the columns nobody reads."""

    rows: list[dict[str, str]] = field(default_factory=list)
    #: Header names that are not among the expected ones, as the file wrote them.
    unknown: list[str] = field(default_factory=list)
    #: Whether there were more than `MAX_ROWS` rows.
    cut_short: bool = False


def _cells(row: list[str]) -> list[str]:
    return [cell.strip().lstrip("﻿").strip() for cell in row]


def header_matches(row: list[str], expected: frozenset[str], required: frozenset[str]) -> bool:
    """Whether a row is this sheet's header: it holds every required name, in any case."""
    names = {cell.lower() for cell in _cells(row) if cell}
    return bool(names) and required <= names and len(names & expected) >= max(1, len(names) // 2)


def text_of(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise Unreadable("encoding") from error


def read(data: bytes, expected: frozenset[str], required: frozenset[str]) -> Sheet:
    """The sheet's rows, by the expected header's names. Raises `Unreadable`."""
    reader = csv.reader(io.StringIO(text_of(data), newline=""))
    sheet = Sheet()
    columns: list[str] | None = None
    try:
        for place, row in enumerate(reader):
            if columns is None:
                if place >= HEADER_WITHIN:
                    break
                if header_matches(row, expected, required):
                    columns = [cell.lower() for cell in _cells(row)]
                    sheet.unknown = [
                        cell for cell in _cells(row) if cell and cell.lower() not in expected
                    ]
                continue
            if not any(cell.strip() for cell in row):
                continue
            if len(sheet.rows) >= MAX_ROWS:
                sheet.cut_short = True
                break
            sheet.rows.append(
                {
                    # A cell's own line breaks, as text keeps them.
                    name: value.replace("\r\n", "\n").strip()
                    for name, value in zip(columns, row, strict=False)
                    if name and name in expected
                }
            )
    except csv.Error as error:
        raise Unreadable("csv") from error
    if columns is None:
        raise Unreadable("header")
    return sheet


def date_of(text: str) -> tuple[dt.date | None, bool]:
    """A date as the archive writes one, and whether it could be read.

    ``Mar 2021``, ``March 2021``, ``2016``, ``05 Jan 2023`` and ISO 8601, with English month
    names. A missing month or day is the first, as a date typed as a year means it; anything
    else is no date, and ``False`` says so, so the review can name it.
    """
    said = " ".join((text or "").split())
    if not said:
        return None, True
    try:
        if found := _YEAR.fullmatch(said):
            return dt.date(int(found[1]), 1, 1), True
        if found := _MONTH_YEAR.fullmatch(said):
            month = MONTHS.get(found[1].lower())
            if month:
                return dt.date(int(found[2]), month, 1), True
        if found := _DAY_MONTH_YEAR.fullmatch(said):
            month = MONTHS.get(found[2].lower())
            if month:
                return dt.date(int(found[3]), month, int(found[1])), True
        if found := _ISO.fullmatch(said):
            return dt.date(int(found[1]), int(found[2]), int(found[3] or 1)), True
    except ValueError:
        pass
    return None, False


def reduced_of(text: str) -> tuple[str, bool]:
    """A date as a publication keeps it, as text: ``2021``, ``2021-03`` or ``2021-03-05``."""
    said = " ".join((text or "").split())
    if not said:
        return "", True
    day, read = date_of(said)
    if day is None:
        return "", read
    if _YEAR.fullmatch(said):
        return f"{day.year:04d}", True
    if _MONTH_YEAR.fullmatch(said) or (_ISO.fullmatch(said) and len(said) == 7):
        return f"{day.year:04d}-{day.month:02d}", True
    return day.isoformat(), True
