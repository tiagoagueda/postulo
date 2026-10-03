"""A salary and a closing date, read in the language the page is written in (#267).

A board that publishes no structured data still writes both down for its readers, in their
language: "Remuneração: 1.385,99 € mensais", "Date limite de candidature : 30 octobre
2026", "Closing date: 30/10/2026". Reading them takes three things a reader that knows only
markup does not have, and Postulo does, because it already speaks the languages these pages
are written in:

- **the month names**, from the translations Django ships and Postulo already writes every
  date with;
- **which way round a numeric date goes**, from the same locale formats: 30/10/2026 can only
  be the thirtieth of October, but 03/04/2026 is the third of April in Lisbon and the fourth
  of March in Chicago;
- **the words a page puts before each**, which are this plugin's own strings, translated
  like any other. Somebody who knows how adverts in their language announce a deadline
  teaches this reader to find it from the translation platform, without a line of code.

The page says which language it is in -- ``<html lang>``, ``og:locale``, a
``Content-Language`` -- and is read in that language and in English. A page that says
nothing is read in English alone.

**Nothing is invented.** A figure is read only just after one of the words, and only with
the currency it is written in; a period only where the page states one; a date only when it
is whole -- a day, a month and a year -- and cannot be read two ways. A numeric date the
page's language does not settle (03/04/2026 on a page that says only "en", which is written
both ways round) is left for the person, and so is "apply by 30 October", whose year would
be a guess. Two readings that disagree are no reading at all.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import translation
from django.utils.dates import MONTHS, MONTHS_3, MONTHS_ALT, MONTHS_AP
from django.utils.formats import get_format, get_format_modules
from django.utils.translation import pgettext_lazy

# ------------------------------------------------------------------ the words
#
# Each is a list of spellings separated by "|", translated like any other string. English is
# always read as well. A page in a language whose lists nobody has translated yet is read in
# English alone, which finds nothing on it -- and nothing is the right answer until somebody
# who reads that language says what to look for.

CLOSING_WORDS = pgettext_lazy(
    "Words a job advert writes just before the last day to apply, separated by |. List the "
    "ones adverts in your language use; each is matched as whole words, capitals aside.",
    "closing date|application deadline|deadline|apply by|apply before|applications close"
    "|closes on|closing on|last day to apply|open until",
)
SALARY_WORDS = pgettext_lazy(
    "Words a job advert writes just before the pay it offers, separated by |. List the ones "
    "adverts in your language use; each is matched as whole words, capitals aside.",
    "salary|salary range|base salary|annual salary|pay range|pay rate|compensation"
    "|remuneration|wage",
)
UP_TO_WORDS = pgettext_lazy(
    "Words that make a pay figure the most on offer rather than the least, as in 'up to "
    "40,000', separated by |.",
    "up to|maximum|max",
)
RANGE_WORDS = pgettext_lazy(
    "Words that join the two figures of a pay range, as in '30,000 to 40,000', separated by |.",
    "to|and",
)
PERIOD_WORDS = {
    "year": pgettext_lazy(
        "Words that say a pay figure is for a year, separated by |.",
        "per year|a year|per annum|annually|yearly|annual|p.a.|/year|/yr",
    ),
    "month": pgettext_lazy(
        "Words that say a pay figure is for a month, separated by |.",
        "per month|a month|monthly|/month|/mo",
    ),
    "day": pgettext_lazy(
        "Words that say a pay figure is for a day, separated by |.",
        "per day|a day|daily|/day",
    ),
    "hour": pgettext_lazy(
        "Words that say a pay figure is for an hour, separated by |.",
        "per hour|an hour|hourly|/hour|/hr|/h",
    ),
}

#: Every list, by the name it is asked for by.
LISTS = {
    "closing": CLOSING_WORDS,
    "salary": SALARY_WORDS,
    "up_to": UP_TO_WORDS,
    "range": RANGE_WORDS,
    **PERIOD_WORDS,
}

# ------------------------------------------------------------------ the money

#: Written beside an amount, each of these is one currency wherever the page comes from.
#: "$", "kr" and "¥" are not here: each is several currencies, and which one is a guess.
SYMBOLS = {
    "€": "EUR",
    "£": "GBP",
    "R$": "BRL",
    "US$": "USD",
    "CA$": "CAD",
    "C$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
    "NZ$": "NZD",
    "HK$": "HKD",
    "S$": "SGD",
    "MX$": "MXN",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "₺": "TRY",
    "₪": "ILS",
    "₴": "UAH",
    "₦": "NGN",
    "₱": "PHP",
    "₫": "VND",
    "฿": "THB",
    "zł": "PLN",
    "Kč": "CZK",
}

#: The euro, written out in the word most of the languages that use it share.
CURRENCY_WORDS = {"euro": "EUR", "euros": "EUR"}

#: What "$" alone is, where the page says which country it is written for. Anywhere else it
#: is one of twenty currencies, and it is not read.
DOLLAR_REGIONS = {
    "US": "USD",
    "CA": "CAD",
    "AU": "AUD",
    "NZ": "NZD",
    "SG": "SGD",
    "HK": "HKD",
    "MX": "MXN",
}

#: A figure as money is written: grouped in threes by a dot, a comma, an apostrophe or a
#: space of any width, and never with more than two decimals. That last is what settles the
#: grouping without asking the locale: "1.500" and "1,500" are fifteen hundred whichever
#: language wrote them, because no price has three decimals.
FIGURE = (
    r"\d{1,3}(?:[.,'’    ]\d{3})+(?:[.,]\d{1,2})?(?!\d)"
    r"|\d+(?:[.,]\d{1,2})?(?!\d)"
)

#: "45k", "45 K": thousands, as salaries are written in a hurry.
THOUSANDS = r"\s?[kK](?![^\W\d_])"

#: A figure this large is not a salary somebody is being offered.
LARGEST = Decimal(10) ** 9

# ------------------------------------------------------------------ the dates

#: English is written both ways round: 03/04/2026 is April in London and March in New York.
#: A page that says only "en" settles nothing, so only these places are read day first.
DAY_FIRST_ENGLISH = frozenset({"GB", "IE", "AU", "NZ", "IN"})

# ------------------------------------------------------------------ the reading

#: How far past its words a value is looked for, and how much may stand between them: a
#: colon or a line break and a few words, or a very few words and no colon at all.
WINDOW = 160
REACH_WITH_A_COLON = 40
REACH_WITHOUT = 12

#: How far past a figure its period is looked for, on the same line.
PERIOD_REACH = 30


def tag_of(raw: str) -> str:
    """A page's declared language as Postulo writes one, ``pt-BR``; "" where it is not a tag.

    Pages write "pt", "pt-PT", "pt_BR" and "en-US,en;q=0.9"; the first of a list is taken.
    """
    from postulo.plugins.api import is_language_tag, language_tag

    declared = (raw or "").split(",")[0]
    return language_tag(declared) if is_language_tag(declared) else ""


def region_of(tag: str) -> str:
    """The country a tag names, where it names one: ``US`` in ``en-US``."""
    for subtag in tag.split("-")[1:]:
        if (len(subtag) == 2 and subtag.isalpha()) or (len(subtag) == 3 and subtag.isdigit()):
            return subtag.upper()
    return ""


def spoken(tag: str) -> tuple[str, ...]:
    """Postulo's own languages a page's tag names, the closest first; () for none.

    Only ever one of Postulo's codes, never the page's tag itself: the tag is a stranger's
    text, and each language a translation is asked for stays in memory for the life of the
    process.
    """
    from postulo.plugins.api import language_matches

    return language_matches(tag, [code for code, _name in settings.LANGUAGES])


def _languages(tag: str) -> tuple[str, ...]:
    """What a page is read in: English, and the languages its tag names."""
    return tuple(dict.fromkeys((settings.LANGUAGE_CODE, *spoken(tag))))


@functools.lru_cache(maxsize=512)
def _list_in(code: str, name: str) -> tuple[str, ...]:
    with translation.override(code):
        written = str(LISTS[name])
    return tuple(word.strip() for word in written.split("|") if word.strip())


def words(name: str, tag: str = "") -> tuple[str, ...]:
    """One list, in English and in every language of Postulo's that the tag names."""
    found: dict[str, str] = {}
    for code in _languages(tag):
        for word in _list_in(code, name):
            found.setdefault(word.casefold(), word)
    return tuple(found.values())


