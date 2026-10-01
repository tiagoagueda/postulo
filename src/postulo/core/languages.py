"""The languages Postulo speaks, and what each one needs.

Plain data, importable without Django: the settings module reads it, and so does the
``scripts/messages.py`` tool that keeps the catalogues current.

The names are the languages' own — somebody looking for their language in a list finds
"Deutsch", not the English word for it — and the plural rules are the standard gettext
ones, which Python's ``gettext`` evaluates at runtime.

**Which languages, and in what order.** #43 set the phases and each now has a milestone:

* **0.2.0** — the 24 official languages of the European Union, and Brazilian Portuguese
  beside the European: the first case of two regions of one language both being offered.
* **0.3.0** — the rest of the European continent (#118), then Africa (#70). Europe beyond
  the Union is where the neat rules stop: a language here may be at home somewhere that is
  not a state (Basque, Catalan, Galician, Welsh), and may be written in a script the rest
  of the list never uses (Greek, Cyrillic, Georgian, Armenian). Africa is bigger than a
  list: "every language of Africa" is some two thousand of them, so the rule drawn there is
  **official or national status in at least one African state, plus the cross-border lingua
  francas that outrank most of those in speakers** — twenty-nine, and a documented rule
  rather than a list assembled by feel. French, Portuguese, English and Spanish already
  carry a great deal of that continent, so the gap was smaller than the map suggests.
* **0.4.0** — Asia and South America (#71). **0.5.0** — the rest of the world (#72).

Every irregularity above is handled here rather than special-cased at each call site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

#: Language code → the ISO 3166-1 alpha-2 code of the country whose flag stands for it.
#:
#: The country, not the flag. What gets drawn is an SVG out of ``static/flags/``, picked by
#: the ``{% flag %}`` tag. This used to hold two regional indicator characters —
#: ``\U0001F1EB\U0001F1F7``, two code points, no request, nothing for ``img-src 'self'`` to
#: block — and the comment here used to say that Windows drawing them as the letters ``FR``
#: was "a legible fallback and not a broken image". It is not a legible fallback. It looks
#: broken, because it is the machine showing you the raw material of a thing it cannot make,
#: and it looked broken on the maintainer's own desktop (#88). Segoe UI Emoji has never
#: contained the flag pairs and Microsoft has said it does not intend to add them, so this
#: was never going to age out.
#:
#: A regional variant carries its own country: `pt-BR` is Brazil and not Portugal, which is
#: the first case in Postulo of two regions of one language both being offered.
#:
#: Written out deliberately rather than derived from the code, because a language is not a
#: country: ``el`` is Greek and ``cs`` is Czech, and neither code says so. Every language
#: of the European Union happened to have one uncontested home, which is what made the
#: first pass tractable. The rest of Europe ends that, and in two different ways.
#:
#: A language may be at home somewhere that is not a state. Catalan's answer is not Spain —
#: that flag already stands for Spanish on this same list — and it is not nothing either:
#: it is ``ES-CT``, Catalonia's own; Basque's is ``ES-PV``, the ikurriña. So this table
#: holds ISO 3166-2 subdivisions as well as countries, and ``assets/flags.txt`` carries the
#: artwork for them.
#:
#: And a language may still have no answer at all, in which case ``flag_country`` returns
#: nothing and the picker closes the row up. Africa (#70) is where that stops being the
#: rare case: Arabic is twenty-two countries, Swahili is four, Hausa is two, and Sesotho is
#: Lesotho's as much as South Africa's. Thirteen of the twenty-nine are blank on purpose
#: rather than by oversight — ``ar``, ``ee``, ``ff``, ``ha``, ``ig``, ``ln``, ``om``,
#: ``ss``, ``st``, ``sw``, ``ti``, ``tn``, ``yo``. No flag beats a wrong flag, a wrong flag
#: about somebody's language is not a small wrong, and every caller copes with nothing.
FLAG_COUNTRIES: dict[str, str] = {
    "en-GB": "GB",
    "af": "ZA",
    "ak": "GH",
    "am": "ET",
    "bg": "BG",
    "bm": "ML",
    "bs": "BA",
    "ca": "ES-CT",
    "cs": "CZ",
    "cy": "GB-WLS",
    "da": "DK",
    "de": "DE",
    "el": "GR",
    "es": "ES",
    "et": "EE",
    "eu": "ES-PV",
    "fi": "FI",
    "fr-FR": "FR",
    "ga": "IE",
    "gl": "ES-GA",
    "hr": "HR",
    "hu": "HU",
    "hy": "AM",
    "is": "IS",
    "it": "IT",
    "ka": "GE",
    "kab": "DZ",
    "lb": "LU",
    "lt": "LT",
    "lv": "LV",
    "mg": "MG",
    "mk": "MK",
    "mt": "MT",
    "nb": "NO",
    "nl": "NL",
    "nr": "ZA",
    "ny": "MW",
    "pl": "PL",
    "pt-PT": "PT",
    "pt-BR": "BR",
    "ro": "RO",
    "rw": "RW",
    "sk": "SK",
    "sl": "SI",
    "sn": "ZW",
    "so": "SO",
    "sq": "AL",
    "sr-Cyrl": "RS",
    "sv": "SE",
    "tr": "TR",
    "ts": "ZA",
    "uk": "UA",
    "ve": "ZA",
    "wo": "SN",
    "xh": "ZA",
    "zu": "ZA",
}


def flag_country(code: str) -> str:
    """The country whose flag stands for a language, or nothing where none is right.

    Nothing is a perfectly good answer and the interface must cope with it: Europe beyond
    the Union already brings languages with no single home, and they are left blank rather
    than given somebody's best guess.
    """
    return FLAG_COUNTRIES.get(find(code, FLAG_COUNTRIES), "")


#: Language tag → the language's own name for itself.
#: The order is the order of the picker: alphabetical by code, source language first.
NATIVE_NAMES: dict[str, str] = {
    "en-GB": "English (United Kingdom)",
    "af": "Afrikaans",
    "ak": "Akan",
    "am": "አማርኛ",
    "ar": "العربية",
    "bg": "български",
    "bm": "Bamanankan",
    "bs": "bosanski",
    "ca": "català",
    "cs": "čeština",
    "cy": "Cymraeg",
    "da": "dansk",
    "de": "Deutsch",
    "ee": "Eʋegbe",
    "el": "Ελληνικά",
    "es": "español",
    "et": "eesti",
    "eu": "euskara",
    "ff": "Pulaar",
    "fi": "suomi",
    "fr-FR": "français (France)",
    "ga": "Gaeilge",
    "gl": "galego",
    "ha": "Hausa",
    "hr": "hrvatski",
    "hu": "magyar",
    "hy": "հայերեն",
    "ig": "Igbo",
    "is": "íslenska",
    "it": "italiano",
    "ka": "ქართული",
    "kab": "Taqbaylit",
    "lb": "Lëtzebuergesch",
    "ln": "Lingála",
    "lt": "lietuvių",
    "lv": "latviešu",
    "mg": "Malagasy",
    "mk": "македонски",
    "mt": "Malti",
    "nb": "norsk bokmål",
    "nl": "Nederlands",
    "nr": "isiNdebele",
    "ny": "Chichewa",
    "om": "Afaan Oromoo",
    "pl": "polski",
    "pt-PT": "português (Portugal)",
    "pt-BR": "português (Brasil)",
    "ro": "română",
    "rw": "Ikinyarwanda",
    "sk": "slovenčina",
    "sl": "slovenščina",
    "sn": "chiShona",
    "so": "Soomaali",
    "sq": "shqip",
    #: Serbian is written in two scripts, and this catalogue is the Cyrillic one. The
    #: registry suppresses no script for `sr`, so the tag says which (#337); a Latin
    #: catalogue, the day there is one, is `sr-Latn`.
    "sr-Cyrl": "српски",
    "ss": "siSwati",
    "st": "Sesotho",
    "sv": "svenska",
    "sw": "Kiswahili",
    "ti": "ትግርኛ",
    "tn": "Setswana",
    "tr": "Türkçe",
    "ts": "Xitsonga",
    "uk": "українська",
    "ve": "Tshivenḓa",
    "wo": "Wolof",
    "xh": "isiXhosa",
    "yo": "Yorùbá",
    "zu": "isiZulu",
}

#: What ``settings.LANGUAGES`` is built from.
LANGUAGES: list[tuple[str, str]] = list(NATIVE_NAMES.items())

#: The source language: catalogues translate from it, and it has none of its own.
SOURCE = "en-GB"


# ------------------------------------------------------------------ what a code is
#
# A BCP 47 tag (RFC 5646), written in its canonical form: `pt-BR`, `sr-Cyrl`, `de` (#337).
# Tags compare without regard to case, so `pt-br` was never wrong to a machine. It was a
# second spelling, and Postulo had three: Django's lower case in what it stored and sent,
# Word's in one export, and gettext's `pt_BR` in the directories. The last is forced and is
# not a tag at all. Everything else is written by `tag` and compared by `find` and `match`,
# here and nowhere else.

#: How long a tag may be. RFC 5646 section 4.4.1 advises room for thirty-five characters,
#: which holds `ca-ES-valencia` and everything else anybody is likely to write.
MAX_LENGTH = 35

#: What a tag has to look like to be one: RFC 5646's ``langtag``, with a primary subtag of
#: two or three letters, which is every language the registry holds. The grammar also allows
#: one of five to eight letters, reserved and never used, and allowing it here would let
#: ``english`` through.
#:
#: The shape and nothing more. Whether ``xx-YY`` names anything is the registry's to say,
#: and only Postulo's own list is held to that (`tests/test_language_registry.py`): a
#: document may be in a language Postulo has never heard of.
#:
#: ASCII, because ``[a-z]`` ignoring case otherwise takes the long s and the Kelvin sign,
#: which fold to ``s`` and ``k`` and are neither.
_SHAPE = re.compile(
    r"(?P<language>[a-z]{2,3})(?:-[a-z]{3}){0,3}"
    r"(?:-(?P<script>[a-z]{4}))?"
    r"(?:-(?P<region>[a-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*"
    r"(?:-[0-9a-wy-z](?:-[a-z0-9]{2,8})+)*"
    r"(?:-x(?:-[a-z0-9]{1,8})+)?",
    re.ASCII | re.IGNORECASE,
)


def _text(code) -> str:
    """What was given, as text with hyphens: gettext's ``pt_BR`` is read, and never written."""
    return code.strip().replace("_", "-") if isinstance(code, str) else ""


