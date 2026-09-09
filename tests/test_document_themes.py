"""A theme says what it sets, and nothing offers a pair it has not been taught (#132).

> all of them plaing nice with possible template extentions

Two questions had to be answered together, because either alone is unsafe. *What does a
theme owe a kind it has never seen?* — nothing, provided it says so before somebody has
chosen the pair, which makes the picker the place this is decided rather than the renderer.
And *where may a theme come from?* — from Postulo, or from inside an installed plugin, and
from nowhere else: rendering executes the template, so an upload form here would be remote
code execution with a file picker on it.

The tests that matter most are the two refusals and the one fallback, because they are not
the same rule and getting them backwards is a real loss either way. A theme that does not
set this kind must be refused, or somebody's classic CV arrives beside a plain portfolio.
A theme name nobody recognises — a plugin that was uninstalled — must *not* be refused, or
removing a plugin quietly took somebody's documents with it.
"""

from __future__ import annotations

import sys

import pytest
from django.core.exceptions import ValidationError
from django.template.loader import get_template
from django.urls import reverse

from postulo.documents import themes
from postulo.documents.models import CV, CoverLetter, LetterKind

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_plugin_themes():
    """Nothing a test registers survives into the next one."""
    yield
    themes.forget()


def a_theme(name: str, *kinds: str, label: str = "") -> themes.Theme:
    return themes.Theme(
        name=name,
        label=label or name.title(),
        templates={kind: f"documents/themes/plain/{kind}.html" for kind in kinds},
    )


# --------------------------------------------------- what Postulo itself guarantees


def test_postulos_own_themes_set_every_kind_postulo_has():
    """The rule that makes "declare what you set" safe to allow.

    Whatever a plugin offers or declines to offer, no kind of document is left with nothing
    that can set it — so refusing a pair is always a narrowing of choice, never the removal
    of the only option.
    """
    for kind in themes.Kind:
        able = {theme.name for theme in themes.for_kind(kind)}
        assert able == {"plain", "classic"}, kind


def test_every_template_a_shipped_theme_declares_actually_exists():
    """A theme that declares a kind it has no template for is the original bug, moved."""
    for theme in themes.BUILT_IN:
        for kind, path in theme.templates.items():
            assert get_template(path), f"{theme.name} says it sets {kind} but has no {path}"


@pytest.mark.parametrize("theme", ["plain", "classic"])
def test_a_shipped_theme_lays_an_arabic_cv_out_right_to_left(user, theme):
    """Part of the contract, not a detail of the base template (#67).

    A theme that ignores the direction renders an Arabic CV left to right, and the person
    it goes to reads a document that is wrong in a way no spell-check catches.
    """
    from postulo.documents.rendering import render_cv_html

    cv = CV.objects.create(owner=user, name="سيرة", theme=theme, language="ar")

    html = render_cv_html(cv)

    assert 'dir="rtl"' in html
    assert 'lang="ar"' in html


# ------------------------------------------------------ the picker, before the button


def test_the_cv_picker_offers_only_themes_that_set_a_cv(user):
    themes.register(a_theme("lettery", themes.Kind.LETTER))
    from postulo.documents.forms import CVForm

    offered = {value for value, _label in CVForm(user=user).fields["theme"].choices}

    assert offered == {"plain", "classic"}, "a letters-only theme was offered for a CV"


def test_the_letter_picker_offers_a_plugins_letter_theme(user):
    themes.register(a_theme("lettery", themes.Kind.LETTER))
    from postulo.documents.forms import CoverLetterForm

    offered = {value for value, _label in CoverLetterForm(user=user).fields["theme"].choices}

    assert offered == {"plain", "classic", "lettery"}


def test_the_picker_says_whose_markup_a_theme_is(user):
    """A theme runs when a document is exported, so whose it is belongs beside the name."""
    themes.register(
        themes.Theme(
            name="vellum",
            label="Vellum",
            templates={themes.Kind.LETTER: "documents/themes/plain/letter.html"},
            provider="Vellum Press",
        )
    )

    labels = dict(themes.choices_for(themes.Kind.LETTER))

    assert labels["vellum"] == "Vellum, from Vellum Press"
    assert labels["plain"] == "Plain", "Postulo's own have nothing to be distinguished from"


