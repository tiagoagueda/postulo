"""What both Europass readers share: the levels, the dates, the language, the XML walk.

Europass has written two XML formats, and the plugin reads both: `reader` the older
``LearnerInfo`` and the JSON, `candidate` the ``Candidate`` europass.europa.eu writes today.
The second reads with the first one's helpers, and the first decides which format a file is
-- so the helpers were in `reader`, `candidate` imported `reader`, and `reader` imported
`candidate` back from inside a function (#248). They are here, below both, and nothing about
reading a file changed.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import textwrap

from django.core.exceptions import ValidationError
from django.utils import translation

from postulo.accounts import identifiers
from postulo.plugins.api import is_language_tag, language_match, language_tag

#: CEFR levels as Europass writes them, mapped onto Postulo's own.
CEFR = {"A1": "a1", "A2": "a2", "B1": "b1", "B2": "b2", "C1": "c1", "C2": "c2"}

#: The five skills Europass records separately for each foreign language.
CEFR_PARTS = ("Listening", "Reading", "SpokenInteraction", "SpokenProduction", "Writing")

_ORDER = list(CEFR.values())

#: The two codes Cedefop's Europass wrote from the European Union's own list rather than
#: ISO 3166-1: the United Kingdom as ``UK`` and Greece as ``EL``.
_EU_COUNTRY_CODES = {"UK": "GB", "EL": "GR"}


def _country(value: str) -> str:
    """An ISO 3166-1 alpha-2 code, or nothing.

    The official list writes a country as a Publications Office URI ending in an alpha-3
    code; files seen since write ``pt`` or ``PT``; the old Europass wrote ``UK`` and ``EL``.
    Only the two-letter form is taken: cutting ``PRT`` to two letters would make it Puerto
    Rico.
    """
    code = value.strip().rsplit("/", 1)[-1]
    if not (len(code) == 2 and code.isascii() and code.isalpha()):
        return ""
    code = code.upper()
    return _EU_COUNTRY_CODES.get(code, code)


# ------------------------------------------------------- shared by both formats


def _split_skills(prose: str) -> list[str]:
    """Europass keeps skills as free prose; a line or a semicolon is a boundary."""
    return [line.strip(" -•\t") for line in re.split(r"[\n;]+", prose) if line.strip(" -•\t")]


def _lowest(levels: dict[str, str]) -> str:
    """One CEFR level out of the five Europass records for a language.

    Europass keeps listening, reading, spoken interaction, spoken production and writing
    apart, and a person is rarely the same at all five. Postulo keeps one, so this takes
    the **lowest**: claiming the highest of five on a CV is the kind of thing that gets
    found out in an interview, and the review page shows all five so it can be corrected.
    """
    found = [CEFR[value] for value in levels.values() if value in CEFR]
    return min(found, key=_ORDER.index) if found else ""


def _make_date(year, month, day) -> dt.date | None:
    """A date out of three numbers that arrived from somewhere else.

    A month or a day may be missing — people write "2019" and mean it — so what is
    **absent** becomes the first of the period rather than the import failing. What is
    **present and unreadable** is a different thing: no date is produced at all, because
    turning a nonsense month into January could misdate a job by eleven months, and an
    entry with no start is named in the report rather than written.

    The day is kept as written and only pulled back to the end of the month when the month
    does not have it: clamping every date to the 28th would silently move a perfectly good
    30 June, which is worse than the impossible date it was guarding against.
    """
    try:
        year = int(year)
        month = max(1, min(12, int(month or 1)))
        day = max(1, int(day or 1))
    except (TypeError, ValueError, OverflowError):
        return None
    try:
        return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))
    except (ValueError, TypeError, OverflowError):
        return None


def _orcid_from(addresses: list[str]) -> str:
    """An ORCID out of the websites a Europass file lists, if one of them is one.

    Neither format has a field for it, and everybody who has one puts it among their
    websites. It is worth lifting out because in the places Postulo is aimed at first it is
    the identifier an application form asks for by name (#46). The checksum decides: a
    wrong one is dropped rather than saved, and orcid.org is asked nothing.
    """
    for address in addresses:
        if "orcid.org" not in address:
            continue
        try:
            return identifiers.clean(identifiers.ORCID, address)
        except (ValidationError, ValueError):
            # urlsplit refuses an unbalanced bracket in the host with a ValueError.
            continue
    return ""


def _locale(value) -> str:
    """The language a file says it is in, written the way Postulo writes one.

    Europass puts it on the wrapper -- ``locale="pt"`` on the XML, ``"Locale": "pt"`` in the
    JSON -- and it is the only statement anywhere in the file about what language the career
    itself is written in. Anything that is not a language tag is dropped rather than stored:
    a file from somewhere else can put anything in that attribute, and this one ends up in
    a column and in a language negotiation, so its shape is checked rather than trusted.
    """
    text = str(value or "")
    return language_tag(text) if is_language_tag(text) else ""


def _heading(label, locale: str) -> str:
    """A heading the plugin writes onto a CV, in the language the file says the CV is in.

    It is a heading on somebody's CV, so it follows the record and not the person's interface:
    a Portuguese file read from an English page still gets Portuguese headings, and the other
    way round. The language is the one Postulo offers that is closest to the file's, and the
    active one when the file states none or none is close.
    """
    language = language_match(locale) if locale else ""
    if not language:
        return str(label)
    with translation.override(language):
        return str(label)


def _project_from(title: str, description: str) -> dict | None:
    """A project from an entry, which may have a title, a description, or only the second.

    With no title the name is the description's first line, shortened at a word, and the
    whole description is kept as the summary: cutting it to a name would lose the rest
    without a word, on a review page that is supposed to show everything that was read.
    """
    if not title and not description:
        return None
    if title:
        return {"name": title, "summary": description}
    first = description.strip().splitlines()[0].strip()
    name = textwrap.shorten(first, 80, placeholder="…") if len(first) > 80 else first
    if name == "…":  # one word longer than the limit: cut it, there is no boundary
        name = first[:79] + "…"
    return {"name": name, "summary": "" if name == description.strip() else description}


# ------------------------------------------------------------ walking the XML


def _local(tag: str) -> str:
    """The tag without its namespace, which is how everything here is matched."""
    return tag.rsplit("}", 1)[-1]


def _find(element, *names: str):
    """The first descendant whose local name is ``names`` in order, or nothing."""
    current = element
    for name in names:
        found = None
        for child in current:
            if _local(child.tag) == name:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _all(element, name: str) -> list:
    return [child for child in element if _local(child.tag) == name]


def _text(element, *names: str, keep_lines: bool = False) -> str:
    """The text of a node, tidied.

    ``keep_lines`` matters where the prose is a list: Europass puts skills in one block
    with a line each, so collapsing the newlines turns three skills into one long one.
    """
    found = _find(element, *names) if names else element
    if found is None or found.text is None:
        return ""
    if keep_lines:
        return "\n".join(" ".join(line.split()) for line in found.text.splitlines()).strip()
    return " ".join(found.text.split())