def _parsed(code) -> re.Match | None:
    text = _text(code)
    return _SHAPE.fullmatch(text) if len(text) <= MAX_LENGTH else None


def well_formed(code) -> bool:
    """Whether this is shaped like a language tag, in any case and with either separator.

    What a value taken in from outside is held to -- a form, a file, an address -- before it
    is stored. Nothing, and anything that is not text, is not one.
    """
    return _parsed(code) is not None


def tag(code) -> str:
    """A language code in its canonical form: ``pt-BR``, ``sr-Cyrl``, ``zh-Hant-TW``, ``de``.

    RFC 5646 section 2.1.1: lower case throughout, except a subtag of two letters, written
    in upper case, and one of four, with a capital first -- unless it is the first subtag or
    comes after a singleton, where neither is a region or a script any more. So the rule is
    about where a subtag stands and how long it is, and ``zh-yue`` keeps its three letters
    lower.

    Never refuses. This is called where a value is read as well as where one is taken in,
    so what is not a tag comes back as it was given, without the space around it, and
    `well_formed` is what decides whether to believe it. Nothing comes back as nothing.
    """
    if not isinstance(code, str):
        return ""
    if not well_formed(code):
        return code.strip()
    first, *rest = _text(code).split("-")
    written = [first.lower()]
    after_singleton = False
    for subtag in rest:
        after_singleton = after_singleton or len(subtag) == 1
        if not after_singleton and len(subtag) == 4 and subtag.isalpha():
            written.append(subtag.title())
        elif not after_singleton and len(subtag) == 2 and subtag.isalpha():
            written.append(subtag.upper())
        else:
            written.append(subtag.lower())
    return "-".join(written)


