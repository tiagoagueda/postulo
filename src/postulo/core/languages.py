"""The languages Postulo speaks, and what each one needs.

Plain data, importable without Django: the settings module reads it, and so does the
``scripts/messages.py`` tool that keeps the catalogues current.

Phase 1 is every official language of the European Union. The names are the languages'
own — someone looking for their language in a list finds "Deutsch", not the English word
for it — and the plural rules are the standard gettext ones, which Python's ``gettext``
evaluates at runtime.
"""

from __future__ import annotations

#: Language code as Django writes it → the language's own name for itself.
#: The order is the order of the picker: alphabetical by code, source language first.
NATIVE_NAMES: dict[str, str] = {
    "en-gb": "English (United Kingdom)",
    "bg": "български",
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
    "it": "italiano",
    "lt": "lietuvių",
    "lv": "latviešu",
    "mt": "Malti",
    "nl": "Nederlands",
    "pl": "polski",
    "pt-pt": "português (Portugal)",
    "ro": "română",
    "sk": "slovenčina",
    "sl": "slovenščina",
    "sv": "svenska",
}

#: What ``settings.LANGUAGES`` is built from.
LANGUAGES: list[tuple[str, str]] = list(NATIVE_NAMES.items())

#: The source language: catalogues translate from it, and it has none of its own.
SOURCE = "en-gb"

_TWO = "nplurals=2; plural=(n != 1);"

#: gettext ``Plural-Forms`` per language, written into each catalogue's header.
PLURAL_FORMS: dict[str, str] = {
    "bg": _TWO,
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
    "nl": _TWO,
    "pl": (
        "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "pt-pt": _TWO,
    "ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);",
    "sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
    "sv": _TWO,
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
