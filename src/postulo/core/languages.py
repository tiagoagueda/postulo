"""The languages Postulo speaks, and what each one needs.

Plain data, importable without Django: the settings module reads it, and so does the
``scripts/messages.py`` tool that keeps the catalogues current.

Phase 1 is every official language of the European Union. The names are the languages'
own — someone looking for their language in a list finds "Deutsch", not the English word
for it — and the plural rules are the standard gettext ones, which Python's ``gettext``
evaluates at runtime.
"""

from __future__ import annotations

#: Language code → the flag of the country the language is most plainly at home in.
#:
#: Two regional indicator characters, not an image: ``\U0001F1EB\U0001F1F7`` is two code
#: points, costs no request at all, and cannot be blocked by ``img-src 'self'``. It renders
#: as a flag on macOS, iOS, Android and most Linux desktops. **Windows shows the two
#: letters instead** — ``FR`` rather than a French flag — which is a legible fallback and
#: not a broken image, and is worth knowing before it is reported as a bug.
#:
#: Written out deliberately rather than derived from the code, because a language is not a
#: country: ``el`` is Greek and ``cs`` is Czech, and neither code says so. For the European
#: Union set every language has one uncontested home, which is what makes this tractable
#: now. It will not survive #43 moving past Europe — Spanish is not only Spain, Arabic is
#: not one flag — and the rule there is that a language with no uncontested home gets no
#: flag at all. No flag beats a wrong flag.
FLAGS: dict[str, str] = {
    "en-gb": "\U0001f1ec\U0001f1e7",
    "bg": "\U0001f1e7\U0001f1ec",
    "cs": "\U0001f1e8\U0001f1ff",
    "da": "\U0001f1e9\U0001f1f0",
    "de": "\U0001f1e9\U0001f1ea",
    "el": "\U0001f1ec\U0001f1f7",
    "es": "\U0001f1ea\U0001f1f8",
    "et": "\U0001f1ea\U0001f1ea",
    "fi": "\U0001f1eb\U0001f1ee",
    "fr-fr": "\U0001f1eb\U0001f1f7",
    "ga": "\U0001f1ee\U0001f1ea",
    "hr": "\U0001f1ed\U0001f1f7",
    "hu": "\U0001f1ed\U0001f1fa",
    "it": "\U0001f1ee\U0001f1f9",
    "lt": "\U0001f1f1\U0001f1f9",
    "lv": "\U0001f1f1\U0001f1fb",
    "mt": "\U0001f1f2\U0001f1f9",
    "nl": "\U0001f1f3\U0001f1f1",
    "pl": "\U0001f1f5\U0001f1f1",
    "pt-pt": "\U0001f1f5\U0001f1f9",
    "ro": "\U0001f1f7\U0001f1f4",
    "sk": "\U0001f1f8\U0001f1f0",
    "sl": "\U0001f1f8\U0001f1ee",
    "sv": "\U0001f1f8\U0001f1ea",
}


def flag(code: str) -> str:
    """The flag for a language, or nothing where none is right.

    Nothing is a perfectly good answer and the interface must cope with it: the phase of
    #43 beyond Europe brings languages with no single home, and they will be left blank
    rather than given somebody's best guess.
    """
    return FLAGS.get(code, "")


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
