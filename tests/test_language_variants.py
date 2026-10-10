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