def _word(word: str) -> str:
    """One spelling, matched whole: "/yr" straight after a figure, "per annum" across any
    spacing, "p.a." with its dots."""
    body = re.escape(_straight(word).lstrip("/").strip()).replace(r"\ ", r"\s+")
    if word.startswith("/"):
        return rf"\s{{0,2}}/\s{{0,2}}{body}(?![^\W\d_])"
    return rf"(?<![^\W\d_]){body}(?![^\W\d_])"


@functools.lru_cache(maxsize=256)
def _pattern_for(spellings: tuple[str, ...]) -> re.Pattern:
    """Several spellings as one pattern, the longest first so that a longer one wins."""
    ordered = sorted(set(spellings), key=len, reverse=True)
    return re.compile("|".join(_word(word) for word in ordered) or r"(?!)", re.IGNORECASE)


def _straight(text: str) -> str:
    """A curly apostrophe as a straight one, so "jusqu’à" is "jusqu'à" however it was typed."""
    return (text or "").replace("’", "'")


def _runs_on(filler: str) -> bool:
    """Whether what stands between a label and a value is only the label running on to it.

    Not a sentence, not a number, not another paragraph, not another label: "Closing date:
    30 October 2026" and "Salário base mensal: 1.385,99 €" run on, and so does "Salary
    €45,000"; "salary sacrifice scheme worth up to £500" does not, because a label that is
    a word in the middle of a sentence labels nothing, and "Deadline: ASAP. Start date:
    1 November" does not, because the date belongs to the second label.
    """
    if re.search(r"[\d!?;]", filler) or filler.count("\n") > 1 or filler.count(":") > 1:
        return False
    stops = (stop.end() for stop in re.finditer(r"\.\s+", filler))
    if any(end < len(filler) and filler[end].isupper() for end in stops):
        return False  # a full stop and a capital: a new sentence, and not this label's
    if ":" in filler or "\n" in filler:
        return len(filler) <= REACH_WITH_A_COLON
    return len(filler) <= REACH_WITHOUT


