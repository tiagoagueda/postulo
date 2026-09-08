"""The languages Postulo speaks, and what each one needs.

Plain data, importable without Django: the settings module reads it, and so does the
``scripts/messages.py`` tool that keeps the catalogues current.

Europe first: the twenty-four official languages of the European Union, Brazilian
Portuguese beside the European — the first case of two regions of one language both being
offered — and then the rest of the continent, which is where the neat rules stop. A
language on this list may have no country of its own (Basque, Catalan, Galician, Welsh),
and may be written in a script the rest of the list never uses (Greek, Cyrillic, Georgian,
Armenian). Both are handled here rather than special-cased at each call site.

The names are the languages'
own — someone looking for their language in a list finds "Deutsch", not the English word
for it — and the plural rules are the standard gettext ones, which Python's ``gettext``
evaluates at runtime.
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
#: first pass tractable. The rest of Europe ends that: Basque, Catalan, Galician and Welsh
#: are spoken across borders or inside one state that already flies its flag for another
#: language, so they are absent from this table and ``flag_country`` answers with nothing.
#: No flag beats a wrong flag, and every caller already copes with the empty answer.
FLAG_COUNTRIES: dict[str, str] = {
    "en-gb": "GB",
    "bg": "BG",
    "bs": "BA",
    "cs": "CZ",
    "da": "DK",
    "de": "DE",
    "el": "GR",
    "es": "ES",
    "et": "EE",
    "fi": "FI",
    "fr-fr": "FR",
    "ga": "IE",
    "hr": "HR",
    "hu": "HU",
    "is": "IS",
    "it": "IT",
    "lt": "LT",
    "lv": "LV",
    "mt": "MT",
    "nb": "NO",
    "nl": "NL",
    "pl": "PL",
    "pt-pt": "PT",
    "pt-br": "BR",
    "ro": "RO",
    "sk": "SK",
    "sl": "SI",
    "sr": "RS",
    "sv": "SE",
    "tr": "TR",
    "uk": "UA",
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
    "bg": "български",
    "bs": "bosanski",
    "cs": "čeština",
    "da": "dansk",
    "de": "Deutsch",
    "el": "Ελληνικά",
    "es": "español",
    "et": "eesti",
    "fi": "suomi",
    "fr-fr": "français (France)",
    "ga": "Gaeilge",
    "hr": "hrvatski",
    "hu": "magyar",
    "is": "íslenska",
    "it": "italiano",
    "lt": "lietuvių",
    "lv": "latviešu",
    "mt": "Malti",
    "nb": "norsk bokmål",
    "nl": "Nederlands",
    "pl": "polski",
    "pt-pt": "português (Portugal)",
    "pt-br": "português (Brasil)",
    "ro": "română",
    "sk": "slovenčina",
    "sl": "slovenščina",
    "sr": "српски",
    "sv": "svenska",
    "tr": "Türkçe",
    "uk": "українська",
}

#: What ``settings.LANGUAGES`` is built from.
LANGUAGES: list[tuple[str, str]] = list(NATIVE_NAMES.items())

#: The source language: catalogues translate from it, and it has none of its own.
SOURCE = "en-gb"

_TWO = "nplurals=2; plural=(n != 1);"

#: gettext ``Plural-Forms`` per language, written into each catalogue's header.
PLURAL_FORMS: dict[str, str] = {
    "bg": _TWO,
    #: The same three-form rule as Croatian and Serbian, written out rather than
    #: shared: these are separate languages, and a shared constant would invite the
    #: next one to inherit a rule nobody checked.
    "bs": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "cs": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "da": _TWO,
    "de": _TWO,
    "el": _TWO,
    "es": _TWO,
    "et": _TWO,
    "fi": _TWO,
    "fr-fr": "nplurals=2; plural=(n > 1);",
    "ga": ("nplurals=5; plural=(n==1 ? 0 : n==2 ? 1 : (n>2 && n<7) ? 2 :(n>6 && n<11) ? 3 : 4);"),
    "hr": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "hu": _TWO,
    #: Not `_TWO`: two forms, but the last digit decides rather than the value. 21 and
    #: 31 take the singular like 1 — *tuttugu og ein umsókn* — while 11 takes the plural
    #: like 12, *ellefu umsóknir*.
    "is": "nplurals=2; plural=(n%10!=1 || n%100==11);",
    "it": _TWO,
    "lt": (
        "nplurals=3; plural=(n%10==1 && (n%100<11 || n%100>19) ? 0 : n%10>=2 && n%10<=9 && "
        "(n%100<11 || n%100>19) ? 1 : 2);"
    ),
    "lv": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n != 0 ? 1 : 2);",
    "mt": (
        "nplurals=4; plural=(n==1 ? 0 : n==0 || ( n%100>1 && n%100<11) ? 1 : "
        "(n%100>10 && n%100<20 ) ? 2 : 3);"
    ),
    "nb": _TWO,
    "nl": _TWO,
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
    "sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
    "sr": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
        "(n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "sv": _TWO,
    #: Not `_TWO`. Turkish counts nothing after a numeral — *bir başvuru*, *iki
    #: başvuru* — so both forms carry the same noun, and the split matters only for
    #: the rest of the sentence around it.
    "tr": "nplurals=2; plural=(n > 1);",
    #: Three, and the rule is about the last digit rather than the value: 1, 21 and 101
    #: take the first form, 2-4 the second, and 11-14 the third despite ending in 1-4.
    "uk": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
        "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
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
