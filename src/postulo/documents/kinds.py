"""What kinds of document exist, said once (#133).

`DocumentKind` used to be doing two jobs at once. It labels an uploaded file — a
certificate, a reference, a portfolio somebody had made elsewhere — *and* it labels a
render Postulo produced; and separately `CoverLetter` mapped its own `LetterKind` onto it.
Two vocabularies with no relation between them, which is a disagreement waiting for a third
kind to arrive.

**So one is derived from the other now.** The values stay in `DocumentKind` because they
are stored, exported and read by the API, and a value in a database column is not a thing to
compute. What is derived is everything *said about* a kind: its label, whether Postulo
composes one or it only ever arrives as a file somebody had, which theme vocabulary sets it,
and who provides it. The two `kind` fields take their choices from here through a callable,
so a kind added by a plugin reaches every picker without a migration -- which is what makes
"a kind is a plugin" something other than a phrase.

**A kind is small on purpose.** The issue asks for a contract wide enough to declare a
model, a template per theme, what a new one starts as, how it renders, how it snapshots and
what it is called. That is a much larger surface than any other plugin kind here, and most
of it already exists somewhere better: a theme declares its own templates (#132), a
`LETTER_STARTERS`-shaped map declares what a new one starts as, and rendering goes through
`documents.rendering`. What was actually missing was a single answer to *what kinds are
there, and which of them does Postulo make?* -- so that is what this is, and the wider
surface waits until something outside needs it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Kind:
    """One kind of document Postulo knows about."""

    #: What is stored in a `kind` column. A `DocumentKind` value for Postulo's own.
    key: str
    label: object
    #: Whether Postulo composes one of these, or it only ever arrives as a file somebody
    #: already had. A certificate is issued by somebody else and can only be uploaded; a CV
    #: is written here. The difference decides which pickers offer it.
    authored: bool = False
    #: Which `themes.Kind` sets it, where one does. Empty for a kind that is never rendered.
    theme_kind: str = ""
    #: Who provides it, in words. Empty for Postulo's own.
    provider: str = ""


#: Every kind, in the order a picker should offer them. Postulo's own are registered as the
#: app loads; a plugin adds to the end.
REGISTRY: dict[str, Kind] = {}


def register(kind: Kind) -> bool:
    """Offer a kind. Returns whether it was taken.

    A key already in use is refused rather than overridden, for the reason a theme name is
    (#132): a plugin able to replace `cv` could change what every CV already filed under it
    claims to be, without anybody having chosen that.
    """
    if not kind.key:
        logger.warning("A document kind with no key was ignored")
        return False
    if kind.key in REGISTRY:
        logger.warning("Document kind %r was offered twice; the first one keeps it", kind.key)
        return False
    REGISTRY[kind.key] = kind
    return True


def forget(key: str = "") -> None:
    """Drop a plugin's kind, or every kind. For tests and for uninstalling."""
    if key:
        REGISTRY.pop(key, None)
    else:
        REGISTRY.clear()


def get(key: str) -> Kind | None:
    return REGISTRY.get(key)


def label_for(key: str) -> str:
    """A kind's name in words, or the key itself where nothing claims it.

    The key rather than an empty string: a document filed under a kind whose plugin has been
    removed still has to say something, and saying the raw value is honest where saying
    nothing would look like a bug.
    """
    kind = REGISTRY.get(key)
    return str(kind.label) if kind else key


def choices() -> list[tuple[str, object]]:
    """What a `kind` column offers. A callable, so a plugin's kind needs no migration."""
    return [(kind.key, kind.label) for kind in REGISTRY.values()]


def authored_choices() -> list[tuple[str, object]]:
    """The kinds Postulo composes. What a *render* can be filed as."""
    return [(kind.key, kind.label) for kind in REGISTRY.values() if kind.authored]


def theme_kind_for(key: str) -> str:
    """Which theme vocabulary sets this kind, or empty where none does."""
    kind = REGISTRY.get(key)
    return kind.theme_kind if kind else ""


def register_the_ones_postulo_has() -> None:
    """Postulo's own kinds, described once.

    Called from `DocumentsConfig.ready`, and idempotent, because a registry filled at import
    time is a registry that is empty in whichever test imported the module first.
    """
    from . import themes
    from .models import DocumentKind

    described = (
        Kind(DocumentKind.CV, _("CV"), authored=True, theme_kind=themes.Kind.CV),
        Kind(
            DocumentKind.PORTFOLIO,
            _("Portfolio"),
            authored=True,
            theme_kind=themes.Kind.PORTFOLIO,
        ),
        Kind(
            DocumentKind.COVER_LETTER,
            _("Cover letter"),
            authored=True,
            theme_kind=themes.Kind.LETTER,
        ),
        Kind(
            DocumentKind.MOTIVATION_LETTER,
            _("Motivation letter"),
            authored=True,
            theme_kind=themes.Kind.LETTER,
        ),
        # Held rather than authored: somebody else issues these, and Postulo keeps the file.
        Kind(DocumentKind.CERTIFICATE, _("Certificate")),
        Kind(DocumentKind.REFERENCE, _("Reference")),
        Kind(DocumentKind.OTHER, _("Other")),
    )
    for kind in described:
        if kind.key not in REGISTRY:
            register(kind)