def test_choosing_a_theme_that_does_not_set_this_kind_is_refused_by_the_form(user, client):
    themes.register(a_theme("lettery", themes.Kind.LETTER))
    client.force_login(user)

    response = client.post(
        reverse("documents:cv_create"), {"name": "Backend", "theme": "lettery", "language": ""}
    )

    assert response.status_code == 200, "the form should come back, not save"
    assert not CV.objects.filter(name="Backend").exists()


def test_the_field_says_the_same_thing_to_anything_that_arrives_another_way(user):
    """The validator travels with the column, because the form is one door of several."""
    themes.register(a_theme("lettery", themes.Kind.LETTER))
    cv = CV(owner=user, name="Backend", theme="lettery")

    with pytest.raises(ValidationError) as raised:
        cv.full_clean()

    assert "theme" in raised.value.error_dict
    assert raised.value.error_dict["theme"][0].code == "wrong_kind"


def test_a_name_nothing_recognises_is_refused_too(user):
    cv = CV(owner=user, name="Backend", theme="brutalist")

    with pytest.raises(ValidationError) as raised:
        cv.full_clean()

    assert raised.value.error_dict["theme"][0].code == "unknown_theme"


# ----------------------------------------------------------- and at the renderer


def test_rendering_a_pair_the_picker_would_not_offer_says_so(user):
    """`CannotRender`, not `TemplateDoesNotExist`.

    Reaching here means something asked for the pair directly, and the honest answer is a
    sentence about themes rather than a traceback naming a file nobody wrote.
    """
    themes.register(a_theme("lettery", themes.Kind.LETTER))

    with pytest.raises(themes.CannotRender) as raised:
        themes.template_for("lettery", themes.Kind.CV)

    assert "does not set this kind" in str(raised.value)


def test_a_theme_a_removed_plugin_left_behind_still_exports(user):
    """The one place a fallback is right, and why it is not the same question.

    A theme that cannot set this kind is a live choice somebody could still make. A theme
    that is not installed any more is a row remembering something that has gone, and
    refusing there would mean uninstalling a plugin had taken somebody's CV with it.
    """
    from postulo.documents.rendering import render_cv_html

    cv = CV.objects.create(owner=user, name="Backend", theme="brutalist")

    assert themes.template_for("brutalist", themes.Kind.CV) == "documents/themes/plain/cv.html"
    assert "<html" in render_cv_html(cv), "a removed plugin took the document with it"


def test_editing_a_cv_whose_theme_was_removed_does_not_stop_on_a_field_nobody_touched(user):
    """It already renders as Plain; the form stops pretending otherwise.

    Somebody opening a CV to fix a headline should not be handed an error about a theme
    they never chose to lose. Falling the picker back to what the document actually looks
    like is the smallest true thing to say.
    """
    from postulo.documents.forms import CVForm

    cv = CV.objects.create(owner=user, name="Backend", theme="brutalist")

    form = CVForm(instance=cv, user=user)

    assert form["theme"].value() == "plain"


def test_nothing_builds_a_theme_path_by_hand():
    """One door, because a path built by hand can name a file nobody wrote.

    The two f-strings this replaced are how a kind with no template became a
    ``TemplateDoesNotExist`` at the moment of export rather than a menu that never
    offered it.
    """
    from pathlib import Path

    source = Path("src/postulo/documents/rendering.py").read_text(encoding="utf-8")

    assert "documents/themes/" not in source
    assert "themes.template_for(" in source


# --------------------------------------------------------- what a plugin may claim


def test_a_plugin_cannot_take_a_name_postulo_ships():
    """It could otherwise change how every document already set in `plain` looks, quietly."""
    assert themes.register(a_theme("plain", themes.Kind.CV, label="Not Postulo's")) is False
    assert themes.label_for("plain") == "Plain"


def test_two_plugins_claiming_one_name_leave_it_with_the_first():
    assert themes.register(a_theme("shared", themes.Kind.CV, label="First")) is True
    assert themes.register(a_theme("shared", themes.Kind.CV, label="Second")) is False
    assert themes.label_for("shared") == "First"


def test_a_theme_that_sets_nothing_is_not_a_theme():
    assert themes.register(themes.Theme(name="empty", label="Empty")) is False
    assert themes.find("empty") is None


def test_a_name_the_column_cannot_hold_is_ignored():
    """A theme name is somebody else's to choose now, within a limit that still fits."""
    assert themes.register(a_theme("x" * (themes.MAX_NAME_LENGTH + 1), themes.Kind.CV)) is False


