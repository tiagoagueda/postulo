"""A language code is a BCP 47 tag, written in its canonical form (#337).

`pt-BR`, not `pt-br` and not `pt_BR`. Tags compare without regard to case, so the lower-case
spelling was never wrong to a machine; it was a second spelling, and Postulo had three of
them: Django's in what it stored and sent, Word's in one export, and gettext's in the
directories. One of those is forced -- a locale directory is `pt_BR` because gettext says
so -- and the rest is settled here: one writer, one comparison, and the tests that hold
every place a code is written to them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.core import languages

# ------------------------------------------------------------------ the writer


@pytest.mark.parametrize(
    ("given", "written"),
    [
        ("pt-br", "pt-BR"),
        ("PT-br", "pt-BR"),
        ("pt_BR", "pt-BR"),
        ("pt_br", "pt-BR"),
        (" en-gb ", "en-GB"),
        ("DE", "de"),
        ("kab", "kab"),
        ("sr-cyrl", "sr-Cyrl"),
        ("SR_LATN_rs", "sr-Latn-RS"),
        ("zh-hans", "zh-Hans"),
        ("zh-hant-tw", "zh-Hant-TW"),
        ("es-419", "es-419"),
        ("ca-es-valencia", "ca-ES-valencia"),
        ("de-ch-1996", "de-CH-1996"),
        # An extended language subtag has three letters and stays lower: the rule is about
        # where a subtag stands and how long it is, and Word's writer, which upper-cased
        # anything of two or three letters, wrote this one `zh-YUE`.
        ("ZH-YUE", "zh-yue"),
        # After a singleton nothing is a region or a script any more.
        ("en-US-x-AB-cdef", "en-US-x-ab-cdef"),
        ("de-DE-u-CO-phonebk", "de-DE-u-co-phonebk"),
    ],
)
def test_a_tag_is_written_in_its_canonical_form(given, written):
    assert languages.tag(given) == written
    assert languages.tag(written) == written, "and writing it again changes nothing"


@pytest.mark.parametrize("given", ["", None, "   ", 0, 3.5, [], b"pt"])
def test_nothing_is_written_as_nothing(given):
    assert languages.tag(given) == ""
    assert not languages.well_formed(given)


@pytest.mark.parametrize(
    "given",
    [
        "english",
        "portuguese-brazil",
        "Not A Language At All",
        "<script>",
        "p",
        "pt-",
        "-pt",
        "pt--br",
        "pt-b",
        "x-private",
        "i-klingon",
        "pt-BR-",
        "en-" + "a" * 40,
        # Letters that fold to ASCII ones and are not them: the long s and the Kelvin sign.
        "pt-bſ",
        "Kab",
    ],
)
def test_what_is_not_a_tag_is_not_dressed_up_as_one(given):
    """The writer never refuses -- it is called where a value is read as well as where one
    is taken in -- so what it cannot write it hands back as it came, and the question of
    whether to believe it belongs to `well_formed`."""
    assert not languages.well_formed(given)
    assert languages.tag(given) == given.strip()


def test_a_tag_longer_than_a_column_is_not_one():
    """RFC 5646 section 4.4.1 advises room for thirty-five characters, and the columns have it."""
    assert languages.MAX_LENGTH == 35
    longest = "de-Latn-DE-1996-x-" + "a" * 8 + "-" + "b" * 8
    assert len(longest) == languages.MAX_LENGTH and languages.well_formed(longest)
    assert not languages.well_formed(longest + "-c")


@pytest.mark.parametrize(
    ("code", "primary", "script", "region"),
    [
        ("pt-BR", "pt", "Latn", "BR"),
        ("pt_br", "pt", "Latn", "BR"),
        ("sr-Cyrl", "sr", "Cyrl", ""),
        ("sr-latn-rs", "sr", "Latn", "RS"),
        ("es-419", "es", "Latn", "419"),
        ("de", "de", "Latn", ""),
        ("zh-yue-Hant-HK", "zh", "Hant", "HK"),
        ("en-x-us", "en", "Latn", ""),
        ("", "", "", ""),
    ],
)
def test_the_parts_of_a_tag(code, primary, script, region):
    assert languages.primary(code) == primary
    assert languages.script_of(code) == script
    assert languages.region_of(code) == region


# --------------------------------------------------------------- the comparison


def test_a_code_is_found_however_it_is_spelt():
    among = ["en-GB", "pt-PT", "pt-BR", "de"]
    for spelling in ("pt-BR", "pt-br", "PT_BR", " pt_br "):
        assert languages.find(spelling, among) == "pt-BR"
    assert languages.find("pt", among) == "", "finding is not matching: pt is neither of them"
    assert languages.find("", among) == ""
    assert languages.find(None, among) == ""


def test_what_is_found_is_the_spelling_of_the_list_it_was_found_in():
    """So a lookup works while a table is being respelt, and against what a database still
    holds: the answer is a key of the thing that was asked."""
    assert languages.find("PT-br", {"pt-br": 1}) == "pt-br"
    assert languages.find("pt-br", {"pt_BR": 1}) == "pt_BR"


@pytest.mark.parametrize(
    ("wanted", "available", "closest"),
    [
        # Exactly, whatever the case.
        ("pt-br", ["pt-PT", "pt-BR"], "pt-BR"),
        # RFC 4647 section 3.4: what is asked for, shortened from the end.
        ("pt-BR", ["en", "pt"], "pt"),
        ("zh-Hant-TW", ["zh", "zh-Hant"], "zh-Hant"),
        ("de-CH-1996", ["de-CH", "de"], "de-CH"),
        # A singleton left at the end by the shortening goes with it.
        ("en-US-x-private", ["en-US-x", "en"], "en"),
        # Then a sibling: a Brazilian reader given European Portuguese has read the entry.
        ("pt-BR", ["en-GB", "pt-PT"], "pt-PT"),
        ("pt", ["pt-PT", "pt-BR"], "pt-PT"),
        ("pt-AO", ["pt-BR", "pt-PT"], "pt-BR"),
        # The shorter form before a sibling, whichever is listed first.
        ("pt-BR", ["pt-PT", "pt"], "pt"),
        # Never another language.
        ("fr", ["pt-PT", "en"], ""),
        ("", ["pt-PT"], ""),
        ("pt", [], ""),
    ],
)
def test_the_closest_of_what_is_available(wanted, available, closest):
    assert languages.match(wanted, available) == closest


def test_norwegian_is_both_no_and_nb():
    """The registry files Bokmål under the macrolanguage `no`. Postulo's catalogue is `nb`
    and ESCO publishes `no`, and until the comparison knew the two together a Norwegian
    reader was given English occupation names."""
    assert languages.match("nb", ["en", "no"]) == "no"
    assert languages.match("nb-NO", ["en", "no"]) == "no"
    assert languages.match("no", ["en-GB", "nb"]) == "nb"
    assert languages.match("no-NO", ["en-GB", "nb"]) == "nb"
    assert languages.match("nb", ["no", "nb"]) == "nb", "its own name first"


def test_every_match_closest_first():
    assert languages.matches("pt-zz", ["en-GB", "pt-PT", "pt-BR"]) == ("pt-PT", "pt-BR")
    assert languages.matches("pt-br", ["pt", "pt-PT", "pt-BR"]) == ("pt-BR", "pt", "pt-PT")
    assert languages.matches("fr", ["pt-PT"]) == ()


def test_the_list_is_what_is_matched_against_when_nothing_else_is_named():
    assert languages.find(languages.SOURCE) == languages.SOURCE
    assert languages.match("de-AT") == "de"
    assert languages.match("tlh") == ""


# ------------------------------------------------------- the language of the moment


def test_the_instance_s_own_language_is_written_as_every_code_is(settings):
    assert settings.LANGUAGE_CODE == languages.SOURCE == "en-GB"
    assert dict(settings.LANGUAGES) == languages.NATIVE_NAMES


@pytest.mark.parametrize("active", ["pt-BR", "pt-br", "sr-Cyrl", "en-GB", "de"])
def test_the_language_of_the_moment_is_written_as_postulo_writes_one(active):
    """Django says the same tag in lower case, which is why nothing else asks Django."""
    with translation.override(active):
        assert translation.get_language() == active.lower()
        assert languages.current() == languages.tag(active)


def test_with_nothing_active_it_is_the_instance_s_own():
    translation.deactivate()
    assert languages.current() == "en-GB"
    translation.deactivate_all()
    assert languages.current() == "en-GB", "never empty: an override needs something to return to"


@pytest.mark.parametrize(
    ("declared", "drawn"),
    [
        ("pt-PT", "pt-PT"),
        ("PT_br", "pt-BR"),
        # A document in the Portuguese of Angola is drawn from the Portuguese there is.
        ("pt-AO", "pt-PT"),
        ("pt", "pt-PT"),
        ("en", "en-GB"),
        # What a record held before the list said which script the Serbian catalogue is in,
        # and what a file from elsewhere may still say.
        ("sr", "sr-Cyrl"),
        ("sr-RS", "sr-Cyrl"),
        # The one Serbian catalogue there is, until there is a Latin one.
        ("sr-Latn", "sr-Cyrl"),
        ("no", "nb"),
        ("nb-NO", "nb"),
        # A language Postulo does not speak is still handed on: Django may know its months.
        ("ja", "ja"),
        ("ja_jp", "ja-JP"),
        ("", ""),
        (None, ""),
        ("not a language", ""),
    ],
)
def test_what_a_declared_language_is_drawn_in(declared, drawn):
    assert languages.catalogue(declared) == drawn


def test_activating_goes_through_the_list():
    with translation.override("en-GB"):
        languages.activate("pt-AO")
        assert languages.current() == "pt-PT"
        languages.activate("")
        assert languages.current() == "pt-PT", "nothing leaves it as it is"
        languages.activate("<script>")
        assert languages.current() == "pt-PT"
    with languages.override("sr"):
        assert languages.current() == "sr-Cyrl"
    with translation.override("fr-FR"), languages.override(""):
        assert languages.current() == "fr-FR"


def test_why_an_old_serbian_code_cannot_be_activated_as_it_is():
    """gettext looks for `sr_Cyrl` and then for `sr`, so Django's own Serbian words are found
    under the new name. It does not look the other way: `sr` never finds `sr_Cyrl`, and a
    page activated as `sr` was Django's Serbian months over Postulo's English."""
    import gettext

    assert gettext._expand_lang("sr_Cyrl")[:2] == ["sr_Cyrl", "sr"]
    assert "sr_Cyrl" not in gettext._expand_lang("sr")


def test_serbian_still_reads_django_s_own_words_and_formats():
    from django.conf.locale.sr import formats as serbian
    from django.utils.dates import MONTHS
    from django.utils.formats import get_format

    with languages.override("sr-Cyrl"):
        assert str(MONTHS[1]) == "јануар"
        assert get_format("DATE_FORMAT") == serbian.DATE_FORMAT


# --------------------------------------------------------------- what a page says


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["pt-BR", "sr-Cyrl", "fr-FR"])
def test_a_page_and_its_header_say_the_language_as_postulo_writes_it(client, user, code):
    user.profile.language = code
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert response.headers["Content-Language"] == code
    assert f'<html lang="{code}"' in response.content.decode()
    assert response.wsgi_request.LANGUAGE_CODE == code


@pytest.mark.django_db
@pytest.mark.parametrize("asked", ["sr", "sr-RS", "sr-Latn", "sr-Cyrl", "sr-cyrl-rs"])
def test_a_browser_asking_for_serbian_is_given_the_catalogue_there_is(client, asked):
    response = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE=asked)

    assert response.headers["Content-Language"] == "sr-Cyrl"
    assert '<html lang="sr-Cyrl"' in response.content.decode()


@pytest.mark.django_db
def test_an_answer_made_before_any_view_says_it_the_same_way(client):
    """The header is written by the layer that chose the language, so a page that was never
    found says it as a page that was."""
    response = client.get("/nothing-is-here/", HTTP_ACCEPT_LANGUAGE="pt-br")

    assert response.status_code == 404
    assert response.headers["Content-Language"] == "pt-BR"


@pytest.mark.django_db
def test_a_profile_still_holding_the_old_serbian_code_reads_serbian(client, user):
    from postulo.accounts.models import Profile
    from postulo.core.models import SiteSettings

    Profile.objects.filter(user=user).update(language="sr")
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"offered_languages": ["en-GB", "sr-Cyrl"]}
    )
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.headers["Content-Language"] == "sr-Cyrl"


# ------------------------------------------------------- what the comparison reaches


@pytest.fixture
def classified(tmp_path, monkeypatch):
    """A classification of one unit group, published as ESCO publishes: `no`, not `nb`.

    Written here rather than read from the download, which a checkout does not have: the
    question is how a reader's language is matched to the file's, not what the file says.
    """
    import json

    from postulo.jobs import esco

    names = {
        "en": "Systems analysts",
        "no": "Systemanalytikere",
        "pt": "Analistas de sistemas",
    }
    document = {
        **esco.ABSENT,
        "revision": "ESCO for a test",
        "languages": sorted(names),
        "unit_groups": {"2511": {"major": "2", "names": names}},
    }
    (tmp_path / "esco-0.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(esco, "DATA_DIR", tmp_path)
    esco.forget()
    yield names
    esco.forget()


def test_a_norwegian_reader_is_given_the_norwegian_names(classified):
    """ESCO publishes its Norwegian under `no`; Postulo's catalogue is `nb`. The two were
    compared as strings, and a Norwegian reader was given the English names."""
    from postulo.jobs import esco

    assert esco.name_for("2511", "nb") == classified["no"]
    assert esco.name_for("2511", "nb-NO") == classified["no"]
    with languages.override("nb"):
        assert esco.name_for("2511") == classified["no"]
    with languages.override("pt-BR"):
        assert esco.name_for("2511") == classified["pt"], "as it always was"
    assert esco.name_for("2511", "tlh") == classified["en"], "English where ESCO has none"


def test_a_skill_is_read_in_norwegian_too(monkeypatch):
    from postulo.jobs import esco

    monkeypatch.setattr(esco, "skills_about", lambda: {"languages": ["en", "no", "pt"]})

    assert esco._skill_language("nb") == "no"
    assert esco._skill_language("pt-BR") == "pt"
    assert esco._skill_language("PT_br") == "pt"
    assert esco._skill_language("tlh") == "", "and no English standing in on a CV"


def test_the_catalogue_tool_refuses_the_other_spelling():
    """Django finds `fr_FR` for `fr-fr` as readily as for `fr-FR`, which is how a gate keyed
    by codes would have shrunk without failing: the catalogue is there, and the code is in
    no list a test filters by."""
    from pathlib import Path

    from postulo.core import messages_tool

    messages_tool.use(Path(__file__).resolve().parents[1])
    assert messages_tool.po_path("fr-FR").parts[-3] == "fr_FR"
    assert messages_tool.po_path("sr-Cyrl").parts[-3] == "sr_Cyrl"
    with pytest.raises(ValueError, match="fr-FR"):
        messages_tool.po_path("fr-fr")
    with pytest.raises(ValueError, match="pt-BR"):
        messages_tool.po_path("pt_BR")


def test_every_catalogue_is_in_the_directory_its_code_names():
    """Case for case, which Windows and macOS would not notice and a container does."""
    from pathlib import Path

    from postulo.core import messages_tool

    messages_tool.use(Path(__file__).resolve().parents[1])
    expected = {languages.locale_dir_name(code) for code in messages_tool.translated_languages()}
    for subject in messages_tool.catalogue_sets():
        found = {path.name for path in subject.locale.iterdir() if path.is_dir()}
        assert found == expected, subject.name


# ------------------------------------------------------------------- the guards
#
# What keeps the other spelling from coming back. Each reads the source rather than running
# it, so a line nobody's test happens to reach is held as well.

SRC = Path(__file__).resolve().parents[1] / "src" / "postulo"
REPO = SRC.parents[1]
WIKI = REPO.parent / "postulo.wiki"

#: Every code on the list that has a region or a script, in the two spellings it is not
#: written in: Django's lower case, and gettext's underscore, which is a directory's name.
OTHERWISE = {
    spelling: code
    for code in languages.NATIVE_NAMES
    if "-" in code
    for spelling in (code.lower(), code.replace("-", "_"), code.lower().replace("-", "_"))
}

#: Where Django is told a language directly instead of through `postulo.core.languages`, and
#: why that is right there. Not a list of exemptions: a new entry is a decision, and one that
#: is no longer true fails.
DIRECT = {
    "plugins/builtin/patterns.py": (
        "the words and month names a page is read with, in each of Postulo's own languages "
        "that the page's tag names: only ever a key of the list, from `spoken`, and a plugin "
        "imports nothing but the surface"
    ),
    "plugins/europass/candidate.py": (
        "the name of a language in the language of the CV, which is Django's own catalogue "
        "of language names and not Postulo's"
    ),
}


def sources() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def test_no_string_in_the_source_is_a_code_in_another_spelling():
    """A string that is exactly one of the list's codes, written some other way. Prose is
    not read: a docstring may say what the other spelling was."""
    import ast

    found = []
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend(
            f"{path.relative_to(SRC).as_posix()}:{node.lineno} {node.value!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in OTHERWISE
        )

    assert not found, f"written as a BCP 47 tag in its canonical form (#337): {found}"


def test_no_page_and_no_script_writes_a_code_in_another_spelling():
    other = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, OTHERWISE)) + r")(?![\w-])")
    pages = [*(SRC / "templates").rglob("*.html"), SRC / "static" / "js" / "app.js"]
    assert len(pages) > 100, "the reader found the templates"

    found = [
        f"{path.relative_to(SRC).as_posix()}:{number} {line.strip()[:80]}"
        for path in pages
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if other.search(line)
    ]

    assert not found, found


def test_django_is_told_and_asked_a_language_in_one_place():
    """Django answers `get_language()` in lower case once a translation is active, and is
    told a language that may have no catalogue of its own. `postulo.core.languages` is where
    both are dealt with, so nothing else does either."""
    direct = re.compile(r"\bget_language\(|\btranslation\.(?:activate|override)\(")
    found = {}
    for path in sources():
        name = path.relative_to(SRC).as_posix()
        if name != "core/languages.py" and direct.search(path.read_text(encoding="utf-8")):
            found[name] = True

    assert set(found) == set(DIRECT), (
        "ask `languages.current()`, and tell it through `languages.activate` or "
        f"`languages.override`: {sorted(set(found) ^ set(DIRECT))}"
    )


def test_nothing_lower_cases_a_code_for_itself():
    """The idiom every one of the eleven had."""
    found = [
        path.relative_to(SRC).as_posix()
        for path in sources()
        if '.lower().replace("_", "-")' in path.read_text(encoding="utf-8")
    ]

    assert not found, found


def test_the_other_spelling_is_only_written_beside_the_right_one():
    """In the guide and the wiki a code is written as Postulo writes it. The one exception is
    a sentence saying what not to write, and it says what to write on the same line."""
    pages = [*(REPO / "docs").glob("*.md"), *(REPO / name for name in ("README.md", "CLAUDE.md"))]
    pages += [REPO / "CONTRIBUTING.md", *(WIKI.glob("*.md") if WIKI.is_dir() else [])]
    assert len(pages) > 5

    found = []
    for path in pages:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for spelling, code in OTHERWISE.items():
                if "_" in spelling:
                    continue  # a locale directory is `pt_BR`, and the pages say so
                if (
                    re.search(rf"(?<![\w-]){re.escape(spelling)}(?![\w-])", line)
                    and code not in line
                ):
                    found.append(f"{path.name}:{number} {spelling}")

    assert not found, found


def test_the_sentence_about_a_code_holds_none():
    """It shows two codes, and they are handed to it: a translation of it cannot keep an
    old spelling, because it holds no spelling at all."""
    from postulo.core import language_field

    said = language_field.refusal("portuguese").messages[0]

    assert said == "“portuguese” is not a language code, like en-GB or pt-PT."
    with languages.override("fr-FR"):
        french = language_field.refusal("portuguese").messages[0]
    assert "en-GB" in french and "pt-PT" in french and "portuguese" in french