#: How much of what stands before a figure can name its period: a word or two, never the line.
LABEL_REACH = 40


def _labelling(before: str) -> str:
    """The tail of ``before`` that can label the figure after it, for ``_period`` (#420).

    Not the line: "Daily standups. Salary: 40,000" is not paid by the day. What is kept is
    the last word when only spaces separate it from the end, so "Monthly salary" and "Gross
    annual salary" keep their period, and nothing at or before a sentence stop or a
    separator such as a bar. Bounded, so a long run of spaces costs nothing.
    """
    tail = before[-LABEL_REACH:]
    stop = max(tail.rfind(mark) for mark in (".", "!", "?", ";", "|", "\n", "•"))
    tail = tail[stop + 1 :]
    word = re.search(r"[^\W\d_]+[\s:]{0,3}$", tail)
    return word.group(0) if word else ""


def _after(labels: tuple[str, ...], text: str):
    """Each ``(label, what follows it)`` in ``text``: the label's own words with the word
    before them (``_labelling``), and the stretch after it where the value it labels has to
    start."""
    for hit in _pattern_for(labels).finditer(text):
        line = text.rfind("\n", 0, hit.start()) + 1
        before = _labelling(text[line : hit.start()])
        yield f"{before}{text[hit.start() : hit.end()]}", text[hit.end() : hit.end() + WINDOW]


# ------------------------------------------------------------------ dates


