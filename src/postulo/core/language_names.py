"""What a language is called, in any language there is a name for it in (#689).

`core.languages` is the one place a language's *code* is made and compared; this is the one
place its *name* is. A spoken language on a CV used to be text the person typed, and a
different text for every language they wanted the CV in; with a code on the entry the name is
a lookup, so a Portuguese CV says *Inglês* and a French one *Anglais* without anybody typing
either.

**The names are the Unicode CLDR's**, through Babel (which `jobs/places.py` already reads for
country names): 633 languages, each named in as many as 1,082 languages, reviewed by the
speakers of each. Nothing here is a table of names, and no name is made by a machine.

**A gap falls back and is never blank.** Not every language is named in every other (the 18
African display languages that have names for 69 of them name 21 to 68 of them, 671 gaps
among 4,761 pairs of Postulo's own tags, listed in `tests/test_language_names.py`). In
order: CLDR's name in the language asked for, read through that language's own parents --
`pt-AO` reads as `pt-PT` -- then what the language calls itself, then CLDR's English, then the
code as it was given. A well-formed code nobody has a name for is shown as the code.

**The capital is CLDR's, and Babel does not carry it.** CLDR writes a language as it falls
in a sentence -- *inglês*, *anglais* -- and says in `contextTransforms` which languages
capitalise it as a label (`titlecase-firstword`, for *languages*, standing alone or in a
menu). A CV line is a label, so it is capitalised where **either** context asks, which
gives *Inglês* and *Anglais* and leaves Catalan and Dutch as CLDR writes them. Only the
first letter is raised and the rest is left alone: `capitalize()` turns Dutch *IJslands*
into *Ijslands*, and Turkish *İngiliz İngilizcesi* into one with a combining dot. CLDR's own
Turkish names are capitalised already, and Georgian's first letter raised with `upper()`
would be Mtavruli, the all-capitals form; neither is in the table. `CAPITALISED` is the table
and `tests/test_language_names.py` holds it to the CLDR's rules for every tag Postulo has a
catalogue for, and to the version of CLDR those were read from.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

import babel
from babel import Locale, UnknownLocaleError

from . import languages

#: The version of CLDR the rules below were read from (`cldr-json` 47.0.0, `contextTransforms`
#: of every locale Postulo has a catalogue for). Babel's data carries the names and not the
#: rules, so a Babel that ships another CLDR may have changed either, and the test that holds
#: `CAPITALISED` to the tag list fails until somebody has looked.
CLDR_VERSION = "47"

#: The languages whose names CLDR asks to be capitalised as a label, by primary subtag: the
#: ones that say `titlecase-firstword` for *languages* standing alone or in a menu. European
#: Portuguese sets the menu to `no-change` and inherits the other from `pt`, so it is here.
CAPITALISED = frozenset({"cs", "da", "es", "fi", "fr", "hr", "it", "pt", "sk", "sv", "uk"})

#: CLDR's codes for what is not a language: "multiple languages", "undetermined", "no
#: linguistic content". Never offered.
NOT_LANGUAGES = frozenset({"mul", "und", "zxx"})

#: The variants offered beside the languages themselves. A CV is the one place the
#: difference between European and Brazilian Portuguese, or between the two Chinese scripts,
#: is something somebody reads.
VARIANTS = ("pt-BR", "pt-PT", "zh-Hans", "zh-Hant")

#: A name that is the umbrella of several offered ones is not a choice between them: *Chinese*
#: beside the two scripts, *Portuguese* beside both countries', *Norwegian* beside Bokmål and
#: Nynorsk. A typed name that is one of these stays text (`match`).
UMBRELLAS = frozenset({languages.primary(code) for code in VARIANTS}) | frozenset(
    languages.MACROLANGUAGE.values()
)

#: The three-letter codes of the Publications Office's list of spoken languages (ECV06),
#: onto the two-letter code CLDR names them by. Bibliographic variants too, because files
#: written by hand use either. Sign languages and language families have no two-letter code.
ISO_639_2 = {
    "alb": "sq", "ara": "ar", "arm": "hy", "aze": "az", "baq": "eu", "bel": "be",
    "ben": "bn", "bos": "bs", "bul": "bg", "cat": "ca", "ces": "cs", "chi": "zh",
    "cym": "cy", "cze": "cs", "dan": "da", "deu": "de", "dut": "nl", "ell": "el",
    "eng": "en", "est": "et", "eus": "eu", "fas": "fa", "fin": "fi", "fra": "fr",
    "fre": "fr", "geo": "ka", "ger": "de", "gle": "ga", "glg": "gl", "gre": "el",
    "guj": "gu", "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy",
    "ice": "is", "isl": "is", "ita": "it", "jav": "jv", "jpn": "ja", "kat": "ka",
    "kaz": "kk", "kor": "ko", "kur": "ku", "lat": "la", "lav": "lv", "lim": "li",
    "lit": "lt", "mac": "mk", "mar": "mr", "may": "ms", "mkd": "mk", "mlt": "mt",
    "msa": "ms", "nld": "nl", "nor": "no", "oci": "oc", "pan": "pa", "per": "fa",
    "pol": "pl", "por": "pt", "ron": "ro", "rum": "ro", "rus": "ru", "san": "sa",
    "slk": "sk", "slo": "sk", "slv": "sl", "spa": "es", "sqi": "sq", "srd": "sc",
    "srp": "sr", "swe": "sv", "tam": "ta", "tel": "te", "tur": "tr", "ukr": "uk",
    "urd": "ur", "vie": "vi", "vol": "vo", "wel": "cy", "wln": "wa", "yid": "yi",
    "zho": "zh",
}  # fmt: skip


def cldr_version() -> str:
    """The version of CLDR the installed Babel carries."""
    return str(babel.core.get_global("cldr")["version"])


@lru_cache(maxsize=256)
def _locale(code) -> Locale | None:
    """The locale for a language tag, or for the longest start of it there is data for."""
    parts = languages.tag(code).split("-")
    while parts and parts[0]:
        try:
            return Locale.parse("_".join(parts))
        except (UnknownLocaleError, ValueError):
            parts.pop()
    return None


def _cldr(code: str, locale: Locale | None) -> str:
    """CLDR's name for ``code`` in ``locale``, from the most to the least exact key."""
    if locale is None:
        return ""
    names = locale.languages
    parts = code.split("-")
    for count in range(len(parts), 0, -1):
        found = names.get("_".join(parts[:count]))
        if found:
            return found
    return ""


