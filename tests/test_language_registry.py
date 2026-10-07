"""Postulo's own list of languages is held to the IANA registry (#337).

A BCP 47 tag is made of subtags from a registry, and being shaped like one is not the same
as being one: `xx-YY` is well formed and names nothing. Two rules follow, and this file is
the second.

- **A free field is held to the shape only** (`languages.well_formed`). A document may be in
  a language Postulo has never heard of, and a record imported from elsewhere may say
  `pt-AO`; refusing either would be Postulo deciding what languages exist.
- **The list Postulo offers is held to the registry.** It is kept by hand, and a hand writes
  `sr-Cyrl` as readily as `sr-Cyr`. Every subtag has to be registered, in the place it
  stands in; none may be deprecated; and none may state a script the registry says goes
  without saying -- `bg-Cyrl` is Bulgarian with a redundant subtag, and the canonical form
  leaves it out.

The copy in `tests/data` is the registry without its descriptions, dates and comments, which
is what keeps it under the size a commit is allowed to add. It is never fetched by a test.
To take a newer one (`docs/TRANSLATING.md` says the same):

    curl -s https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry \\
      | grep -vE '^(Description|Added|Comments):|^  ' > tests/data/language-subtag-registry
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from postulo.accounts import addressing
from postulo.core import languages, phones

REGISTRY = Path(__file__).resolve().parent / "data" / "language-subtag-registry"

#: Every table keyed by a whole language tag, by where it lives.
TABLES = {
    "languages.NATIVE_NAMES": languages.NATIVE_NAMES,
    "languages.FLAG_COUNTRIES": languages.FLAG_COUNTRIES,
    "languages.PLURAL_FORMS": languages.PLURAL_FORMS,
    "phones.FROM_LANGUAGE": phones.FROM_LANGUAGE,
    "addressing.FORMS_OF_ADDRESS": addressing.FORMS_OF_ADDRESS,
    "addressing.PRONOUNS": addressing.PRONOUNS,
}
TAGS = sorted({code for table in TABLES.values() for code in table} | {languages.SOURCE})

#: Every table keyed by a language alone, without script or region.
BY_LANGUAGE = {
    "languages.RTL": languages.RTL,
    "languages.SCRIPTS": languages.SCRIPTS,
    "languages.MACROLANGUAGE": languages.MACROLANGUAGE,
}


@cache
def registry() -> dict[tuple[str, str], dict[str, str]]:
    """``{(type, subtag): its fields}``, from the record-jar the registry is published as."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for record in REGISTRY.read_text(encoding="utf-8").split("%%")[1:]:
        fields = dict(line.split(": ", 1) for line in record.strip().splitlines())
        found[fields["Type"], fields.get("Subtag") or fields["Tag"]] = fields
    return found


def retired(fields: dict[str, str]) -> bool:
    return "Deprecated" in fields or "Preferred-Value" in fields


def test_the_copy_of_the_registry_is_whole():
    """A check on the check: a file cut short would pass every language it no longer holds."""
    assert REGISTRY.read_text(encoding="utf-8").startswith("File-Date: 20")
    kinds = [kind for kind, _subtag in registry()]
    assert kinds.count("language") > 8000
    assert kinds.count("script") > 200
    assert kinds.count("region") > 300
    assert ("language", "zu") in registry(), "to the end of the alphabet"


@pytest.mark.parametrize("code", TAGS)
def test_every_code_postulo_writes_is_a_registered_tag_in_its_canonical_form(code):
    assert languages.tag(code) == code, "written as the writer writes it"
    assert languages.well_formed(code)
    known = registry()

    language = known.get(("language", languages.primary(code)))
    assert language is not None, f"{languages.primary(code)} is not a registered language"
    assert not retired(language), f"{languages.primary(code)} is deprecated in the registry"

    parts = code.split("-")[1:]
    script = next((part for part in parts if len(part) == 4 and part.isalpha()), "")
    region = languages.region_of(code)
    assert len(parts) == bool(script) + bool(region), "a language, a script and a region only"
    if script:
        entry = known.get(("script", script))
        assert entry is not None and not retired(entry), f"{script} is not a script in use"
        assert language.get("Suppress-Script") != script, (
            f"{script} goes without saying for {languages.primary(code)}: the registry "
            "suppresses it, and the canonical form leaves it out"
        )
    if region:
        entry = known.get(("region", region))
        assert entry is not None and not retired(entry), f"{region} is not a region in use"