@functools.lru_cache(maxsize=128)
def _months_in(code: str) -> tuple[tuple[str, int], ...]:
    """Every way one language writes each month: in full, abbreviated, and the form some
    languages use inside a date. Django's own translations, so all of Postulo's languages
    that Django speaks, and English for the rest."""
    found: list[tuple[str, int]] = []
    with translation.override(code):
        for table in (MONTHS, MONTHS_ALT, MONTHS_AP, MONTHS_3):
            for number, name in table.items():
                key = _month_key(str(name))
                if key and not any(character.isdigit() for character in key):
                    found.append((key, number))
    return tuple(found)


def _month_key(written: str) -> str:
    return written.strip().rstrip(".").casefold()


@functools.lru_cache(maxsize=64)
def _month_names(codes: tuple[str, ...]) -> dict[str, int]:
    merged: dict[str, int] = {}
    clashes: set[str] = set()
    for code in codes:
        for key, number in _months_in(code):
            if merged.setdefault(key, number) != number:
                clashes.add(key)
    return {key: number for key, number in merged.items() if key not in clashes}


def months(tag: str = "") -> dict[str, int]:
    """Month names to their numbers, in English and the page's language, leaving out any
    name two months would share."""
    return dict(_month_names(_languages(tag)))


def order(tag: str) -> str:
    """How the page's language writes a numeric date: "dmy", "mdy", or "" where it does not
    settle it."""
    if tag.split("-")[0] == "en":
        region = region_of(tag)
        if region == "US":
            return "mdy"
        return "dmy" if region in DAY_FIRST_ENGLISH else ""
    codes = spoken(tag)
    if not codes or not get_format_modules(codes[0]):
        # A language Django has no formats for falls back to settings written for somebody
        # else, which is no evidence about this page.
        return ""
    for written in get_format("DATE_INPUT_FORMATS", lang=codes[0]):
        if written.startswith("%Y"):
            continue
        day, month = written.find("%d"), written.find("%m")
        if 0 <= day < month:
            return "dmy"
        if 0 <= month < day:
            return "mdy"
    return ""


@functools.lru_cache(maxsize=64)
def _dates(codes: tuple[str, ...]) -> tuple[re.Pattern, dict[str, int]]:
    """The pattern a date is written in, in these languages, and their month names."""
    names = _month_names(codes)
    month = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    # "de", "of", "du": the short word some languages put between a day, a month and a year.
    little = r"(?:\s+[^\W\d_]{1,3})?"
    pattern = re.compile(
        r"(?P<iso>(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2}))(?!\d)"
        r"|(?P<ymd>(?P<yy>\d{4})(?P<ys>[./])(?P<ym>\d{1,2})(?P=ys)(?P<yd>\d{1,2}))(?!\d)"
        r"|(?P<num>(?P<na>\d{1,2})(?P<ns>[./-])(?P<nb>\d{1,2})(?P=ns)(?P<ny>\d{4}|\d{2}))(?!\d)"
        rf"|(?P<dm>(?P<dd>\d{{1,2}})(?:st|nd|rd|th|º|ª|er|\.)?{little}\s*"
        rf"(?P<dmm>{month})(?![^\W\d_])\.?,?{little}\s+(?P<dy>\d{{4}}))(?!\d)"
        rf"|(?P<md>(?P<mdm>{month})(?![^\W\d_])\.?\s+(?P<mdd>\d{{1,2}})(?:st|nd|rd|th)?,?"
        r"\s+(?P<mdy>\d{4}))(?!\d)",
        re.IGNORECASE,
    )
    return pattern, names


def _date(match: re.Match, names: dict[str, int], written: str) -> dt.date | None:
    """One date the pattern matched, or ``None`` where it is not a day or reads two ways."""
    get = match.group
    try:
        if get("iso"):
            return dt.date(int(get("iy")), int(get("im")), int(get("id")))
        if get("ymd"):
            return dt.date(int(get("yy")), int(get("ym")), int(get("yd")))
        if get("num"):
            first, second = int(get("na")), int(get("nb"))
            year = int(get("ny"))
            year = year if year > 99 else 2000 + year
            if first > 12 >= second:
                day, month = first, second
            elif second > 12 >= first:
                day, month = second, first
            elif written == "dmy":
                day, month = first, second
            elif written == "mdy":
                day, month = second, first
            else:
                return None
            return dt.date(year, month, day)
        if get("dm"):
            return dt.date(int(get("dy")), names[_month_key(get("dmm"))], int(get("dd")))
        if get("md"):
            return dt.date(int(get("mdy")), names[_month_key(get("mdm"))], int(get("mdd")))
    except (KeyError, ValueError):
        return None
    return None


