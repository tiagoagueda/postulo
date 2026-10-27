"""Two regions of one language, both offered.

Postulo already carried `en-GB`, `fr-FR` and `pt-PT` — a language plus a region. Brazilian
Portuguese is the first case where **two regions of the same language** are offered at once,
and whatever is done here is the pattern for `es-419`, `de-AT` and the rest.

The interesting failures are not about Portuguese. They are about what a variant needs that
a new language does not: its own country, its own plural rule, and its own catalogue.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from postulo.core import languages, phones

pytestmark = pytest.mark.django_db

VARIANT = "pt-BR"
SIBLING = "pt-PT"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    from postulo.core import messages_tool

    messages_tool.use(REPO)
    return messages_tool


@pytest.fixture(scope="module")
def catalogues(tool):
    return {
        code: tool.parse(tool.po_path(code).read_text(encoding="utf-8"))
        for code in (VARIANT, SIBLING)
    }


@pytest.fixture(scope="module")
def compiled(tool, catalogues, tmp_path_factory):
    """Just the two, compiled into a temporary tree and registered with Django."""
    from django.utils.translation import trans_real

    root = tmp_path_factory.mktemp("variants")
    for code, catalogue in catalogues.items():
        path = root / languages.locale_dir_name(code) / "LC_MESSAGES" / "django.mo"
        path.parent.mkdir(parents=True)
        path.write_bytes(tool.compile_catalogue(catalogue))

    previous = settings.LOCALE_PATHS
    settings.LOCALE_PATHS = [root]
    trans_real._translations = {}
    trans_real._default = None
    yield root
    settings.LOCALE_PATHS = previous
    trans_real._translations = {}
    trans_real._default = None


# ------------------------------------------------------- what a variant needs


def test_a_variant_carries_its_own_country_and_not_its_siblings():
    assert languages.FLAG_COUNTRIES[VARIANT] == "BR"
    assert languages.FLAG_COUNTRIES[SIBLING] == "PT"
    assert phones.FROM_LANGUAGE[VARIANT] == "BR", "a telephone field starts where you are"


def test_the_plural_rule_is_not_copied_from_the_sibling():
    """The trap. Brazilian Portuguese treats zero as plural — *0 candidaturas* — where
    European Portuguese says *0 candidatura*. Copying the sibling's line without looking
    makes every count on every page ungrammatical for the language's largest population.
    """
    assert languages.PLURAL_FORMS[VARIANT] == "nplurals=2; plural=(n > 1);"
    assert languages.PLURAL_FORMS[SIBLING] == "nplurals=2; plural=(n != 1);"
    assert languages.PLURAL_FORMS[VARIANT] != languages.PLURAL_FORMS[SIBLING]


def test_each_variant_is_named_by_its_own_country():
    """ "português" alone would leave somebody guessing which of the two they had picked."""
    assert languages.NATIVE_NAMES[VARIANT] == "português (Brasil)"
    assert languages.NATIVE_NAMES[SIBLING] == "português (Portugal)"


def test_the_catalogue_lands_in_the_right_directory():
    assert languages.locale_dir_name(VARIANT) == "pt_BR"


# ------------------------------------------------------------- the catalogue


def test_the_variant_has_a_slot_for_every_string_its_sibling_has(catalogues):
    """The structure is this repository's; the words are Weblate's.

    pt-BR was seeded from pt-PT and adapted (docs/TRANSLATING.md). Which words it uses --
    *Salvar* for *Save*, *Excluir* for *Delete*, `ligação` left for a speaker -- used to be
    pinned here, and is not any more: translations are made in Weblate only (#706), and a
    speaker improving a word there must not turn its pull request red. What is held is what
    a commit decides: the same strings, in the same place.
    """
    variant, sibling = catalogues[VARIANT], catalogues[SIBLING]
    assert set(variant.messages) == set(sibling.messages)


# ------------------------------------------------------------- the interface


def test_the_picker_offers_both_and_says_neither_is_reviewed(client, user):
    client.force_login(user)

    html = client.get(reverse("settings:locale")).content.decode()

    assert 'value="pt-BR"' in html and 'value="pt-PT"' in html
    assert "português (Brasil)" in html


def test_both_variants_are_in_the_settings(client, user):
    codes = dict(settings.LANGUAGES)
    assert codes[VARIANT] and codes[SIBLING]


@pytest.mark.parametrize("code", [VARIANT])
def test_the_interface_renders_in_the_variant(client, user, code, compiled):
    """The same check every other language gets: it compiles, loads and renders."""
    user.profile.language = code
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert f'lang="{code}"' in response.content.decode()


# ------------------------------------------- a browser that names the language bare (#1053)


def page_language(client, asked: str) -> str:
    """The `lang` the sign-in page is drawn in for a visitor whose browser asks for this."""
    import re

    response = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE=asked)
    return re.search(r'<html lang="([^"]+)"', response.content.decode()).group(1)


@pytest.mark.parametrize(
    ("asked", "given"),
    [
        ("pt", "pt-BR"),
        ("es", "es-MX"),
        ("fr", "fr-FR"),
        ("en", "en-GB"),
        ("zh", "zh-Hans"),
    ],
)
def test_a_bare_tag_lands_on_the_variant_most_of_its_speakers_use(client, asked, given):
    """Not whichever came first on the list: that made ``pt`` European Portuguese, and ``es``,
    a code of its own, Spain's Spanish."""
    assert page_language(client, asked) == given


@pytest.mark.parametrize(
    ("asked", "given"),
    [
        ("pt-PT", "pt-PT"),
        ("es-ES", "es"),
        ("fr-CA", "fr-CA"),
        ("en-CA", "en-CA"),
        ("zh-Hant", "zh-Hant"),
        ("pt-PT,pt;q=0.8", "pt-PT"),
        ("de,pt;q=0.8", "de"),
    ],
)
def test_a_browser_that_names_a_variant_is_given_that_variant(client, asked, given):
    assert page_language(client, asked) == given


def test_a_bare_tag_is_given_another_form_of_the_language_before_none_of_it(client):
    """An instance offering European Portuguese and not the Brazilian: ``pt`` is still
    Portuguese, and not the instance's English."""
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(
        pk=1, defaults={"offered_languages": ["de", "pt-PT"], "default_language": "de"}
    )

    assert page_language(client, "pt") == "pt-PT"
    assert page_language(client, "fr") == "de", "and never another language"


def test_the_defaults_are_variants_postulo_has():
    for bare, variant in languages.DEFAULT_VARIANTS.items():
        assert languages.find(variant) == variant, variant
        assert languages.primary(variant) == bare
        assert languages.default_variant(bare.upper()) == variant
    assert languages.default_variant("pt-PT") == "", "only the bare tag is read"
    assert languages.default_variant("de") == ""


def test_the_variants_of_a_language_are_one_family():
    codes = ["en-GB", "de", "es", "es-MX", "nb", "nn", "pt-PT", "pt-BR", "zh-Hans", "zh-Hant"]

    assert languages.families(codes) == {
        "en": ["en-GB"],
        "de": ["de"],
        "es": ["es", "es-MX"],
        "nb": ["nb"],
        "nn": ["nn"],
        "pt": ["pt-PT", "pt-BR"],
        "zh": ["zh-Hans", "zh-Hant"],
    }