def test_a_plugins_theme_has_a_name_in_words_on_every_page_that_mentions_it(user, client):
    """What ``get_theme_display()`` could never have done, which is why it had to go."""
    themes.register(a_theme("lettery", themes.Kind.LETTER, label="Letterpress"))
    letter = CoverLetter.objects.create(
        owner=user, name="To Aperture", kind=LetterKind.COVER, body="Dear …", theme="lettery"
    )

    assert letter.theme_label == "Letterpress"
    assert CV(theme="brutalist").theme_label == "brutalist", "and a slug beats a blank"


def test_the_field_no_longer_freezes_a_list_into_every_migration():
    """The migration boundary the issue names.

    Choices are written into every migration that touches the field, so a theme arriving
    from a plugin could never be one of them.
    """
    assert CV._meta.get_field("theme").choices is None
    assert CoverLetter._meta.get_field("theme").choices is None


# ------------------------------------------------- how a theme arrives from outside


@pytest.fixture
def theme_plugin(tmp_path, monkeypatch):
    """A throwaway plugin package with a theme and the templates that set it."""
    package = tmp_path / "vellum"
    (package / "templates" / "vellum").mkdir(parents=True)
    (package / "templates" / "vellum" / "letter.html").write_text(
        '<html lang="{{ document_language }}" dir="{{ document_direction }}">'
        "<body>{{ body }}</body></html>",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import Theme, ThemeKind\n"
        "class Vellum:\n"
        "    name = 'vellum'\n"
        "    kind = 'feature'\n"
        "    themes = [Theme(name='vellum', label='Vellum',\n"
        "                    templates={ThemeKind.LETTER: 'vellum/letter.html'},\n"
        "                    provider='Vellum Press')]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield package
    sys.modules.pop("vellum", None)


def test_a_plugins_theme_and_its_templates_arrive_together(theme_plugin, settings, user):
    """The whole door, end to end: declare a theme, ship the markup, render a document."""
    from postulo.plugins.themes import register_plugin_themes

    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": list(settings.TEMPLATES[0]["DIRS"])}]
    from vellum import Vellum

    assert register_plugin_themes(Vellum) == 1

    letter = CoverLetter.objects.create(
        owner=user, name="To Aperture", body="Dear …", theme="vellum", language="he"
    )
    from postulo.documents.rendering import render_letter_html

    html = render_letter_html(letter)
    assert "Dear …" in html
    assert 'dir="rtl"' in html, "a theme from outside owes the direction too"
    assert themes.find("vellum").provider == "Vellum Press"


def test_a_plugin_cannot_replace_a_page_of_postulos_interface(theme_plugin, settings):
    """Appended, never prepended, exactly as its catalogues are.

    Django's loader takes the first template it finds, so Postulo's own directory staying
    first is what stops a plugin shipping ``documents/cv_list.html`` and quietly becoming
    the CV list.
    """
    from postulo.plugins import themes as plugin_themes

    first = settings.TEMPLATES[0]["DIRS"][0]
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": list(settings.TEMPLATES[0]["DIRS"])}]

    assert plugin_themes.register_template_dir(theme_plugin / "templates") is True
    assert plugin_themes.register_template_dir(theme_plugin / "templates") is False, "once"

    assert settings.TEMPLATES[0]["DIRS"][0] == first
    plugin_themes._registered.remove(str((theme_plugin / "templates").resolve()))


def test_a_plugin_that_declares_no_theme_gets_no_template_directory(settings):
    """An unused search path is a file somebody can shadow by accident."""
    from postulo.plugins.themes import register_plugin_themes

    before = list(settings.TEMPLATES[0]["DIRS"])

    class Quiet:
        name = "quiet"

    assert register_plugin_themes(Quiet) == 0
    assert settings.TEMPLATES[0]["DIRS"] == before


def test_a_plugin_that_cannot_list_its_themes_does_not_take_the_page_down():
    """A broken plugin disables itself, as everywhere else in the registry."""
    from postulo.plugins.themes import themes_of

    class Broken:
        name = "broken"

        def themes(self):
            raise RuntimeError("no")

    assert themes_of(Broken()) == []


def test_the_surface_is_where_a_plugin_gets_the_two_names_it_needs():
    """A plugin importing `postulo.documents.themes` would be reaching past the surface."""
    from postulo.plugins import api

    assert api.Theme is themes.Theme
    assert api.ThemeKind is themes.Kind
    assert {"Theme", "ThemeKind"} <= set(api.__all__)
