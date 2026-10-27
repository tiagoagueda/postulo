"""What a language is called, in any language there is a name for it in (#689).

English is *Inglês* in European Portuguese and *Anglais* in French; French is *Français* and
*Francês*. The names are CLDR's, through Babel, and the capital is CLDR's rule, which Babel
does not carry and `language_names.CAPITALISED` does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from babel import Locale

from postulo.core import language_names, languages

GAPS = Path(__file__).parent / "data" / "language_name_gaps.json"

#: What CLDR 47 (`cldr-json` 47.0.0, `cldr-misc-full/main/<locale>/contextTransforms.json`)
#: says of the capital on a language's name, for every tag Postulo has a catalogue for that
#: says anything at all: `(standing alone, in a menu)`, `None` where the locale says nothing.
#: European Portuguese sets the menu to `no-change` and inherits the other from Portuguese.
CLDR_RULES: dict[str, tuple[str | None, str | None]] = {
    "cs": ("titlecase-firstword", "titlecase-firstword"),
    "da": (None, "titlecase-firstword"),
    "es": ("titlecase-firstword", "titlecase-firstword"),
    "es-MX": ("titlecase-firstword", "titlecase-firstword"),
    "fi": (None, "titlecase-firstword"),
    "fr-FR": (None, "titlecase-firstword"),
    "fr-CA": (None, "titlecase-firstword"),
    "hr": ("titlecase-firstword", "titlecase-firstword"),
    "it": ("titlecase-firstword", "titlecase-firstword"),
    "pt-PT": ("titlecase-firstword", "no-change"),
    "pt-BR": ("titlecase-firstword", "titlecase-firstword"),
    "sk": ("titlecase-firstword", "titlecase-firstword"),
    "sv": ("titlecase-firstword", "titlecase-firstword"),
    "uk": ("titlecase-firstword", "titlecase-firstword"),
}

TAGS = list(languages.NATIVE_NAMES)


# ------------------------------------------------------------------------- the two examples


@pytest.mark.parametrize(
    "code,shown,expected",
    [
        ("en", "pt-PT", "Inglês"),
        ("fr", "pt-PT", "Francês"),
        ("pt", "pt-PT", "Português"),
        ("en", "fr-FR", "Anglais"),
        ("fr", "fr-FR", "Français"),
        ("pt", "fr-FR", "Portugais"),
        ("en", "en-GB", "English"),
        ("pt-BR", "en", "Brazilian Portuguese"),
        ("pt-BR", "pt-PT", "Português do Brasil"),
    ],
)
def test_a_language_is_named_in_the_language_asked_for(code, shown, expected):
    assert language_names.name(code, shown) == expected


def test_a_language_with_no_language_asked_for_is_named_as_it_names_itself():
    assert language_names.name("de") == "Deutsch"


# --------------------------------------------------------------------------- the gaps


def test_the_gaps_are_the_recorded_ones():
    """870 of the 7,225 names of Postulo's own tags in one another are not in CLDR. One
    language is named nowhere: CLDR has no name for Montenegrin under `cnr`, so every
    language falls back to *crnogorski*. The other 785 are all in 18 African display
    languages. A Babel that changes either number has changed the data beneath the
    fallback, and somebody has to look."""
    recorded = json.loads(GAPS.read_text(encoding="utf-8"))
    assert recorded["cldr"] == language_names.CLDR_VERSION

    found: dict[str, list[str]] = {}
    for shown in TAGS:
        locale = Locale.parse(shown, sep="-")
        for named in TAGS:
            key = named.replace("-", "_")
            if not (locale.languages.get(key) or locale.languages.get(named.split("-")[0])):
                found.setdefault(shown, []).append(named)

    assert found == recorded["gaps"]
    assert sum(len(names) for names in found.values()) == 870
    nowhere = [named for named in TAGS if all(named in found.get(shown, []) for shown in TAGS)]
    assert nowhere == ["cnr"]
    rest = {shown: [n for n in names if n not in nowhere] for shown, names in found.items()}
    assert sum(len(names) for names in rest.values()) == 785
    assert len([shown for shown, names in rest.items() if names]) == 18


def test_a_gap_is_never_blank():
    for shown in TAGS:
        for named in TAGS:
            assert language_names.name(named, shown).strip(), (named, shown)


def test_a_gap_falls_back_to_the_languages_own_name():
    """Tsonga has no name for Southern Ndebele, which names itself."""
    assert "nr" in json.loads(GAPS.read_text(encoding="utf-8"))["gaps"]["ts"]
    assert language_names.name("nr", "ts") == "isiNdebele"


def test_a_gap_in_the_language_and_in_its_own_name_is_english_and_then_the_code(monkeypatch):
    real = language_names._cldr

    def only_english(code, locale):
        return real(code, locale) if locale is not None and locale.language == "en" else ""

    monkeypatch.setattr(language_names, "_cldr", only_english)
    assert language_names.name("fr", "ts") == "French"
    assert language_names.name("bfi", "pt") == "bfi"


def test_a_well_formed_code_nobody_names_is_kept_as_it_is():
    assert language_names.name("bfi", "pt-PT") == "bfi"
    assert language_names.name("qq", "fr-FR") == "qq"
    assert language_names.name("", "fr-FR") == ""
    assert not language_names.known("bfi")
    assert language_names.known("fr")


def test_a_regional_tag_reads_through_its_parent():
    """`pt-AO` has no names of its own to speak of: it reads as European Portuguese."""
    assert language_names.name("en", "pt-AO") == "Inglês"
    assert language_names.name("fr", "pt-AO") == "Francês"
    assert language_names.name("en", "ca-ES-valencia") == "anglès"


def test_a_tag_for_a_language_the_catalogues_do_not_have_is_still_named():
    assert language_names.name("fr", "ja") == "フランス語"


# ----------------------------------------------------------------------------- the capital


def test_the_capitalisation_table_agrees_with_cldr_for_every_tag_postulo_has():
    """A label is capitalised where either context asks, which is `CAPITALISED`."""
    for tag in TAGS:
        alone, menu = CLDR_RULES.get(tag, (None, None))
        asks = "titlecase-firstword" in (alone, menu)
        assert (languages.primary(tag) in language_names.CAPITALISED) is asks, tag


def test_every_language_in_the_table_is_one_postulo_has_a_catalogue_for():
    assert language_names.CAPITALISED <= {languages.primary(tag) for tag in TAGS}


def test_the_rules_were_read_from_the_cldr_babel_carries():
    """A Babel upgrade that brings another CLDR has to be checked against the rules above."""
    assert language_names.cldr_version() == language_names.CLDR_VERSION


def test_a_name_standing_alone_has_its_capital_where_the_language_asks():
    assert language_names.name("en", "pt-PT") == "Inglês"
    assert language_names.name("en", "fr-FR") == "Anglais"
    assert language_names.name("en", "pt-BR") == "Inglês"
    assert language_names.name("en", "es") == "Inglés"
    assert language_names.name("en", "uk") == "Англійська"


def test_catalan_and_dutch_are_written_as_cldr_writes_them():
    assert language_names.name("fr", "ca") == "francès"
    assert language_names.name("fr", "nl") == "Frans"
    assert language_names.name("en", "da") == "Engelsk"


def test_a_capital_is_only_the_first_letter_raised():
    """`capitalize()` makes Dutch *IJslands* into *Ijslands*, and Turkish *İngiliz
    İngilizcesi* into one with a combining dot."""
    assert language_names.name("is", "nl") == "IJslands"
    assert language_names.name("en-GB", "tr") == "İngiliz İngilizcesi"
    assert "̇" not in language_names.name("en-GB", "tr")


def test_georgian_is_not_raised_into_mtavruli():
    georgian = language_names.name("en", "ka")
    assert georgian == "ინგლისური"
    assert georgian[0] == "ი", "Mtavruli, the all-capitals form, is not a sentence capital"


# ---------------------------------------------------------------------------- the list


def test_the_list_is_cldrs_languages_and_the_four_variants_without_what_is_not_one():
    offered = language_names.offered()

    assert {"en", "fr", "ja", "hi", "mwl", "yue", "pt-BR", "pt-PT", "zh-Hans", "zh-Hant"} <= set(
        offered
    )
    assert not {"und", "mul", "zxx"} & set(offered)
    assert len(offered) == len(set(offered)) > 600
    assert all(languages.well_formed(code) for code in offered)
    assert all(languages.tag(code) == code for code in offered)


def test_the_list_is_in_the_order_the_language_it_is_in_reads():
    listed = language_names.listing("pt-PT")
    names = [name for _code, name in listed]

    assert ("en", "Inglês") in listed
    assert names.index("Alemão") < names.index("Inglês") < names.index("Português")
    assert {code for code, _name in listed} == set(language_names.offered())


# ---------------------------------------------------------------------------- a typed name


@pytest.mark.parametrize(
    "typed,code",
    [
        ("French", "fr"),
        ("Francês", "fr"),
        ("français", "fr"),
        ("FRANCAIS", "fr"),
        ("  english ", "en"),
        ("Inglês", "en"),
        ("Alemão", "de"),
        ("Deutsch", "de"),
        ("fr", "fr"),
        ("fra", "fr"),
        ("FRE", "fr"),
        ("pt-BR", "pt-BR"),
        ("Brazilian Portuguese", "pt-BR"),
    ],
)
def test_a_name_that_is_exactly_one_language_is_that_language(typed, code):
    assert language_names.match(typed) == code


@pytest.mark.parametrize(
    "typed",
    [
        "Norwegian",
        "Chinese",
        "Portuguese",
        "Português",
        "xyzzy",
        "",
        "   ",
        "Elvish",
        "Spanish-ish",
    ],
)
def test_an_ambiguous_or_unknown_name_is_nothing(typed):
    """What is put on a CV is never a guess: *Norwegian* is Bokmål or Nynorsk, *Chinese* is
    two scripts, *Portuguese* is two countries'."""
    assert language_names.match(typed) == ""