def primary(code) -> str:
    """The language itself, without script or region: ``pt`` of ``pt-BR``."""
    return _text(code).split("-", 1)[0].lower()


def script_of(code) -> str:
    """The script a tag is written in, as its ISO 15924 code: ``Cyrl`` of ``sr-Cyrl``.

    The one the tag names, else the one its language is ordinarily written in, which for
    everything absent from `SCRIPTS` is Latin.
    """
    found = _parsed(code)
    if found is not None and found["script"]:
        return found["script"].title()
    language = primary(code)
    return SCRIPTS.get(language, "Latn") if language else ""


def region_of(code) -> str:
    """The country or area a tag names, where it names one: ``BR`` of ``pt-BR``."""
    found = _parsed(code)
    return (found["region"] or "").upper() if found is not None else ""


#: A language the registry files under a macrolanguage → that macrolanguage.
#:
#: Only where two names for one thing meet in Postulo. The catalogue is Bokmål, ``nb``;
#: ESCO publishes its Norwegian under ``no``, Europass writes ``nor``, and a page says
#: ``lang="no"``. Compared as strings those never met, and a Norwegian reader was given the
#: English occupation names.
MACROLANGUAGE: dict[str, str] = {
    "nb": "no",
    "nn": "no",
}


def _folded(code) -> str:
    """A code as it is compared: case and separator are not part of what it says."""
    return _text(code).lower()