def date_in(text: str, tag: str = "") -> dt.date | None:
    """The date one short text states, with no label needed: the text of an element a
    person's own correction already showed to hold the closing date (`hints`)."""
    pattern, names = _dates(_languages(tag))
    match = pattern.search(_straight(text))
    return _date(match, names, order(tag)) if match is not None else None


def closing_date(text: str, tag: str = "") -> dt.date | None:
    """The last day to apply, where the page writes it just after its words for that."""
    text = _straight(text)
    pattern, names = _dates(_languages(tag))
    written = order(tag)
    found: set[dt.date] = set()
    for _label, following in _after(words("closing", tag), text):
        match = pattern.search(following)
        if match is None or not _runs_on(following[: match.start()]):
            continue
        date = _date(match, names, written)
        if date is not None:
            found.add(date)
    return found.pop() if len(found) == 1 else None


# ------------------------------------------------------------------ pay


def figure(written: str) -> Decimal | None:
    """A figure as a page writes it, as a number: "1.385,99" and "1,385.99" alike."""
    raw = re.sub(r"[\s'’]", "", written or "")
    marks = [character for character in raw if character in ".,"]
    if not marks:
        digits = raw
    else:
        last = raw.rfind(marks[-1])
        tail = raw[last + 1 :]
        if len(set(marks)) == 1 and (len(marks) > 1 or len(tail) == 3):
            # Only one kind of mark, and three digits after it: it groups, whatever the
            # language, because no price has three decimals.
            digits = raw.replace(marks[-1], "")
        else:
            digits = raw[:last].replace(".", "").replace(",", "") + "." + tail
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _currency(dollar: str) -> str:
    """Every way a currency is written beside a figure, as one pattern."""
    from postulo.plugins.api import CURRENCY_CODES

    symbols = [
        rf"(?<![^\W\d_]){re.escape(symbol)}" if symbol[0].isalpha() else re.escape(symbol)
        for symbol in sorted(SYMBOLS, key=len, reverse=True)
    ]
    ways = [
        *symbols,
        r"(?<![A-Za-z])(?:" + "|".join(sorted(CURRENCY_CODES)) + r")(?![A-Za-z])",
        r"(?i:(?<![^\W\d_])euros?(?![^\W\d_]))",
    ]
    if dollar:
        ways.append(r"(?<![A-Za-z])\$")
    return "|".join(ways)


def currency_code(written: str, dollar: str) -> str:
    """The ISO 4217 code for a currency as it was written beside a figure."""
    from postulo.plugins.api import CURRENCY_CODES

    written = (written or "").strip()
    if written in SYMBOLS:
        return SYMBOLS[written]
    if written.casefold() in CURRENCY_WORDS:
        return CURRENCY_WORDS[written.casefold()]
    if written == "$":
        return dollar
    return written if written in CURRENCY_CODES else ""


@functools.lru_cache(maxsize=64)
def _pay_pattern(codes: tuple[str, ...], dollar: str) -> re.Pattern:
    """One figure or two joined as a range, each with its currency on either side."""
    currency = _currency(dollar)
    joins: list[str] = []
    for code in codes:
        joins.extend(_list_in(code, "range"))

    def one(n: int) -> str:
        return (
            f"(?:(?P<before{n}>{currency})\\s{{0,2}})?"
            f"(?P<figure{n}>{FIGURE})"
            f"(?P<thousands{n}>{THOUSANDS})?"
            f"(?:\\s{{0,2}}(?P<after{n}>{currency}))?"
        )

    join = f"\\s*(?:[-–—]|(?i:{_pattern_for(tuple(joins)).pattern}))\\s*"
    return re.compile(f"{one(1)}(?:{join}{one(2)})?")


