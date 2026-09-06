"""The languages Postulo speaks, and what each one needs.

Plain data, importable without Django: the settings module reads it, and so does the
``scripts/messages.py`` tool that keeps the catalogues current.

The names are the languages' own — somebody looking for their language in a list finds
"Deutsch", not the English word for it — and the plural rules are the standard gettext
ones, which Python's ``gettext`` evaluates at runtime.

**Which languages, and in what order.** #43 set the phases and each now has a milestone:

* **0.2.0** — the 24 official languages of the European Union.
* **0.3.0** — Africa (#70). "Every language of Africa" is some two thousand of them, so the
  rule drawn here is: **a language with official or national status in at least one African
  state, plus the cross-border lingua francas that outrank most of those in speakers.**
  Twenty-nine of them, and a documented rule rather than a list assembled by feel. French,
  Portuguese, English and Spanish already carry a great deal of the continent, so the gap
  was smaller than the map suggests.
* **0.4.0** — Asia and South America (#71). **0.5.0** — the rest of the world (#72).
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
#: Union set every language had one uncontested home. Africa (#70) is where that stopped
#: being true, and the rule held: **a language with no uncontested home gets no flag at
#: all.** Arabic is twenty-two countries, Swahili is four, Hausa is two, and Sesotho is
#: Lesotho's as much as South Africa's. Those are blank on purpose, and the picker copes.
#: No flag beats a wrong flag, and a wrong flag about somebody's language is not a small
#: wrong.
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
    # Africa (#70). A flag only where one country is uncontestedly the language's
    # home. Absent for the cross-border ones by the rule above rather than by
    # oversight: ar, ee, ff, ha, ig, ln, om, ss, st, sw, ti, tn, yo.
    "af": "\U0001f1ff\U0001f1e6",
    "ak": "\U0001f1ec\U0001f1ed",
    "am": "\U0001f1ea\U0001f1f9",
    "bm": "\U0001f1f2\U0001f1f1",
    "kab": "\U0001f1e9\U0001f1ff",
    "mg": "\U0001f1f2\U0001f1ec",
    "nr": "\U0001f1ff\U0001f1e6",
    "ny": "\U0001f1f2\U0001f1fc",
    "rw": "\U0001f1f7\U0001f1fc",
    "sn": "\U0001f1ff\U0001f1fc",
    "so": "\U0001f1f8\U0001f1f4",
    "ts": "\U0001f1ff\U0001f1e6",
    "ve": "\U0001f1ff\U0001f1e6",
    "wo": "\U0001f1f8\U0001f1f3",
    "xh": "\U0001f1ff\U0001f1e6",
    "zu": "\U0001f1ff\U0001f1e6",
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
    # Africa (#70), alphabetical by code like the rest.
    "af": "Afrikaans",
    "ak": "Akan",
    "am": "አማርኛ",
    "ar": "العربية",
    "bm": "Bamanankan",
    "ee": "Eʋegbe",
    "ff": "Pulaar",
    "ha": "Hausa",
    "ig": "Igbo",
    "kab": "Taqbaylit",
    "ln": "Lingála",
    "mg": "Malagasy",
    "nr": "isiNdebele",
    "ny": "Chichewa",
    "om": "Afaan Oromoo",
    "rw": "Ikinyarwanda",
    "sn": "chiShona",
    "so": "Soomaali",
    "ss": "siSwati",
    "st": "Sesotho",
    "sw": "Kiswahili",
    "ti": "ትግርኛ",
    "tn": "Setswana",
    "ts": "Xitsonga",
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
    "af": _TWO,
    "ak": "nplurals=2; plural=(n > 1);",
    "am": "nplurals=2; plural=(n > 1);",
    # Arabic distinguishes zero, one, two, a few, many and everything else: six forms,
    # more than any other language Postulo carries.
    "ar": (
        "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : "
        "n%100>=11 ? 4 : 5);"
    ),
    "bm": "nplurals=1; plural=0;",
    "ee": _TWO,
    "ff": _TWO,
    "ha": _TWO,
    "ig": "nplurals=1; plural=0;",
    "kab": "nplurals=2; plural=(n > 1);",
    "ln": "nplurals=2; plural=(n > 1);",
    "mg": "nplurals=2; plural=(n > 1);",
    "nr": _TWO,
    "ny": _TWO,
    "om": _TWO,
    "rw": _TWO,
    "sn": "nplurals=1; plural=0;",
    "so": _TWO,
    "ss": _TWO,
    "st": _TWO,
    "sw": _TWO,
    "ti": "nplurals=2; plural=(n > 1);",
    "tn": _TWO,
    "ts": _TWO,
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