def find(code, among: Iterable[str] | None = None) -> str:
    """The one of ``among`` that is this code however it is spelt, as spelt there; else "".

    ``among`` is Postulo's own list where it is not given. The answer is always a member of
    what was searched, which is what lets it be used as a key into it.
    """
    wanted = _folded(code)
    if not wanted:
        return ""
    for one in NATIVE_NAMES if among is None else among:
        if _folded(one) == wanted:
            return one
    return ""


def _shortened(folded: str) -> Iterator[str]:
    """A tag, then the same with its last subtag taken off, and so on (RFC 4647, 3.4)."""
    while folded:
        yield folded
        folded = folded.rpartition("-")[0]
        # A single letter left at the end introduced what was just taken off.
        while "-" in folded and len(folded.rpartition("-")[2]) == 1:
            folded = folded.rpartition("-")[0]


def _renamed(folded: str) -> list[str]:
    """The same tag under the other names the registry knows its language by."""
    language, hyphen, rest = folded.partition("-")
    others = [MACROLANGUAGE[language]] if language in MACROLANGUAGE else []
    others += [member for member, macro in MACROLANGUAGE.items() if macro == language]
    return [other + hyphen + rest for other in others]


def matches(wanted, available: Iterable[str] | None = None) -> tuple[str, ...]:
    """Every one of ``available`` that ``wanted`` names, the closest first, as spelt there.

    Exactly, whatever the case. Then what was asked for, shortened from the end: ``pt-BR``
    takes ``pt``. Then the same under the language's other name, which is how ``nb`` and
    ``no`` meet. Then the rest of the family: ``pt-BR`` takes ``pt-PT`` rather than nothing,
    because a Brazilian reader given European Portuguese has read the entry, and one given
    English has not. Never another language.
    """
    folded = _folded(wanted)
    if not folded:
        return ()
    pool = list(NATIVE_NAMES if available is None else available)
    keys = [_folded(one) for one in pool]
    candidates = list(_shortened(folded))
    for other in _renamed(folded):
        candidates.extend(_shortened(other))
    order: list[int] = []
    for candidate in candidates:
        order.extend(i for i, key in enumerate(keys) if key == candidate and i not in order)
    family = primary(folded)
    order.extend(i for i, key in enumerate(keys) if primary(key) == family and i not in order)
    return tuple(pool[i] for i in order)


def match(wanted, available: Iterable[str] | None = None) -> str:
    """The closest of ``available`` to ``wanted``, as spelt there, or "" if none is close.

    See `matches` for what close means. ``available`` is Postulo's own list where it is not
    given.
    """
    found = matches(wanted, available)
    return found[0] if found else ""


def same(one, other) -> bool:
    """Whether two codes are the same tag, however each is spelt. Nothing is the same as nothing."""
    return _folded(one) == _folded(other)


def native_name(code, default: str = "") -> str:
    """What a language on Postulo's list calls itself, or ``default`` for one that is not on it."""
    return NATIVE_NAMES.get(find(code), default)


# ------------------------------------------------------- the language of the moment
#
# Django answers `get_language()` in lower case once a translation is active and with
# `LANGUAGE_CODE` as it is written when none is, so the same call says `pt-br` or `en-GB`
# depending on what happened earlier in the request. Nothing else in Postulo asks it. And it
# is told a language through these, because a stored code need not be one Postulo has a
# catalogue for: `pt-AO` is somebody's CV, `sr` an older record, `no` a file from elsewhere.


def current() -> str:
    """The language this request, or this block, is being drawn in, as Postulo writes it.

    Never empty: with nothing active it is the instance's own language.
    """
    from django.conf import settings
    from django.utils.translation import get_language

    active = get_language() or settings.LANGUAGE_CODE
    return find(active) or tag(active)


def catalogue(code) -> str:
    """The language to draw something in when it is declared to be in ``code``.

    Postulo's own nearest: a letter in ``pt-AO`` takes the Portuguese catalogue, where
    activating ``pt-AO`` itself finds none and prints English headings over a Portuguese
    letter. A language Postulo does not speak is still handed on as it is, since Django's
    own month names and formats may know it. What is not a tag at all is nothing.
    """
    return match(code) or (tag(code) if well_formed(code) else "")


