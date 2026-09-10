"""Everything a plugin may import from Postulo. Nothing else is a contract.

> a plugin, even a internal must be a self-contained as possible having is own manifest,
> is own locale, etc, and dont depende on the core

"Do not depend on the core" could not be enforced, or even checked, while there was no
written answer to *depend on what, then*. This module is that answer: the names below are a
promise, everything else in `postulo` is this month's internals, and a test walks every
plugin Postulo ships and fails on one that reaches past this (#126).

**Total independence is not the goal and cannot be.** A plugin holding one person's data
must scope it with `for_user()` or it breaks the project's first promise. One that renders
must extend the base template or it renders unstyled. One that redirects must go through
`safe_next()`, one that fetches must go through the client that checks where it is dialling.
Each of those is a *reason* to depend on Postulo, and the imperative is served by making
them a small, named, stable set rather than by pretending they are avoidable.

**This is a promise about breakage, made deliberately and now.** Once a surface exists,
changing it breaks third-party plugins between releases — which is the cost the project
avoided so far by having no such surface. It is made now because the alternative is worse:
#129 moves every shipped plugin into its own package, and a package outside `src/postulo`
has to know what it may import. Deferring the promise means either deferring that work or
doing it against an undeclared surface, which is how a surface gets set by accident.

**What that promise is.** These names keep working across a minor release. A change to any
of them is a `### ⚠️ Deprecated` entry first and a `### 🗑️ Removed` entry in a later
release, never a silent rename. Anything reached through `postulo.plugins.base`,
`postulo.core` or any other module may move without warning, whatever it looks like today.

**What is deliberately not here yet.** What a dashboard widget is handed (#125) is
unsettled; a plugin needing it is reaching past this on purpose, and
`tests/test_plugin_surface.py` records the ones that do with the reason. That list is the
map of what #129 still has to move. What a *document* template may be is settled: `Theme`
and `ThemeKind` below, plus a ``templates/`` directory beside the package (#132).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - the five names `__getattr__` resolves at run time
    # Listed here so a reader, an editor and a linter all see the whole surface in one
    # place. They cannot be imported at module load: two are Django models and this module
    # is reachable before the app registry is ready.
    from postulo.core.models import OwnedModel, OwnedQuerySet
    from postulo.core.redirects import safe_next
    from postulo.documents.themes import Kind as ThemeKind
    from postulo.documents.themes import Theme

    from .consent import ACCESS_TOKEN, access_token
    from .http import client

from .base import (
    # ------------------------------------------ what a transport carries, and how
    MAIL,
    MAX_IMPORT_BYTES,
    MEDIUMS,
    TEXT,
    # ------------------------------------------------- the protocols to satisfy
    ConnectedPlugin,
    # -------------------------------------------------- what a plugin declares
    Consent,
    # ------------------------------------------- what a store is handed, and gives back
    DocumentMetadata,
    ExternalRef,
    FeaturePlugin,
    FieldSpec,
    ImporterPlugin,
    # ---------------------------------------------------- reading a file safely
    ImportRefused,
    JobPostingData,
    Manifest,
    OutboxPlugin,
    SourcePlugin,
    StorePlugin,
    SyncPlugin,
    SyncReport,
    # ----------------------------------------------------- what it hands back
    TestResult,
    TextMessage,
    TransportPlugin,
    declares,
    description_of,
    label_of,
    manifest_of,
    medium_of,
    refuse_unreadable,
    shipped,
)

__all__ = [
    "ACCESS_TOKEN",
    "MAIL",
    "MAX_IMPORT_BYTES",
    "MEDIUMS",
    "TEXT",
    "ConnectedPlugin",
    "Consent",
    "DocumentMetadata",
    "ExternalRef",
    "FeaturePlugin",
    "FieldSpec",
    "ImportRefused",
    "ImporterPlugin",
    "JobPostingData",
    "Manifest",
    "OutboxPlugin",
    "OwnedModel",
    "OwnedQuerySet",
    "SourcePlugin",
    "StorePlugin",
    "SyncPlugin",
    "SyncReport",
    "TestResult",
    "TextMessage",
    "Theme",
    "ThemeKind",
    "TransportPlugin",
    "access_token",
    "client",
    "declares",
    "description_of",
    "label_of",
    "manifest_of",
    "medium_of",
    "refuse_unreadable",
    "safe_next",
    "shipped",
]


def __getattr__(name: str):
    """The half of the surface that cannot be imported at module load.

    `OwnedModel` and `OwnedQuerySet` are Django models, and this module is imported from
    `plugins/base.py`'s neighbourhood — long before the app registry is ready. The rest are
    cheap but kept here for one rule rather than two: everything below is looked up when it
    is asked for, so importing this module costs nothing and never touches the database.
    """
    if name in ("OwnedModel", "OwnedQuerySet"):
        from postulo.core import models

        return getattr(models, name)
    if name == "safe_next":
        # Every redirect a plugin makes goes through this, or a plugin becomes a way to
        # bounce somebody off the instance.
        from postulo.core.redirects import safe_next

        return safe_next
    if name == "client":
        # Every outbound request goes through this, or a plugin becomes a way to make the
        # server dial an address it should not. `docs/THREAT-MODEL.md` rule 5.
        from .http import client

        return client
    if name in ("Theme", "ThemeKind"):
        # How a plugin sets a document: one `Theme` per way of setting it, declaring which
        # kinds it can set by having a template for each. The templates live in a
        # `templates/` directory beside the package and go on Django's search path when the
        # plugin is registered -- which is the only way markup reaches the renderer (#132).
        from postulo.documents import themes

        return themes.Kind if name == "ThemeKind" else themes.Theme
    if name == "access_token":
        # For a connection that authenticates by consent: ask for the token at the moment of
        # use, never keep one, because refreshing is the part that has to happen then (#150).
        from .consent import access_token

        return access_token
    if name == "ACCESS_TOKEN":
        # Where a consent plugin finds its token in the settings it is handed. A plugin whose
        # `send` is given settings rather than the connection cannot call `access_token`
        # itself, so Postulo renews the token first -- before a send and before a test -- and
        # this is the key it is then under (#151).
        from .consent import ACCESS_TOKEN

        return ACCESS_TOKEN
    raise AttributeError(f"{name!r} is not part of the plugin surface. See docs/PLUGINS.md.")
