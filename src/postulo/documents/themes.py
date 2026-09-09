"""What a theme is, what it owes a kind it has never seen, and where one may come from.

A theme used to be two things at once: a ``TextChoices`` in the model and a directory of
templates named after it. Two themes and two kinds is four templates. Five kinds is ten,
and a theme that had never been taught a kind resolved to a path that did not exist — a
``TemplateDoesNotExist`` at the moment somebody pressed *Export PDF* (#132).

**What a theme owes a kind it has never seen: nothing, as long as it says so first.** A
theme declares what it sets, by having a template for it, and nothing else is offered. The
alternative was falling back to `plain`, which would put somebody's classic CV beside a
plain portfolio in the same envelope; the two would not look like one person's application.
Refusing is not a worse outcome than that — it is the same outcome, said in time, which is
the whole difference. So the picker on each kind offers only the themes that can set that
kind, and reaching a pair the picker would not have offered raises `CannotRender` with a
sentence in it rather than a traceback from the template loader.

**A name nobody recognises is a different problem and gets a different answer.** A theme
that has never been taught a kind is a live choice somebody could make and must not; a
theme name left behind by an uninstalled plugin is a row remembering something that is not
here any more. Refusing there would mean a plugin somebody removed had quietly taken their
documents with it, so an unknown name falls back to `plain` and the document still exports.
Nothing is inconsistent with anything, because there is no other theme to clash with.

**Postulo's own themes set every kind Postulo has**, which `tests/test_document_themes.py`
holds them to. That is what makes "declare what you set" safe to allow: whatever a plugin
does or does not offer, no kind is left with nothing.

**Where a theme may come from, and the one that is ruled out.** Rendering executes the
template, so a theme somebody uploads is remote code execution by design, and the
docstring this module replaced was right to refuse it. Two sources are legitimate:

* **shipped inside Postulo** — the two below, vendored and reviewed;
* **inside an installed plugin** — trusted exactly as much as the plugin is, which is a
  decision an administrator made with the author, licence and source in front of them.

Markup a user uploads is neither, and is not a gap: it needs a sandboxed engine and is a
different project. `postulo.plugins.themes` is the door for the second kind, and it is the
only one.

**A theme honours direction and language.** `document_direction()` gives a rendered
document its own, and a theme that ignores it renders an Arabic CV left to right (#67).
The templates Postulo ships get this from ``base_cv.html`` and ``base_letter.html``; a
theme from outside is free not to extend those, and then it owes the ``lang`` and ``dir``
itself. Stated here because it is part of the contract rather than a detail of the base
template.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class Kind(models.TextChoices):
    """The shapes Postulo authors, which are the shapes a theme sets.

    Not `documents.models.DocumentKind`, which names what a *file* is — a certificate, a
    reference, a portfolio somebody made elsewhere. Most of those arrive as uploads and
    are never rendered at all. This is the shorter list of things Postulo composes itself,
    and it grows when a new one is written rather than when a new sort of file is accepted.
    """

    CV = "cv", _("CV")
    LETTER = "letter", _("Letter")


#: The theme a document falls back to. It sets every kind, which is what makes it a safe
#: default rather than merely a plain one.
DEFAULT = "plain"

#: How long a theme's name may be. The column has to hold it, and a column that holds
#: anything is a column somebody eventually puts a paragraph in.
MAX_NAME_LENGTH = 60


@dataclass(frozen=True)
class Theme:
    """One way of setting a document, and the kinds it is willing to set.

    ``templates`` maps a `Kind` to the template that sets it, and *is* the declaration:
    a theme sets what it has a template for, so the two cannot drift apart. A kind absent
    from it is a kind this theme has never been taught, and nothing will offer the pair.
    """

    name: str
    label: object
    templates: Mapping[str, str] = field(default_factory=dict)
    #: Who provides it, in words, for the page that lists them. Empty for Postulo's own.
    provider: str = ""

    def sets(self, kind: str) -> bool:
        return kind in self.templates


def _shipped(name: str, label) -> Theme:
    """One of Postulo's own: a directory named after it, one template per kind."""
    return Theme(
        name=name,
        label=label,
        templates={
            Kind.CV: f"documents/themes/{name}/cv.html",
            Kind.LETTER: f"documents/themes/{name}/letter.html",
        },
    )


#: The themes Postulo ships. Both set everything, which is the rule the tests hold them to.
BUILT_IN: tuple[Theme, ...] = (
    _shipped("plain", _("Plain")),
    _shipped("classic", _("Classic")),
)