def activate(code) -> None:
    """Draw the rest of this request in ``code``, or leave it as it is where that is nothing."""
    from django.utils import translation

    language = catalogue(code)
    if language:
        translation.activate(language)


def override(code):
    """A block drawn in ``code``, as `django.utils.translation.override` and through `catalogue`.

    Nothing leaves the language as it is, which is what Django does with an empty string.
    """
    from django.utils import translation

    return translation.override(catalogue(code))


_TWO = "nplurals=2; plural=(n != 1);"

#: gettext ``Plural-Forms`` per language, written into each catalogue's header.
PLURAL_FORMS: dict[str, str] = {
    "af": _TWO,
    "ak": "nplurals=2; plural=(n > 1);",
    "am": "nplurals=2; plural=(n > 1);",
    #: Six: zero, one, two, a few, many and everything else — more forms than any
    #: other language Postulo carries, and the reason a form count is read from the
    #: rule rather than assumed.
    "ar": (
        "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : "
        "n%100>=11 ? 4 : 5);"
    ),
    "bg": _TWO,
    #: One. Bamanankan, Igbo, chiShona and Wolof do not distinguish number on the
    #: noun at all, so their catalogues carry a single form and a second would be
    #: filled with a guess.
    "bm": "nplurals=1; plural=0;",
    #: The same three-form rule as Croatian and Serbian, written out rather than
    #: shared: these are separate languages, and a shared constant would invite the
    #: next one to inherit a rule nobody checked.
    "bs": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "ca": _TWO,
    "cs": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    #: Four, and the one language here whose rule singles out particular numbers:
    #: 1 and 2 take their own forms, 8 and 11 share a fourth, and everything else
    #: falls to the third. Nothing about `nplurals=4` predicts that shape.
    "cy": "nplurals=4; plural=(n==1) ? 0 : (n==2) ? 1 : (n != 8 && n != 11) ? 2 : 3;",
    "da": _TWO,
    "de": _TWO,
    "ee": _TWO,
    "el": _TWO,
    "es": _TWO,
    "et": _TWO,
    "eu": _TWO,
    "ff": _TWO,
    "fi": _TWO,
    "fr-FR": "nplurals=2; plural=(n > 1);",
    "ga": ("nplurals=5; plural=(n==1 ? 0 : n==2 ? 1 : (n>2 && n<7) ? 2 :(n>6 && n<11) ? 3 : 4);"),
    "gl": _TWO,
    "ha": _TWO,
    "hr": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "hu": _TWO,
    #: Not `_TWO`. Armenian counts zero with the singular — *0 դիմում*, the way
    #: French does — so the rule is `n > 1`. The noun after a numeral does not
    #: inflect either way, which is exactly what makes the wrong rule easy to miss.
    "hy": "nplurals=2; plural=(n > 1);",
    "ig": "nplurals=1; plural=0;",
    #: Not `_TWO`: two forms, but the last digit decides rather than the value. 21 and
    #: 31 take the singular like 1 — *tuttugu og ein umsókn* — while 11 takes the plural
    #: like 12, *ellefu umsóknir*.
    "is": "nplurals=2; plural=(n%10!=1 || n%100==11);",
    "it": _TWO,
    "ka": _TWO,
    "kab": "nplurals=2; plural=(n > 1);",
    "lb": _TWO,
    "ln": "nplurals=2; plural=(n > 1);",
    "lt": (
        "nplurals=3; plural=(n%10==1 && (n%100<11 || n%100>19) ? 0 : n%10>=2 && n%10<=9 && "
        "(n%100<11 || n%100>19) ? 1 : 2);"
    ),
    "lv": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n != 0 ? 1 : 2);",
    "mg": "nplurals=2; plural=(n > 1);",
    #: Two forms, but the digit decides and not the value, the way Icelandic does
    #: it: 21 and 101 take the singular with 1, while 11 takes the plural.
    "mk": "nplurals=2; plural=(n%10==1 && n%100!=11) ? 0 : 1;",
    "mt": (
        "nplurals=4; plural=(n==1 ? 0 : n==0 || ( n%100>1 && n%100<11) ? 1 : "
        "(n%100>10 && n%100<20 ) ? 2 : 3);"
    ),
    "nb": _TWO,
    "nl": _TWO,
    "nr": _TWO,
    "ny": _TWO,
    "om": _TWO,
    "pl": (
        "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "pt-PT": _TWO,
    #: Not `_TWO`. Brazilian Portuguese treats zero as plural — *0 candidaturas*,
    #: where European Portuguese says *0 candidatura* — so the rule is `n > 1` and not
    #: `n != 1`. Copying the European line without looking would make every count on
    #: every page ungrammatical for the language's largest population.
    "pt-BR": "nplurals=2; plural=(n > 1);",
    "ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);",
    "rw": _TWO,
    "sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
    "sn": "nplurals=1; plural=0;",
    "so": _TWO,
    "sq": _TWO,
    "sr-Cyrl": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "ss": _TWO,
    "st": _TWO,
    "sv": _TWO,
    "sw": _TWO,
    "ti": "nplurals=2; plural=(n > 1);",
    "tn": _TWO,
    #: Not `_TWO`. Turkish counts nothing after a numeral — *bir başvuru*, *iki
    #: başvuru* — so both forms carry the same noun, and the split matters only for
    #: the rest of the sentence around it.
    "tr": "nplurals=2; plural=(n > 1);",
    "ts": _TWO,
    #: Three, and the rule is about the last digit rather than the value: 1, 21 and 101
    #: take the first form, 2-4 the second, and 11-14 the third despite ending in 1-4.
    "uk": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
        "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "ve": _TWO,
    "wo": "nplurals=1; plural=0;",
    "xh": _TWO,
    "yo": "nplurals=2; plural=(n > 1);",
    "zu": "nplurals=2; plural=(n > 1);",
}


