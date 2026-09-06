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
    assert len(codes) == 24
    for code in ("bg", "cs", "da", "de", "el", "es", "et", "fi", "fr-fr", "ga", "hr", "hu"):
        assert code in codes
    for code in ("it", "lt", "lv", "mt", "nl", "pl", "pt-pt", "ro", "sk", "sl", "sv"):
        assert code in codes
    assert all(name == languages.NATIVE_NAMES[code] for code, name in settings.LANGUAGES), (
        "each language under its own name"
    )


@pytest.mark.parametrize("code", CODES)
def test_every_language_has_a_complete_catalogue(code, catalogues):
    catalogue = catalogues[code]
    missing = [m.msgid for m in catalogue.messages.values() if not m.translated]
    assert catalogue.messages, f"{code}: empty catalogue"
    assert not missing, f"{code}: {len(missing)} untranslated, e.g. {missing[:3]}"
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
    path = compiled / languages.locale_dir_name(code) / "LC_MESSAGES" / "django.mo"
    with path.open("rb") as handle:
        catalogue = gettext.GNUTranslations(handle)
    forms = {catalogue.plural(n) for n in range(0, 200)}
    assert len(forms) == languages.nplurals(code), f"{code}: plural rule yields {forms}"
    one = catalogue.ngettext("%(counter)s company", "%(counter)s companies", 1)
    many = catalogue.ngettext("%(counter)s company", "%(counter)s companies", 2)
    assert "%(counter)s" in one and "%(counter)s" in many
    assert one != "%(counter)s company" or code == "en-gb"


@pytest.mark.parametrize("code", CODES)
def test_the_interface_renders_in_every_language(client, user, code, compiled):
    """Each language, one full page: nothing raises, and the page is not in English."""
    user.profile.language = code
    user.profile.save()
    client.force_login(user)
    response = client.get(reverse("applications:list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert f'lang="{code}"' in body
    assert 'dir="ltr"' in body
    with translation.override(code):
        heading = translation.gettext("Applications")
    assert heading != "Applications" or code in ("nl", "ga", "mt", "sv", "da"), (
        f"{code}: the applications heading is still English"
    )
    assert heading in body


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


def test_every_language_option_says_which_language_it_is_in(client, user):
    """WCAG 2.2 3.1.2, Language of Parts, at level AA.

    Every option is written in its own language, and without `lang` a screen reader reads
    all of them with the rules of whatever the interface is set to. This is the one list
    in Postulo where that matters most: it is where somebody who cannot read the current
    language has come to get out of it.
    """
    import re

    client.force_login(user)
    html = client.get(reverse("settings:locale")).content.decode()
    field = html[
        html.index('name="language"') : html.index("</select>", html.index('name="language"'))
    ]

    options = re.findall(r"<option[^>]*>", field)
    assert len(options) > 20, "the whole list is there"

    with_value = [o for o in options if re.search(r'value="[^"]+"', o)]
    assert with_value, "something other than the blank entry"
    for option in with_value:
        code = re.search(r'value="([^"]+)"', option).group(1)
        assert f'lang="{code}"' in option, f"{code} does not say what language it is in"

    blank = [o for o in options if 'value=""' in o]
    assert blank and "lang=" not in blank[0], (
        "the default entry is in the interface language, not in any of the listed ones"
    )


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
