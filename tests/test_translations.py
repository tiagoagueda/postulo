"""Every language Postulo offers has a catalogue that is complete, consistent and loadable.

The catalogues are text in the repository; what Django reads is the compiled .mo. The
test suite compiles into a temporary directory of its own, so it needs neither GNU gettext
nor a build step to have run first, and asks Django to render in each language.
"""

import gettext
import importlib.util
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import translation

from postulo.core import languages

REPO = Path(__file__).resolve().parents[1]
LOCALE = REPO / "src" / "postulo" / "locale"
CODES = [code for code, _name in languages.LANGUAGES if code != languages.SOURCE]

#: The 24 official languages of the European Union: phase one (#43), finished in 0.2.0 and
#: not allowed to regress. Every later phase adds languages whose catalogues arrive over
#: time, so "complete" is a promise about these and an aspiration about the rest.
EUROPEAN_UNION = (
    "bg",
    "cs",
    "da",
    "de",
    "el",
    "en-gb",
    "es",
    "et",
    "fi",
    "fr-fr",
    "ga",
    "hr",
    "hu",
    "it",
    "lt",
    "lv",
    "mt",
    "nl",
    "pl",
    "pt-pt",
    "ro",
    "sk",
    "sl",
    "sv",
)


#: Languages with at least one translated string, which are the ones a person is offered.
def started(catalogues) -> list[str]:
    return [code for code in CODES if any(m.translated for m in catalogues[code].messages.values())]


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("messages_tool", REPO / "scripts" / "messages.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["messages_tool"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def catalogues(tool):
    return {code: tool.parse(tool.po_path(code).read_text(encoding="utf-8")) for code in CODES}


@pytest.fixture(scope="module")
def compiled(tool, catalogues, tmp_path_factory):
    """Compiled .mo files in a temporary locale tree, registered with Django."""
    root = tmp_path_factory.mktemp("locale")
    for code, catalogue in catalogues.items():
        path = root / languages.locale_dir_name(code) / "LC_MESSAGES" / "django.mo"
        path.parent.mkdir(parents=True)
        path.write_bytes(tool.compile_catalogue(catalogue))
    from django.utils.translation import trans_real

    previous = settings.LOCALE_PATHS
    settings.LOCALE_PATHS = [root]
    trans_real._translations = {}
    trans_real._default = None
    yield root
    settings.LOCALE_PATHS = previous
    trans_real._translations = {}
    trans_real._default = None


def test_the_settings_offer_every_eu_language():
    codes = dict(settings.LANGUAGES)
    for code in EUROPEAN_UNION:
        assert code in codes
    assert all(name == languages.NATIVE_NAMES[code] for code, name in settings.LANGUAGES), (
        "each language under its own name"
    )


def test_the_settings_offer_the_african_languages_too():
    """Phase two (#70): the rule is official or national status in an African state."""
    codes = dict(settings.LANGUAGES)
    for code in ("ar", "sw", "am", "ha", "yo", "zu", "af", "so", "rw", "wo", "mg", "ti"):
        assert code in codes, f"{code} is missing from the languages Postulo offers"


@pytest.mark.parametrize("code", [c for c in CODES if c in EUROPEAN_UNION])
def test_every_european_union_language_stays_complete(code, catalogues):
    """Finished in 0.2.0. A later phase must not quietly leave a gap in one of these."""
    catalogue = catalogues[code]
    missing = [m.msgid for m in catalogue.messages.values() if not m.translated]
    assert catalogue.messages, f"{code}: empty catalogue"
    assert not missing, f"{code}: {len(missing)} untranslated, e.g. {missing[:3]}"


@pytest.mark.parametrize("code", CODES)
def test_every_language_has_a_catalogue_with_the_right_plural_rule(code, catalogues):
    """True of every language the day it is added, translated or not.

    A catalogue with the wrong number of plural slots cannot be filled correctly later, so
    this is the thing to get right before anybody starts translating rather than after.
    """
    catalogue = catalogues[code]
    assert catalogue.messages, f"{code}: empty catalogue"
    assert catalogue.header["Plural-Forms"] == languages.PLURAL_FORMS[code]


@pytest.mark.parametrize("code", CODES)
def test_every_translation_keeps_its_placeholders_and_plural_forms(code, catalogues, tool):
    problems = tool.problems_in(catalogues[code], code)
    assert not problems, "\n".join(problems[:10])


def test_the_catalogues_are_current(tool):
    """What the source says, the catalogues carry: no string added without a slot."""
    extracted = tool.extract_all()
    for code in CODES:
        catalogue = tool.parse(tool.po_path(code).read_text(encoding="utf-8"))
        missing = set(extracted) - set(catalogue.messages)
        extra = set(catalogue.messages) - set(extracted)
        assert not missing, (
            f"{code}: run scripts/messages.py extract; missing {sorted(missing)[:3]}"
        )
        assert not extra, f"{code}: run scripts/messages.py extract; stale {sorted(extra)[:3]}"


@pytest.mark.parametrize("code", CODES)
def test_the_compiled_catalogue_loads_and_pluralises(code, compiled):
    """The plural rule, which has to be right before anybody translates against it.

    Arabic has six forms and Wolof has one; a catalogue whose header says otherwise gets
    filled wrongly and nobody finds out until a count is printed in the wrong shape.
    """
    path = compiled / languages.locale_dir_name(code) / "LC_MESSAGES" / "django.mo"
    with path.open("rb") as handle:
        catalogue = gettext.GNUTranslations(handle)
    forms = {catalogue.plural(n) for n in range(0, 200)}
    assert len(forms) == languages.nplurals(code), f"{code}: plural rule yields {forms}"
    one = catalogue.ngettext("%(counter)s company", "%(counter)s companies", 1)
    many = catalogue.ngettext("%(counter)s company", "%(counter)s companies", 2)
    assert "%(counter)s" in one and "%(counter)s" in many


@pytest.mark.parametrize("code", CODES)
def test_a_started_catalogue_actually_translates(code, compiled, catalogues):
    if code not in started(catalogues):
        pytest.skip(f"{code}: nobody has begun this catalogue yet")
    path = compiled / languages.locale_dir_name(code) / "LC_MESSAGES" / "django.mo"
    with path.open("rb") as handle:
        catalogue = gettext.GNUTranslations(handle)
    one = catalogue.ngettext("%(counter)s company", "%(counter)s companies", 1)
    assert one != "%(counter)s company", f"{code}: compiled but translating nothing"


@pytest.mark.parametrize("code", CODES)
def test_the_interface_renders_in_every_language(client, user, code, compiled, catalogues):
    """Each language, one full page: nothing raises, and the page says which it is in.

    Every language, including one whose catalogue is still empty — an untranslated
    language must render an English page rather than fail, because that is what an
    operator setting it as the instance default will get, and half a phase's languages
    are in that state at any time.
    """
    user.profile.language = code
    user.profile.save()
    client.force_login(user)
    response = client.get(reverse("applications:list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert f'lang="{code}"' in body
    assert f'dir="{languages.direction(code)}"' in body
    if code not in started(catalogues):
        return
    with translation.override(code):
        heading = translation.gettext("Applications")
    assert heading != "Applications" or code in ("nl", "ga", "mt", "sv", "da"), (
        f"{code}: the applications heading is still English"
    )
    assert heading in body


def test_a_language_nobody_has_translated_is_not_offered(catalogues):
    """Offering somebody their language and handing them English is a promise with
    nothing behind it. The catalogue waits for a translator; the option appears with them."""
    from postulo.accounts.forms import language_choices

    offered = {code for _group, entries in language_choices()[1:] for code, _label in entries}
    empty = set(CODES) - set(started(catalogues))
    assert empty, "this test means nothing once every catalogue has been started"
    assert not (offered & empty), f"offered but wholly untranslated: {sorted(offered & empty)}"
    assert set(started(catalogues)) <= offered | {languages.SOURCE}


def test_the_picker_groups_languages_by_how_well_translated_they_are(client, user, monkeypatch):
    """The state of a translation is a group heading, not part of a language's name.

    It used to be appended to the option text — "Deutsch — machine translation, awaiting
    review" — which put an English phrase inside the one option that has to be wholly in
    German, because that option carries `lang="de"` and a screen reader will pronounce
    every word of it accordingly.
    """
    from postulo.accounts import forms
    from postulo.core import languages as languages_module

    monkeypatch.setattr(
        languages_module,
        "_STATUS",
        {
            "de": {"total": 10, "translated": 10, "drafts": 10, "percent": 100},
            "fr-fr": {"total": 10, "translated": 10, "drafts": 0, "percent": 100},
            "pl": {"total": 10, "translated": 4, "drafts": 4, "percent": 40},
        },
    )
    with translation.override("en-gb"):
        groups = {str(label): dict(entries) for label, entries in forms.language_choices()[1:]}

    assert groups["Machine translation, awaiting review"]["de"] == "Deutsch"
    assert groups["Reviewed by a speaker"]["fr-fr"] == "français (France)"
    assert groups["Reviewed by a speaker"]["sv"] == "svenska", "no status known: the name alone"
    # A bare percentage carries no language of its own, so it may stay beside the name.
    assert groups["Partly translated"]["pl"] == "polski (40%)"


def test_every_language_says_which_language_it_is_in(client, user):
    """WCAG 2.2 3.1.2, Language of Parts, at level AA.

    Every entry is written in its own language, and without `lang` a screen reader reads
    all of them with the rules of whatever the interface is set to. This is the one list
    in Postulo where that matters most: it is where somebody who cannot read the current
    language has come to get out of it.
    """
    import re

    client.force_login(user)
    html = client.get(reverse("settings:locale")).content.decode()

    rows = re.findall(r'<input type="radio" name="language" value="([^"]*)"', html)
    assert len(rows) > 20, "the whole list is there"

    for code in rows:
        if not code:
            continue
        assert f'lang="{code}"' in html, f"{code} does not say what language it is in"

    # The "use the instance default" row is in the interface language, not in any listed
    # one, so it must not claim to be.
    assert "<span >" not in html
    assert 'lang=""' not in html


def test_each_language_shows_its_flag_without_reading_it_out(client, user):
    """A flag beside a name the person can already read is decoration, and is marked so.

    Two regional indicator characters rather than an image: no request, nothing for
    `img-src 'self'` to block, and on Windows it degrades to two letters rather than to a
    broken image.
    """
    import re

    from postulo.core import languages

    client.force_login(user)
    html = client.get(reverse("settings:locale")).content.decode()

    flags = re.findall(r'<span aria-hidden="true"[^>]*>([^<]+)</span>', html)
    assert languages.flag("el") in flags, "Greek is Greece, which its code does not say"
    assert languages.flag("cs") in flags
    assert languages.flag("ga") in flags, "Irish is Ireland, likewise"
    # Every language the picker actually offers and that has a flag shows it. Languages
    # whose catalogue has not been started are not offered at all, so theirs are absent
    # rather than missing.
    from postulo.accounts.forms import language_choices

    offered = {code for _group, entries in language_choices()[1:] for code, _label in entries}
    expected = {languages.flag(code) for code in offered if languages.flag(code)}
    assert expected <= set(flags), f"missing from the picker: {sorted(expected - set(flags))}"


def test_a_language_with_no_uncontested_home_is_given_no_flag():
    """The rule the African set forced, and the reason it is a rule and not an oversight.

    Arabic is twenty-two countries; Swahili is four; Sesotho is Lesotho's as much as South
    Africa's. A wrong flag against somebody's language is not a small wrong, and the picker
    copes perfectly well with a blank.
    """
    from postulo.core import languages

    for code in ("ar", "sw", "ha", "st", "tn", "ss", "ti", "om", "ln", "ee", "ff", "yo", "ig"):
        assert languages.flag(code) == "", f"{code} was given a flag it should not have"
    # And the ones that do have an uncontested home keep theirs.
    for code in ("am", "rw", "mg", "so", "sn", "ny", "wo", "bm", "kab"):
        assert languages.flag(code), f"{code} has one plain home and should carry its flag"


def test_a_documents_language_menu_says_the_same(client, user):
    """A CV's and a letter's language field list the same names, so they need the same."""
    import re

    client.force_login(user)
    html = client.get(reverse("documents:letter_create")).content.decode()
    field = html[
        html.index('name="language"') : html.index("</select>", html.index('name="language"'))
    ]
    assert 'lang="pt-pt"' in field or 'lang="de"' in field
    for option in re.findall(r'<option value="([^"]+)"[^>]*>', field):
        assert f'lang="{option}"' in field


def test_a_right_to_left_language_flips_the_page(client, user, settings):
    """No EU language is right-to-left; the page is ready for the phase that brings one."""
    settings.LANGUAGES = [*settings.LANGUAGES, ("ar", "العربية")]
    user.profile.language = "ar"
    user.profile.save()
    client.force_login(user)
    body = client.get(reverse("applications:list")).content.decode()
    assert 'lang="ar"' in body and 'dir="rtl"' in body