def plural_forms(code: str) -> str | None:
    """The ``Plural-Forms`` line of a language's catalogue, or None for one with no rule here."""
    return PLURAL_FORMS.get(find(code, PLURAL_FORMS))


def nplurals(code: str) -> int:
    """How many plural forms a language's catalogue carries."""
    forms = plural_forms(code) or _TWO
    return int(forms.split("nplurals=", 1)[1].split(";", 1)[0])


def locale_dir_name(code: str) -> str:
    """``fr-FR`` → ``fr_FR``, ``de`` → ``de``: the directory Django looks in.

    A gettext locale name and not a language tag: the underscore is gettext's, and this
    is the only place one is made from the other.
    """
    from django.utils.translation import to_locale

    return to_locale(code)


def translation_status() -> dict[str, dict[str, int]]:
    """How far along each catalogue is, from the ``status.json`` the tooling writes.

    Read once per process; the file changes only when a catalogue does, and the tooling
    rewrites it then. An installation without the file simply shows names alone.
    """
    global _STATUS
    if _STATUS is None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "locale" / "status.json"
        try:
            _STATUS = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _STATUS = {}
    return _STATUS


_STATUS: dict[str, dict[str, int]] | None = None


def status_of(code, status: Mapping[str, dict[str, int]] | None = None) -> dict[str, int] | None:
    """One language's row of the status, however the code and the file each spell it.

    ``status`` is `translation_status()` where it is not given; a caller that has already
    read it hands it over rather than asking for it once a row.
    """
    status = translation_status() if status is None else status
    return status.get(find(code, status))


#: The parts of a translation bar, in the order they are drawn from the inline start (#312).
PROGRESS_PARTS: tuple[str, ...] = ("reviewed", "draft", "untranslated")

#: The space left between two parts of the bar, in the units the parts are measured in.
#:
#: Three colours and a background cannot each stand 3:1 from all the others: on white that
#: takes a range of 27 to 1 and white to black is only 21. So the parts never touch. Each
#: is 3:1 against the card, and the gap between two of them *is* the card, which is what
#: SC 1.4.11 measures a part against; under forced colours it is what still tells one part
#: from the next. Outside the hundred rather than taken from it, so a part of 1 % is drawn
#: as 1 % and not as a sliver with a hole in it.
BAR_GAP = 1


@dataclass(frozen=True)
class BarSegment:
    """One part of the bar: what it counts, how wide it is, and where it starts."""

    part: str
    count: int
    #: Whole percent of the hundred the parts share between them.
    width: int
    #: Where the part starts, in the bar's own units, measured from the left edge of the
    #: drawing: already mirrored for a page written right to left.
    x: int


@dataclass(frozen=True)
class TranslationProgress:
    """How far along a language is, as three counts that add up to the whole (#312).

    ``status.json`` counts what ``scripts/messages.py stats`` finds in the catalogues:
    ``translated`` is every string with something written in each form, ``drafts`` those
    of them still flagged ``draft``, and ``reviewed`` those of them flagged neither
    ``draft`` nor ``fuzzy`` -- what a speaker has read and settled. A ``fuzzy`` string with
    text in it is therefore translated and not reviewed, and lands with the drafts, which
    is where it belongs: somebody wrote it and nobody has settled it. One with nothing
    written is untranslated, like any other string with nothing written.
    """

    total: int
    reviewed: int
    draft: int
    untranslated: int
    #: The parts to draw, in `PROGRESS_PARTS` order and only those with something in them.
    segments: tuple[BarSegment, ...]
    #: The width of the whole drawing: the hundred plus a gap between each two parts.
    span: int