def capitalised(name: str, in_language: str) -> str:
    """A name as it is written standing alone on a CV, in the language it is written in."""
    locale = _locale(in_language)
    if name and locale is not None and locale.language in CAPITALISED:
        return name[:1].upper() + name[1:]
    return name


def name(code, in_language="") -> str:
    """What ``code`` is called in ``in_language``, with a fallback for every gap.

    Never blank for a code: the language's name in that language, then its own, then
    CLDR's English, then the code. Blank for nothing.
    """
    code = languages.tag(code)
    if not code:
        return ""
    for shown in (in_language, code, "en"):
        found = _cldr(code, _locale(shown)) if shown else ""
        if found:
            return capitalised(found, shown)
    return languages.native_name(code) or code


def known(code) -> bool:
    """Whether CLDR names this language in English, which is whether anybody can name it."""
    code = languages.tag(code)
    return bool(code and _cldr(code, _locale("en")))


@lru_cache(maxsize=1)
def offered() -> tuple[str, ...]:
    """The codes a person may choose: CLDR's languages and `VARIANTS`, by code."""
    english = _locale("en").languages
    found = {key for key in english if "_" not in key and key not in NOT_LANGUAGES}
    return tuple(sorted(found | set(VARIANTS)))


def fold(text) -> str:
    """A name as it is compared: case, accents and the space around it are not part of it."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("-", " ").split())


@lru_cache(maxsize=16)
def listing(in_language="") -> tuple[tuple[str, str], ...]:
    """Every language offered, as (code, name) in ``in_language``, in the order a person reads."""
    return tuple(sorted(((code, name(code, in_language)) for code in offered()), key=_order))


def _order(row: tuple[str, str]) -> tuple[str, str]:
    return fold(row[1]), row[0]


def _own(code: str) -> str:
    """The key CLDR keeps a code under, which is how it writes it with an underscore."""
    return code.replace("-", "_")


@lru_cache(maxsize=1)
def _codes() -> dict[str, str]:
    """Each offered code as it is compared, onto itself as it is written."""
    return {fold(code): code for code in offered()}


@lru_cache(maxsize=1)
def _index() -> dict[str, frozenset[str]]:
    """Every name of every offered language in English and in each language Postulo has a
    catalogue for, folded, onto the codes it is the name of."""
    index: dict[str, set[str]] = {}
    for shown in ("en", *languages.NATIVE_NAMES):
        locale = _locale(shown)
        for code in offered():
            found = locale.languages.get(_own(code)) if locale is not None else ""
            if found:
                index.setdefault(fold(found), set()).add(code)
    return {key: frozenset(codes) for key, codes in index.items()}


def match(text) -> str:
    """The language a typed name stands for, or nothing where it is not exactly one.

    Casefolded and accent-insensitive, in English, in any language Postulo has a catalogue
    for (which includes what each of those calls itself), or as an ISO 639 code. What a
    language outside those calls itself is not looked up: that is a locale loaded for each
    of 633 languages, a minute and a half, to answer a name nobody has typed.

    *Ambiguous* is nothing: a name two languages share, and the umbrella names in
    `UMBRELLAS`, which the person is asked to choose between. This never guesses, because
    what it answers is put on a CV.
    """
    folded = fold(text)
    if not folded:
        return ""
    code = _codes().get(ISO_639_2.get(folded, folded), "")
    if code:
        return code
    codes = _index().get(folded, frozenset())
    if len(codes) != 1:
        return ""
    (only,) = codes
    return "" if only in UMBRELLAS else only
