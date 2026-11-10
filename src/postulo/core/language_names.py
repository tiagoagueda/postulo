"""What a language is called, in any language there is a name for it in (#689).

`core.languages` is the one place a language's *code* is made and compared; this is the one
place its *name* is. A spoken language on a CV used to be text the person typed, and a
different text for every language they wanted the CV in; with a code on the entry the name is
a lookup, so a Portuguese CV says *Inglês* and a French one *Anglais* without anybody typing
either.

**The names are the Unicode CLDR's**, through Babel (which `jobs/places.py` already reads for
country names): 633 languages, each named in as many as 1,082 languages, reviewed by the
speakers of each. Nothing here is a table of names, and no name is made by a machine.

**A gap falls back and is never blank.** Not every language is named in every other (870 gaps
among 7,225 pairs of Postulo's own tags: Montenegrin, which CLDR names nowhere, and 785 in 18
African display languages, listed in `tests/data/language_name_gaps.json`). In
order: CLDR's name in the language asked for, read through that language's own parents --
`pt-AO` reads as `pt-PT` -- then what the language calls itself, then CLDR's English, then the
code as it was given. A well-formed code nobody has a name for is shown as the code.

**A locale is read and let go (#1086).** A locale is a language's whole CLDR data, and Babel
keeps every one it has parsed for the life of the process. Each is read here with Babel
handed it back (`borrowed_locales`): the few languages that names are being read in stay in
hand (`LOCALES_KEPT`), what a language calls itself is kept as that word and not as its
locale (`_itself`), and the table typed names are matched in (`_index`) is kept without the
86 locales it is made from.

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
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import NamedTuple

import babel
from babel import Locale, UnknownLocaleError, localedata

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

#: How many locales are kept in hand at once (#1086). A locale is a language's whole CLDR
#: data, about 0.6 MB, and a process names languages in the few its people read: those stay,
#: and any other is read when it is asked for and let go when these have been used since.
#: What a language says of itself is not among them (`_itself`).
LOCALES_KEPT = 8

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


@contextmanager
def borrowed_locales() -> Iterator[None]:
    """CLDR's locales, read inside the block and handed back at the end of it (#1109).

    Babel keeps every locale it has parsed for the life of the process, each with its
    parents and whole: its dates, its units and its numbers, about 0.6 MB apiece. A table
    made from a few hundred names of each of Postulo's languages therefore left 95 of them
    behind, some 60 MB a process, to be read once. What Babel held before the block it still
    holds after it; what the block loaded is dropped, and is read again from its file the
    next time anybody asks for it, so nothing that is using a locale is disturbed.

    The cache is Babel's own business (`babel.localedata._cache`), which is why Babel is
    pinned to one minor version and `tests/test_places.py` holds this to what it promises.
    """
    # Under Babel's own lock both times: another thread may be loading a locale meanwhile.
    with localedata._cache_lock:
        held = set(localedata._cache)
    try:
        yield
    finally:
        with localedata._cache_lock:
            for loaded in set(localedata._cache) - held:
                del localedata._cache[loaded]


def _read(code) -> Locale | None:
    """The locale for a language tag, or for the longest start of it there is data for.

    With its data read, and none of it left in Babel's cache (`borrowed_locales`): a
    `Locale` loads its data at its first use and keeps it, so reading it here, inside the
    block, makes the object the only thing that holds it. Whoever lets go of the object
    lets go of the locale (#1086).
    """
    parts = languages.tag(code).split("-")
    while parts and parts[0]:
        try:
            with borrowed_locales():
                locale = Locale.parse("_".join(parts))
                locale.languages  # noqa: B018
            return locale
        except (UnknownLocaleError, ValueError):
            parts.pop()
    return None


@lru_cache(maxsize=LOCALES_KEPT)
def _locale(code) -> Locale | None:
    """The locale for a language tag (`_read`), kept in hand while it is one of the last used."""
    return _read(code)


def _cldr(code: str, locale: Locale | _Itself | None) -> str:
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


class _Itself(NamedTuple):
    """What a language's locale says of the language itself, which is all that is kept of it.

    The name by each key CLDR may hold it under, the name of its country, and the language
    the locale is of, as a `Locale` has them: `_cldr` and `_raised` read either.
    """

    language: str
    languages: dict[str, str]
    country: str


@lru_cache(maxsize=1024)
def _itself(code: str) -> _Itself | None:
    """A language's locale, read for what it says of itself and let go (#1086).

    What a language is called in itself is the fallback wherever another has no name for
    it, and the heading and the labels of a menu of languages: one locale for each language
    named, most of them for a single word. The word is kept, and the 0.6 MB it came with is
    not, so a menu of 85 languages is 85 names in hand and not 85 locales.
    """
    locale = _read(code)
    if locale is None:
        return None
    parts = code.split("-")
    keys = ["_".join(parts[:count]) for count in range(len(parts), 0, -1)]
    names = locale.languages
    region = languages.region_of(code) or _likely_region(code)
    return _Itself(
        language=locale.language,
        languages={key: names[key] for key in keys if names.get(key)},
        country=locale.territories.get(region, "") if region else "",
    )


def _raised(name: str, locale: Locale | _Itself | None) -> str:
    """A name with the capital the language of ``locale`` gives it standing alone."""
    if name and locale is not None and locale.language in CAPITALISED:
        return name[:1].upper() + name[1:]
    return name


def capitalised(name: str, in_language: str) -> str:
    """A name as it is written standing alone on a CV, in the language it is written in."""
    return _raised(name, _locale(in_language))


def _asked(code: str, in_language) -> Iterator[Locale | _Itself | None]:
    """Where a name for ``code`` is looked for, in order, each read only when it is reached."""
    if in_language:
        yield _locale(in_language)
    yield _itself(code)
    yield _locale("en")


def name(code, in_language="") -> str:
    """What ``code`` is called in ``in_language``, with a fallback for every gap.

    Never blank for a code: the language's name in that language, then its own, then
    CLDR's English, then the code. Blank for nothing.
    """
    code = languages.tag(code)
    if not code:
        return ""
    for locale in _asked(code, in_language):
        found = _cldr(code, locale)
        if found:
            return _raised(found, locale)
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
    catalogue for, folded, onto the codes it is the name of.

    The table is what a process keeps, some 22,000 names. The 86 locales it is made from
    are not: kept, they were 66 MB in every process that had matched one typed name (#1086).
    """
    index: dict[str, set[str]] = {}
    keys = [(_own(code), code) for code in offered()]
    for shown in ("en", *languages.NATIVE_NAMES):
        # Read and let go, one at a time, and not through `_locale` (#1086): 86 locales
        # through the few it keeps would only push out the ones that are in use.
        locale = _read(shown)
        if locale is None:
            continue
        names = locale.languages
        for key, code in keys:
            found = names.get(key)
            if found:
                index.setdefault(fold(found), set()).add(code)
    # Nearly every name is one language's alone: one set for each language, not for each name.
    sets: dict[frozenset[str], frozenset[str]] = {}
    return {
        key: sets.setdefault(codes, codes)
        for key, codes in ((key, frozenset(found)) for key, found in index.items())
    }


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


# ------------------------------------------------- a language among its own variants (#1053)


def family_name(code) -> str:
    """What a language calls itself as the heading over its variants: *Português* over
    Portugal and Brasil, *中文* over the two scripts.

    CLDR's name for the bare language, in that language, capitalised as CLDR asks of a menu
    (`capitalised`). The name on Postulo's own list where CLDR has none.
    """
    language = languages.primary(code)
    itself = _itself(language) if language else None
    found = _cldr(language, itself)
    return _raised(found, itself) if found else languages.native_name(code, str(code))


def _names_a_script(code: str) -> bool:
    """Whether a tag says which script it is written in, as ``zh-Hans`` does."""
    return any(len(part) == 4 and part.isalpha() for part in code.split("-")[1:])


def _likely_region(code: str) -> str:
    """The country CLDR takes a tag that names none to mean: ``ES`` for ``es``."""
    likely = babel.core.get_global("likely_subtags").get(languages.primary(code), "")
    region = likely.rsplit("_", 1)[-1] if "_" in likely else ""
    return region if len(region) == 2 and region.isalpha() else ""


def variant_name(code) -> str:
    """What tells one variant of a language from the others, in the variant's own language.

    A variant by country is named by its country: *Brasil*, *México*, *United Kingdom*. A
    tag that names no country is read with the one CLDR says is likely, so ``es`` is
    *España*. A variant by script is its whole name, *简体中文* and *繁體中文* (#1053): the
    script alone, *简体*, is not what anybody calls their language.
    """
    code = languages.tag(code)
    own = languages.native_name(code, code)
    if _names_a_script(code) and not languages.region_of(code):
        return own
    itself = _itself(code)
    return (itself.country if itself is not None else "") or own


@lru_cache(maxsize=1024)
def search_words(code, in_language="") -> str:
    """Everything a language can be found by in a menu's search box (#1053).

    Its own name, which is the one the menu shows; CLDR's English one, so that *German*
    finds *Deutsch*; its name in ``in_language``, the language the page is read in, so a
    French reader's *allemand* does too; and its tag. Each once, in that order.
    """
    code = languages.tag(code)
    if not code:
        return ""
    words = [languages.native_name(code, code), name(code, "en")]
    if in_language:
        words.append(name(code, in_language))
    words.append(code)
    return " ".join(dict.fromkeys(word for word in words if word))