#: Themes an installed plugin has registered, by name. Filled while the apps load, the way
#: a plugin's catalogues are.
_from_plugins: dict[str, Theme] = {}


def register(theme: Theme) -> bool:
    """Offer a theme from an installed plugin. Returns whether it was taken.

    A name Postulo already uses is refused rather than overridden. A plugin able to replace
    `plain` could change how every document already set in it looks, without anybody having
    chosen that, and quietly — which is more than installing a plugin should be able to do.
    Two plugins claiming one name is the same problem and gets the same answer: the first
    one registered keeps it.
    """
    if find(theme.name) is not None:
        logger.warning("Theme %r was offered twice; the first one keeps the name", theme.name)
        return False
    if not theme.templates:
        logger.warning("Theme %r sets nothing and was ignored", theme.name)
        return False
    if not theme.name or len(theme.name) > MAX_NAME_LENGTH:
        logger.warning("Theme name %r does not fit the column and was ignored", theme.name)
        return False
    _from_plugins[theme.name] = theme
    return True


def forget(name: str = "") -> None:
    """Drop a plugin's theme, or all of them. Postulo's own are not forgettable."""
    if name:
        _from_plugins.pop(name, None)
    else:
        _from_plugins.clear()


def all_themes() -> list[Theme]:
    """Every theme this instance can set a document in, Postulo's own first."""
    return [*BUILT_IN, *sorted(_from_plugins.values(), key=lambda one: one.name)]


def find(name: str) -> Theme | None:
    for theme in all_themes():
        if theme.name == name:
            return theme
    return None


def for_kind(kind: str) -> list[Theme]:
    """The themes that set this kind — which is what a picker offers.

    A registry question rather than a template question: the answer has to be available
    while somebody is choosing, because the point of all this is to say so before the
    export button rather than after it.
    """
    return [theme for theme in all_themes() if theme.sets(kind)]


def choices_for(kind: str) -> list[tuple[str, str]]:
    """The same list, as form choices, each saying where it came from.

    A theme from a plugin is named with its provider — "Vellum, from Vellum Press" — and
    Postulo's own are not, because there is nothing to distinguish them from. The picker is
    the only place a person meets a theme, and a theme is markup that runs when a document
    is exported: whose markup it is belongs beside the name rather than in a page somebody
    would have to go looking for.
    """
    return [
        (
            theme.name,
            str(
                _("%(theme)s, from %(provider)s")
                % {"theme": theme.label, "provider": theme.provider}
                if theme.provider
                else theme.label
            ),
        )
        for theme in for_kind(kind)
    ]


def label_for(name: str) -> str:
    """A theme's name in words, or its identifier if nothing here recognises it.

    What ``get_theme_display()`` used to do, and the reason it cannot any more: a label
    that came from ``choices`` could only ever name a theme compiled into the model, so a
    plugin's theme would have shown as a bare slug on every page that mentions it.
    """
    theme = find(name)
    return str(theme.label) if theme is not None else name


class CannotRender(Exception):
    """This theme does not set this kind, and says so rather than failing to find a file."""


def template_for(name: str, kind: str) -> str:
    """The template that sets ``kind`` in ``name``. Raises `CannotRender`.

    The single door: nothing else builds a theme's template path, because a path built by
    hand is a path that can name a file nobody wrote.
    """
    theme = find(name) or find(DEFAULT)
    if theme is None:  # pragma: no cover - `plain` is shipped and unforgettable
        raise CannotRender(str(_("No theme is installed that can set this document.")))
    if not theme.sets(kind):
        raise CannotRender(
            str(_("The %(theme)s theme does not set this kind of document."))
            % {"theme": theme.label}
        )
    return theme.templates[kind]


@deconstructible
class SetsThisKind:
    """Field validator: this theme exists, and sets the kind this field belongs to.

    On the field rather than only in the form, because the form is one of the ways a value
    arrives. A validator that a migration can write down is the only version of this rule
    that travels with the column (#132).
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __call__(self, value: str) -> None:
        theme = find(value)
        if theme is None:
            raise ValidationError(
                _("No theme called “%(theme)s” is installed."),
                code="unknown_theme",
                params={"theme": value},
            )
        if not theme.sets(self.kind):
            raise ValidationError(
                _("The %(theme)s theme does not set this kind of document."),
                code="wrong_kind",
                params={"theme": theme.label},
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SetsThisKind) and other.kind == self.kind

    def __hash__(self) -> int:
        return hash((type(self), self.kind))