def whole_percents(counts: Sequence[int]) -> list[int]:
    """Each count as a whole percentage of their sum, adding up to exactly 100.

    The largest-remainder method: every share is rounded down, and the points that leaves
    over go one each to the shares that rounding cost the most. Two rules sit on top of it,
    because a bar is read at a glance and a glance believes what it sees:

    * a count of nothing is 0, and is never drawn;
    * a count of anything is at least 1, so a single string untranslated among two
      thousand is a sliver rather than nothing at all.

    With the sum held at 100, those two are what make the promises that matter: a bar
    with anything untranslated never reads as 100 % done, and a bar with nothing
    untranslated never reads as 99. A share raised to 1 is paid for by the share that
    rounding down cost least, which is always one wider than 1.

    Integer arithmetic throughout, so a remainder is compared exactly and not as a float
    that is almost the same as its neighbour.
    """
    counts = [max(int(count), 0) for count in counts]
    total = sum(counts)
    if not total:
        return [0] * len(counts)
    widths = [100 * count // total for count in counts]
    remainders = [100 * count % total for count in counts]
    raised = {i for i, count in enumerate(counts) if count and not widths[i]}
    for i in raised:
        widths[i] = 1
    leftover = 100 - sum(widths)
    if leftover > 0:
        eligible = [i for i, count in enumerate(counts) if count and i not in raised]
        # Most cost by rounding first; a tie goes to the part drawn first.
        for i in sorted(eligible, key=lambda i: (-remainders[i], i))[:leftover]:
            widths[i] += 1
    taken = [0] * len(counts)
    while sum(widths) > 100:
        spare = [i for i, width in enumerate(widths) if width > 1]
        i = min(spare, key=lambda i: (taken[i], remainders[i], -widths[i], i))
        widths[i] -= 1
        taken[i] += 1
    return widths


def translation_progress(
    row: Mapping[str, int] | None, *, rtl: bool = False
) -> TranslationProgress | None:
    """A language's line in ``status.json`` as a bar: three counts, three widths (#312).

    Nothing where there is nothing to draw -- no line for the language, or a catalogue
    with no strings in it -- and the caller shows the name alone, as it does without the
    file. The counts are clamped into the total, so a hand-edited or half-written file
    draws a bar that adds up rather than one that runs off its end.

    ``rtl`` is the page's direction, not the language's: the bar fills from the inline
    start of the page it sits on, like the words beside it.
    """
    if not row:
        return None
    total = max(int(row.get("total") or 0), 0)
    if not total:
        return None
    translated = min(max(int(row.get("translated") or 0), 0), total)
    reviewed = row.get("reviewed")
    if reviewed is None:
        # A file older than the `reviewed` count: everything translated and not a draft.
        reviewed = translated - int(row.get("drafts") or 0)
    reviewed = min(max(int(reviewed), 0), translated)
    counts = (reviewed, translated - reviewed, total - translated)

    segments: list[BarSegment] = []
    start = 0
    for part, count, width in zip(PROGRESS_PARTS, counts, whole_percents(counts), strict=True):
        if not width:
            continue
        segments.append(BarSegment(part=part, count=count, width=width, x=start))
        start += width + BAR_GAP
    span = 100 + BAR_GAP * (len(segments) - 1)
    if rtl:
        segments = [
            BarSegment(part=s.part, count=s.count, width=s.width, x=span - s.x - s.width)
            for s in segments
        ]
    return TranslationProgress(
        total=total,
        reviewed=counts[0],
        draft=counts[1],
        untranslated=counts[2],
        segments=tuple(segments),
        span=span,
    )


#: Language subtags written right to left.
#:
#: Postulo's own list rather than Django's ``LANGUAGES_BIDI``, and the reason is #43: Django
#: knows the languages Django ships with, and Postulo is going past them. The African set
#: (#70) brings Arabic; the Asian set (#71) brings Hebrew, Persian and Urdu. One list that
#: both the interface and a rendered document read is one place to add a language to, and
#: one answer when they are asked the same question.
#:
#: Matched on the primary subtag, so ``ar-EG`` is as right to left as ``ar``. Direction is a
#: property of the script rather than of the region, and no region of Arabic is written the
#: other way. Where a tag says its script, that is what is read instead: see `is_rtl`.
RTL: frozenset[str] = frozenset(
    {
        "ar",  # Arabic
        "arc",  # Aramaic
        "ckb",  # Central Kurdish (Sorani)
        "dv",  # Divehi
        "fa",  # Persian
        "he",  # Hebrew
        "ks",  # Kashmiri
        "ku",  # Kurdish, where written in the Arabic script
        "nqo",  # N'Ko
        "prs",  # Dari
        "ps",  # Pashto
        "sd",  # Sindhi
        "syr",  # Syriac
        "ug",  # Uyghur
        "ur",  # Urdu
        "yi",  # Yiddish
    }
)


#: The scripts written right to left, by their ISO 15924 code, which is the subtag a tag
#: states one with.
RTL_SCRIPTS: frozenset[str] = frozenset(
    {
        "Adlm",  # Adlam
        "Arab",  # Arabic
        "Hebr",  # Hebrew
        "Mand",  # Mandaic
        "Nkoo",  # N'Ko
        "Rohg",  # Hanifi Rohingya
        "Samr",  # Samaritan
        "Syrc",  # Syriac
        "Thaa",  # Thaana
    }
)


def is_rtl(code: str) -> bool:
    """Whether a language tag names something written right to left.

    By the script it states, where it states one: ``ku-Latn`` is Kurdish in the Latin
    alphabet and reads left to right, ``az-Arab`` is Azerbaijani in the Arabic one and does
    not. Read from the language alone, the first was drawn backwards (#337). A tag that
    says nothing about its script is read by its language, as it always was.
    """
    found = _parsed(code)
    if found is not None and found["script"]:
        return found["script"].title() in RTL_SCRIPTS
    return primary(code) in RTL


def direction(code: str) -> str:
    """``"rtl"`` or ``"ltr"``, for the ``dir`` attribute of a page or a document.

    Always one of the two, never empty: ``dir=""`` is not the same as an absent attribute
    in every engine, and a document that declines to say is a document that gets guessed at.
    """
    return "rtl" if is_rtl(code) else "ltr"


#: Language subtag → the script it is written in, where that is not the Latin alphabet.
#:
#: Only the exceptions are listed: everything absent from this map is Latin, which is the
#: overwhelming majority and would be noise here. What it exists for is fonts. A script the
#: rendering machine cannot draw comes out as a row of empty boxes, and a box on somebody's
#: CV is worse than English — so ``tests/test_fonts.py`` reads this and insists the
#: container image installs a font package that covers every script Postulo offers.
#:
#: Named by its ISO 15924 code, ``Cyrl`` and not *Cyrillic*, because that is what a tag
#: says a script with: ``sr-Cyrl`` states its own, and `script_of` answers for both in one
#: vocabulary. This is the script the registry says goes without saying for the language
#: (its ``Suppress-Script``), and `tests/test_language_registry.py` holds each to that.
SCRIPTS: dict[str, str] = {
    "am": "Ethi",
    "ar": "Arab",
    "bg": "Cyrl",
    "el": "Grek",
    "mk": "Cyrl",
    "ti": "Ethi",
    "uk": "Cyrl",
}

#: A script's code → what it is called, for the two places that say it to a person: the
#: server overview and ``manage.py check_fonts``. In English, as those two always were.
SCRIPT_NAMES: dict[str, str] = {
    "Arab": "Arabic",
    "Beng": "Bengali",
    "Cyrl": "Cyrillic",
    "Deva": "Devanagari",
    "Ethi": "Ethiopic",
    "Grek": "Greek",
    "Gujr": "Gujarati",
    "Guru": "Gurmukhi",
    "Hang": "Hangul",
    "Hani": "Han",
    "Hebr": "Hebrew",
    "Hira": "Hiragana",
    "Kana": "Katakana",
    "Khmr": "Khmer",
    "Laoo": "Lao",
    "Latn": "Latin",
    "Mymr": "Myanmar",
    "Sinh": "Sinhala",
    "Taml": "Tamil",
    "Telu": "Telugu",
    "Tfng": "Tifinagh",
    "Thai": "Thai",
}


def script_name(script: str) -> str:
    """What a script is called in words, or its code where nobody has written a name for it."""
    return SCRIPT_NAMES.get(script, script)


def scripts_offered() -> set[str]:
    """Every script but Latin among the languages Postulo currently offers, by its code."""
    return {script_of(code) for code, _name in LANGUAGES} - {"Latn"}