def test_no_language_is_on_the_list_twice():
    """`pt-BR` and `pt-br` are one tag, and a table holding both would answer either."""
    for name, table in TABLES.items():
        folded = [code.lower() for code in table]
        assert len(folded) == len(set(folded)), name


@pytest.mark.parametrize(
    ("name", "language"),
    [(name, language) for name, table in BY_LANGUAGE.items() for language in sorted(table)],
)
def test_every_language_named_alone_is_registered(name, language):
    assert language == language.lower() and "-" not in language, name
    entry = registry().get(("language", language))
    assert entry is not None, f"{name} names {language}, which the registry does not hold"
    assert not retired(entry), f"{name} names {language}, which the registry has retired"


def test_the_other_name_of_a_language_is_the_registry_s():
    """`nb` and `no` are compared as one because the registry says Bokmål belongs to
    Norwegian, not because somebody here thought so."""
    for member, macro in languages.MACROLANGUAGE.items():
        assert registry()["language", member].get("Macrolanguage") == macro
        assert registry()["language", macro].get("Scope") == "macrolanguage"


def test_serbian_says_its_script_because_nothing_else_does():
    """The reason `sr-Cyrl` is on the list where `bg-Cyrl` could not be: Serbian is written
    in two scripts, so the registry suppresses neither."""
    assert "sr-Cyrl" in languages.NATIVE_NAMES and "sr" not in languages.NATIVE_NAMES
    assert "Suppress-Script" not in registry()["language", "sr"]
    assert registry()["language", "bg"]["Suppress-Script"] == "Cyrl"


# ------------------------------------------------------------------- the scripts

#: Offered, written in something other than Latin, and saying nothing about it: no font check
#: covers them. Known, and #455's to put right, which needs a probe and a font package for
#: each. Held here so that the gap is a list somebody reads and not a thing nobody noticed,
#: and so that it has to come off this list the day it is closed.
UNDECLARED: set[str] = set()


def test_a_script_is_named_as_the_registry_names_it():
    """`Cyrl`, not *Cyrillic*: the code a tag states a script with, so that a language's
    usual script and one a tag states are said in one vocabulary."""
    from postulo.documents import fonts

    named = {*languages.SCRIPTS.values(), *languages.RTL_SCRIPTS, *fonts.PROBES}
    for script in sorted(named | set(languages.SCRIPT_NAMES)):
        entry = registry().get(("script", script))
        assert entry is not None and not retired(entry), f"{script} is not a script in use"
    assert set(fonts.PROBES) <= set(languages.SCRIPT_NAMES), "a script that is probed has a name"
    assert set(languages.SCRIPTS.values()) <= set(languages.SCRIPT_NAMES)


def test_the_script_a_language_is_usually_written_in_is_the_registry_s():
    """What `SCRIPTS` holds is what the registry says goes without saying for the language,
    which is also why none of them is on the list with its script written out."""
    for language, script in languages.SCRIPTS.items():
        assert registry()["language", language].get("Suppress-Script") == script, language


def test_every_language_written_in_another_script_says_so():
    """A language whose script is not recorded is one nobody checked the fonts for, and a
    fixed list of the ones somebody remembered cannot notice the next."""
    silent = set()
    for code in languages.NATIVE_NAMES:
        usual = registry()["language", languages.primary(code)].get("Suppress-Script", "")
        if usual and usual != "Latn" and languages.script_of(code) != usual:
            silent.add(code)

    assert silent == UNDECLARED, (
        "offered in another script and not in languages.SCRIPTS, or declared at last and "
        f"still listed here: {sorted(silent ^ UNDECLARED)}"
    )


def test_the_scripts_offered_are_what_the_tags_and_the_table_say():
    assert languages.script_of("sr-Cyrl") == "Cyrl", "stated"
    assert languages.script_of("bg") == "Cyrl", "usual"
    assert languages.script_of("pt-BR") == "Latn"
    assert languages.scripts_offered() == {
        "Arab",
        "Armn",
        "Beng",
        "Cyrl",
        "Deva",
        "Ethi",
        "Geor",
        "Grek",
        "Hans",
        "Hant",
    }