def _pay(
    match: re.Match, dollar: str, *, named: bool = True
) -> tuple[Decimal | None, Decimal | None, str] | None:
    """``(low, high, currency)`` from one match; ``None`` unless it names one currency.

    ``named=False`` also takes a figure written with no currency at all, and leaves the
    currency empty: for a place a person's own correction showed to hold the pay, where the
    figure is vouched for by them rather than by the words around it.
    """
    get = match.group
    written = [get(name) for name in ("before1", "after1", "before2", "after2") if get(name)]
    currencies = {currency_code(one, dollar) for one in written}
    if "" in currencies or len(currencies) > 1 or (named and not currencies):
        return None
    low = figure(get("figure1"))
    if low is None:
        return None
    if get("thousands1"):
        low *= 1000
    high = None
    if get("figure2"):
        high = figure(get("figure2"))
        if high is None:
            return None
        if get("thousands2"):
            high *= 1000
            # "45-55k" is forty-five to fifty-five thousand, as people write it.
            if not get("thousands1") and low * 1000 <= high:
                low *= 1000
    if low <= 0 or (high is not None and high < low) or max(low, high or low) >= LARGEST:
        return None
    return low, high, currencies.pop() if currencies else ""


def _period(following: str, label: str, tag: str) -> str:
    """The period the page states for a figure: the nearest after it on its line, else the
    one its label names ("Annual salary"). "" where it states none, or two."""
    line = following.split("\n", 1)[0][:PERIOD_REACH]
    nearest, where = "", len(line) + 1
    for period in PERIOD_WORDS:
        hit = _pattern_for(words(period, tag)).search(line)
        if hit is not None and hit.start() < where:
            nearest, where = period, hit.start()
    if nearest:
        return nearest
    named = {period for period in PERIOD_WORDS if _pattern_for(words(period, tag)).search(label)}
    return named.pop() if len(named) == 1 else ""


def pay_in(text: str, tag: str = "") -> tuple[Decimal | None, Decimal | None, str, str] | None:
    """``(low, high, currency, period)`` from one short text with no label needed, or
    ``None``: the text of an element a person's own correction showed to hold the pay
    (`hints`). A figure with no currency beside it is taken, and its currency left empty."""
    text = _straight(text)
    dollar = DOLLAR_REGIONS.get(region_of(tag), "")
    match = _pay_pattern(_languages(tag), dollar).search(text)
    if match is None:
        return None
    read = _pay(match, dollar, named=False)
    if read is None:
        return None
    low, high, currency = read
    before = text[: match.start()]
    if high is None and _pattern_for(words("up_to", tag)).search(before):
        low, high = None, low
    return low, high, currency, _period(text[match.end() :], _labelling(before), tag)


def salary(text: str, tag: str = "") -> tuple[Decimal | None, Decimal | None, str, str]:
    """``(low, high, currency, period)`` where the page writes its pay just after its words
    for that, in a currency it names; four empties otherwise.

    A single figure is the least on offer, unless the words before it say it is the most
    ("up to 40,000"). Two readings of the page that disagree are no reading.
    """
    nothing: tuple[Decimal | None, Decimal | None, str, str] = (None, None, "", "")
    text = _straight(text)
    dollar = DOLLAR_REGIONS.get(region_of(tag), "")
    languages = _languages(tag)
    pattern = _pay_pattern(languages, dollar)
    up_to = _pattern_for(words("up_to", tag))
    found: set[tuple[Decimal | None, Decimal | None, str, str]] = set()
    for label, following in _after(words("salary", tag), text):
        match = pattern.search(following)
        if match is None:
            continue
        filler = following[: match.start()]
        if not _runs_on(filler):
            continue
        read = _pay(match, dollar)
        if read is None:
            continue
        low, high, currency = read
        if high is None and up_to.search(filler):
            low, high = None, low
        period = _period(following[match.end() :], f"{label} {filler}", tag)
        found.add((low, high, currency, period))
    return found.pop() if len(found) == 1 else nothing
