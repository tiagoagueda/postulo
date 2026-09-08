"""The container image must be able to draw the scripts Postulo offers.

A missing glyph is not a degraded experience, it is a row of empty boxes, and a row of
empty boxes on somebody's CV is worse than the same CV in English. WeasyPrint draws with
whatever fonts the image has, and a Debian slim base has almost none — Postulo installed
`fonts-dejavu-core`, which covers Latin, Greek and Cyrillic and stops there. That was
enough for the European Union set and stopped being enough the moment Amharic, Tigrinya
and Arabic arrived (#70).

This reads the language list and the Dockerfile and insists they agree, so a language added
in a later phase cannot quietly ship a PDF nobody can read. It checks the *declaration*
rather than a running container: it costs milliseconds, needs no Docker, and fails on the
laptop of whoever added the language rather than in somebody's hands.
"""

import re
from pathlib import Path

from postulo.core import languages

DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "Dockerfile"

#: Which Debian font package covers which script. Deliberately short: adding a script
#: means deciding which package draws it, which is a decision worth making explicitly.
COVERAGE: dict[str, tuple[str, ...]] = {
    "Latin": ("fonts-dejavu-core", "fonts-noto-core"),
    "Greek": ("fonts-dejavu-core", "fonts-noto-core"),
    "Cyrillic": ("fonts-dejavu-core", "fonts-noto-core"),
    "Arabic": ("fonts-noto-core",),
    "Ethiopic": ("fonts-noto-core",),
    "Hebrew": ("fonts-noto-core",),
    "Devanagari": ("fonts-noto-core",),
    "Thai": ("fonts-noto-core",),
    # CJK is its own package and its own size; #71 decides when it arrives.
    "Han": ("fonts-noto-cjk",),
    "Hiragana": ("fonts-noto-cjk",),
    "Hangul": ("fonts-noto-cjk",),
}


def installed_font_packages() -> set[str]:
    """The font packages the runtime image installs, read from the Dockerfile."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    # Only the lines that are actually instructions; the comment above them names
    # packages too, and a comment is not an install.
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(r"\bfonts-[a-z0-9-]+\b", body))


def test_the_image_can_draw_every_script_postulo_offers():
    installed = installed_font_packages()
    missing = {}
    for script in sorted(languages.scripts_offered()):
        options = COVERAGE.get(script)
        assert options, (
            f"{script} is offered but no font package is recorded for it. Add it to "
            "COVERAGE here and to the Dockerfile."
        )
        if not installed & set(options):
            missing[script] = options
    assert not missing, "the image cannot draw: " + ", ".join(
        f"{script} (install one of {opts})" for script, opts in missing.items()
    )


def test_every_non_latin_language_declares_its_script():
    """A language whose script is not recorded is one nobody checked the fonts for."""
    from postulo.core.languages import LANGUAGES, SCRIPTS

    # The ones known to be written in something other than the Latin alphabet.
    not_latin = {"ar", "am", "ti", "bg", "el"}
    offered = {code for code, _name in LANGUAGES}
    for code in not_latin & offered:
        assert code in SCRIPTS, f"{code} is not written in Latin and says nothing about it"


def test_the_scripts_offered_are_the_ones_the_languages_need():
    offered = languages.scripts_offered()
    assert {"Arabic", "Ethiopic", "Greek", "Cyrillic"} <= offered
    # Nothing from a later phase has crept in without its fonts being decided.
    assert "Han" not in offered and "Devanagari" not in offered


def test_the_dockerfile_reader_ignores_comments():
    """The comment above the install names packages; a comment does not install one."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "# tests/test_fonts.py holds this list" in text, "the comment moved; check this still"
    assert "fonts-noto-cjk" not in installed_font_packages()
