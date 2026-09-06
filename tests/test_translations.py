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


def test_the_picker_says_when_a_language_is_a_draft(client, user, monkeypatch):
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
        labels = dict(forms.language_choices())
    assert labels["de"] == "Deutsch — machine translation, awaiting review"
    assert labels["fr-fr"] == "français (France)"
    assert labels["pl"] == "polski — 40% translated"
    assert labels["sv"] == "svenska", "no status known: the name alone"


def test_a_right_to_left_language_flips_the_page(client, user, settings):
    """No EU language is right-to-left; the page is ready for the phase that brings one."""
    settings.LANGUAGES = [*settings.LANGUAGES, ("ar", "العربية")]
    user.profile.language = "ar"
    user.profile.save()
    client.force_login(user)
    body = client.get(reverse("applications:list")).content.decode()
    assert 'lang="ar"' in body and 'dir="rtl"' in body
