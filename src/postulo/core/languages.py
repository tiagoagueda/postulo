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
#: A regional variant carries its own country: `pt-br` is Brazil and not Portugal, which is
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
    "en-gb": "GB",
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
    "fr-fr": "FR",
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
    "pt-pt": "PT",
    "pt-br": "BR",
    "ro": "RO",
    "rw": "RW",
    "sk": "SK",
    "sl": "SI",
    "sn": "ZW",
    "so": "SO",
    "sq": "AL",
    "sr": "RS",
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
    return FLAG_COUNTRIES.get(code, "")


#: Language code as Django writes it → the language's own name for itself.
#: The order is the order of the picker: alphabetical by code, source language first.
NATIVE_NAMES: dict[str, str] = {
    "en-gb": "English (United Kingdom)",
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
    "fr-fr": "français (France)",
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
    "pt-pt": "português (Portugal)",
    "pt-br": "português (Brasil)",
    "ro": "română",
    "rw": "Ikinyarwanda",
    "sk": "slovenčina",
    "sl": "slovenščina",
    "sn": "chiShona",
    "so": "Soomaali",
    "sq": "shqip",
    "sr": "српски",
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
SOURCE = "en-gb"

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
    "fr-fr": "nplurals=2; plural=(n > 1);",
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
    "pt-pt": _TWO,
    #: Not `_TWO`. Brazilian Portuguese treats zero as plural — *0 candidaturas*,
    #: where European Portuguese says *0 candidatura* — so the rule is `n > 1` and not
    #: `n != 1`. Copying the European line without looking would make every count on
    #: every page ungrammatical for the language's largest population.
    "pt-br": "nplurals=2; plural=(n > 1);",
    "ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);",
    "rw": _TWO,
    "sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
    "sn": "nplurals=1; plural=0;",
    "so": _TWO,
    "sq": _TWO,
    "sr": (
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


def nplurals(code: str) -> int:
    """How many plural forms a language's catalogue carries."""
    forms = PLURAL_FORMS.get(code, _TWO)
    return int(forms.split("nplurals=", 1)[1].split(";", 1)[0])


def locale_dir_name(code: str) -> str:
    """``fr-fr`` → ``fr_FR``, ``de`` → ``de``: the directory Django looks in."""
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


#: Language subtags written right to left.
#:
#: Postulo's own list rather than Django's ``LANGUAGES_BIDI``, and the reason is #43: Django
#: knows the languages Django ships with, and Postulo is going past them. The African set
#: (#70) brings Arabic; the Asian set (#71) brings Hebrew, Persian and Urdu. One list that
#: both the interface and a rendered document read is one place to add a language to, and
#: one answer when they are asked the same question.
#:
#: Matched on the primary subtag, so ``ar-eg`` is as right to left as ``ar``. Direction is a
#: property of the script rather than of the region, and no region of Arabic is written the
#: other way.
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


def is_rtl(code: str) -> bool:
    """Whether a language tag names a language written right to left."""
    return (code or "").strip().lower().replace("_", "-").split("-", 1)[0] in RTL


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
SCRIPTS: dict[str, str] = {
    "am": "Ethiopic",
    "ar": "Arabic",
    "bg": "Cyrillic",
    "el": "Greek",
    "ti": "Ethiopic",
}


def scripts_offered() -> set[str]:
    """Every non-Latin script among the languages Postulo currently offers."""
    return {SCRIPTS[code] for code, _name in LANGUAGES if code in SCRIPTS}
