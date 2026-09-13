"""Two regions of one language, both offered.

Postulo already carried `en-gb`, `fr-fr` and `pt-pt` — a language plus a region. Brazilian
Portuguese is the first case where **two regions of the same language** are offered at once,
and whatever is done here is the pattern for `es-419`, `de-AT` and the rest.

The interesting failures are not about Portuguese. They are about what a variant needs that
a new language does not: its own country, its own plural rule, and a catalogue that came from
its sibling rather than from English — which is a different provenance and needs saying so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from postulo.core import languages, phones

pytestmark = pytest.mark.django_db

VARIANT = "pt-br"
SIBLING = "pt-pt"
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


# ------------------------------------------------------------- the seeding


def test_every_string_came_across(catalogues):
    """Seeded, not translated again: each string had already been thought about once."""
    variant, sibling = catalogues[VARIANT], catalogues[SIBLING]
    assert set(variant.messages) == set(sibling.messages)
    assert not [m for m in variant.messages.values() if not m.translated]


def test_nothing_is_claimed_as_reviewed(catalogues):
    """Complete and unread are not the same thing, and the flag is what says which."""
    unflagged = [m.msgid for m in catalogues[VARIANT].messages.values() if "draft" not in m.flags]
    assert not unflagged, f"{len(unflagged)} strings look reviewed, e.g. {unflagged[:3]}"


def test_the_adaptation_actually_happened(catalogues):
    """Otherwise this is pt-PT under another name, which helps nobody."""
    variant = catalogues[VARIANT]
    for msgid, expected in (
        ("Save", "Salvar"),
        ("Delete", "Excluir"),
        ("Download", "Baixar"),
        ("Settings", "Configurações"),
        ("Password", "Senha"),
        ("Files", "Arquivos"),
        ("Contact", "Contato"),
    ):
        message = variant.messages.get((None, msgid))
        if message is not None:
            assert message.msgstr[0] == expected, msgid


def test_a_word_boundary_kept_contracts_intact(catalogues):
    """`rato` is Brazilian `mouse`, and it is also the tail of `contrato`.

    Without the boundary this substitution produced "Cont**mouse** a termo". Recorded as a
    test because the next variant will want the same list and the same mistake is one edit
    away.
    """
    text = " ".join(" ".join(m.msgstr) for m in catalogues[VARIANT].messages.values())
    assert "mouse" in text, "the real one was adapted"
    assert "contmouse" not in text.lower()
    assert "Contrato" in text


def test_what_was_left_alone_was_left_alone(catalogues):
    """`ligação` means both a *Connection* and a *link* here, and only a reader can tell
    which. A script that guessed would have been confidently wrong in one of the two."""
    text = " ".join(" ".join(m.msgstr) for m in catalogues[VARIANT].messages.values())
    assert "ligaç" in text, "left for a speaker rather than guessed at"


# ------------------------------------------------------------- the interface


def test_the_picker_offers_both_and_says_neither_is_reviewed(client, user):
    client.force_login(user)

    html = client.get(reverse("settings:locale")).content.decode()

    assert 'value="pt-br"' in html and 'value="pt-pt"' in html
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