# ------------------------------------------- a language among its own variants (#1053)


@pytest.mark.parametrize(
    ("code", "heading"),
    [
        ("pt-BR", "Português"),
        ("es-MX", "Español"),
        ("fr-CA", "Français"),
        ("en-GB", "English"),
        ("zh-Hant", "中文"),
    ],
)
def test_the_heading_over_the_variants_is_the_language_in_itself(code, heading):
    """Capitalised as CLDR asks of a menu: *Português*, where a sentence says *português*."""
    assert language_names.family_name(code) == heading


@pytest.mark.parametrize(
    ("code", "variant"),
    [
        ("pt-PT", "Portugal"),
        ("pt-BR", "Brasil"),
        ("es", "España"),
        ("es-MX", "México"),
        ("fr-FR", "France"),
        ("fr-CA", "Canada"),
        ("en-GB", "United Kingdom"),
        ("en-CA", "Canada"),
    ],
)
def test_a_variant_by_country_is_named_by_the_country_in_its_own_language(code, variant):
    """``es`` names no country, and is read with the one CLDR says is likely: Spain."""
    assert language_names.variant_name(code) == variant


def test_chinese_is_named_by_script_and_not_by_region():
    assert language_names.variant_name("zh-Hans") == "简体中文"
    assert language_names.variant_name("zh-Hant") == "繁體中文"


def test_a_language_is_found_by_its_english_name_as_well_as_its_own():
    """*German* finds *Deutsch* (#1053)."""
    words = language_names.search_words("de")

    assert words.startswith("Deutsch")
    assert "German" in words
    assert words.endswith("de")


def test_and_by_its_name_in_the_language_the_page_is_read_in():
    assert "allemand" in language_names.fold(language_names.search_words("de", "fr-FR"))
    assert "Brazilian Portuguese" in language_names.search_words("pt-BR")
    assert "Simplified Chinese" in language_names.search_words("zh-Hans")


def test_every_language_postulo_has_can_be_found_by_more_than_its_tag():
    for code in languages.NATIVE_NAMES:
        words = language_names.search_words(code)
        assert languages.NATIVE_NAMES[code] in words and code in words, code
