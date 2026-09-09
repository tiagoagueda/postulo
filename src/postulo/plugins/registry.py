"""Finding the plugins that are installed.

A plugin is an ordinary Python package that advertises itself through an entry point:

.. code-block:: toml

    [project.entry-points."postulo.sources"]
    my-board = "my_package.source:MyBoardSource"

    [project.entry-points."postulo.notifiers"]
    apprise = "postulo_apprise:AppriseNotifier"

Installing the package registers the plugin; uninstalling it removes it. Postulo itself
needs no change, which is the whole point — the person who cares about a particular job
board, or a particular way of being notified, should not have to wait for this project to
accept a patch about it.

There are two families. **Sources** read a posting off a page and are stateless.
**Connected plugins** — notifiers, stores, syncs — talk to another service on a person's
behalf and need a :class:`~postulo.plugins.models.Connection` holding where and how. Both
are found the same way, and a plugin that fails to load is logged and left out rather
than taking anything else down.

Third-party sources are tried before the built-in ones. A plugin written for a specific
site knows more about it than a general parser does, so it gets first refusal.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from .base import (
    CONNECTED_KINDS,
    FEATURE_GROUP,
    IDENTIFIER_GROUP,
    IMPORTER_GROUP,
    TRANSPORT_GROUP,
    ConnectedPlugin,
    FeaturePlugin,
    IdentifierPlugin,
    ImporterPlugin,
    JobPostingData,
    SourcePlugin,
    TransportPlugin,
)
from .builtin import BUILTIN_SOURCES
from .locale import register_plugin_locale
from .themes import register_plugin_themes

logger = logging.getLogger(__name__)

#: The entry point group third-party sources register themselves under.
ENTRY_POINT_GROUP = "postulo.sources"

#: Every group, by the kind of plugin it holds. Sources and importers are stateless and
#: need nothing from anybody; the connected kinds each need a `Connection`; a transport is
#: instance plumbing and belongs to nobody in particular; a feature is a part of Postulo
#: itself rather than anything outside it.
#:
#: An **outbox** is a connected kind and deliberately not a transport: it sends as the
#: person rather than as the instance, so it is theirs to switch off -- which a transport
#: could never be, because that is an account nobody can recover (#149).
#: An **identifier** plugin advertises no group at all. It is internal for now, and an
#: empty group is how that is *enforced* rather than merely intended: `_load_third_party`
#: returns nothing for one, so no package outside this process can contribute a scheme
#: until there is a contract worth promising (#109).
GROUPS = {
    "source": ENTRY_POINT_GROUP,
    "importer": IMPORTER_GROUP,
    "transport": TRANSPORT_GROUP,
    "feature": FEATURE_GROUP,
    "identifier": IDENTIFIER_GROUP,
    **CONNECTED_KINDS,
}

_cache: dict[str, list] = {}
_builtin: dict[str, list[type]] = {"source": list(BUILTIN_SOURCES)}


def _protocol_for(kind: str):
    return {
        "source": SourcePlugin,
        "importer": ImporterPlugin,
        "transport": TransportPlugin,
        "feature": FeaturePlugin,
        "identifier": IdentifierPlugin,
    }.get(kind, ConnectedPlugin)


def _disabled() -> set[str]:
    """Plugins an administrator has switched off. They stay installed and do not load."""
    try:
        from .installing import disabled_names

        return disabled_names()
    except Exception:  # pragma: no cover - a broken record must not take capture down
        logger.exception("The plugins record could not be read")
        return set()


def _distribution_of(entry_point) -> str:
    from .installing import canonicalise

    distribution = getattr(entry_point, "dist", None)
    name = getattr(distribution, "name", "") if distribution is not None else ""
    return canonicalise(name)


def register_builtin(kind: str, plugin_class: type) -> None:
    """Add a plugin that ships inside this process — Postulo's own, or a test's.

    Built-ins come after third-party plugins of the same kind, as with sources.
    """
    if kind not in GROUPS:
        raise ValueError(f"Unknown plugin kind {kind!r}; one of {sorted(GROUPS)}.")
    registered = _builtin.setdefault(kind, [])
    if plugin_class not in registered:
        registered.append(plugin_class)
    # A built-in holds its own translations too, or the rule in docs/PLUGINS.md is one
    # every plugin Postulo ships breaks (#127). One that has not moved its strings yet
    # finds Postulo's own catalogue, which is already registered, and nothing happens.
    register_plugin_locale(plugin_class.__module__)
    # And its themes, for the same reason: a plugin that sets documents brings the markup
    # that sets them, and nothing else may hand a template to the renderer (#132).
    register_plugin_themes(plugin_class)
    _cache.pop(kind, None)


def builtins() -> dict[str, list[type]]:
    """Every plugin class that ships inside this process, by kind.

    Public so that a test can walk them and fail on one that says nothing about itself. The
    built-ins are registered from three different app configs and a module-level list, which
    makes "all of them" easy to miscount by hand -- and a built-in added later would be
    exactly the one nobody remembered to check (#98).
    """
    return {kind: list(registered) for kind, registered in _builtin.items() if registered}


def register_builtin_locales() -> None:
    """Register the catalogues of every built-in, including those registered at import.

    ``register_builtin`` covers the ones an app config registers, but the two built-in
    sources are seeded into ``_builtin`` when this module is imported, which can be before
    the settings are usable. Sweeping at ``AppConfig.ready`` catches those, costs nothing
    for the ones already registered, and puts every catalogue in place before the first
    request rather than during one.
    """
    for classes in builtins().values():
        for plugin_class in classes:
            register_plugin_locale(plugin_class.__module__)


def register_builtin_themes() -> None:
    """Register the themes of every built-in, for the reason the catalogues are swept.

    None of Postulo's own plugins declares one today; Postulo's two themes are its own and
    need no plugin to arrive. The sweep exists so that the first one that does is registered
    by the same path a third-party plugin's is, rather than by a line somebody remembers to
    add (#132).
    """
    for classes in builtins().values():
        for plugin_class in classes:
            register_plugin_themes(plugin_class)


def unregister_builtin(kind: str, plugin_class: type) -> None:
    registered = _builtin.get(kind, [])
    if plugin_class in registered:
        registered.remove(plugin_class)
    _cache.pop(kind, None)


def _load_third_party(kind: str) -> list:
    """Instantiate every registered plugin of ``kind``, skipping any that will not load.

    A broken plugin disables itself and is logged. It does not take the feature down with
    it: the built-in plugins are still perfectly able to do their job.
    """
    if not GROUPS[kind]:
        # No group advertised: this kind is internal, and nothing outside registers one.
        return []
    protocol = _protocol_for(kind)
    plugins: list = []
    switched_off = _disabled()
    for entry_point in entry_points(group=GROUPS[kind]):
        if _distribution_of(entry_point) in switched_off:
            continue
        try:
            plugin = entry_point.load()()
        except Exception:
            logger.exception("Plugin %r (%s) could not be loaded", entry_point.name, kind)
            continue

        # Every plugin holds its own translations: a locale/ next to its package.
        register_plugin_locale(entry_point.module)
        # And, if it sets documents, the templates that set them (#132).
        register_plugin_themes(plugin, entry_point.module)

        if not isinstance(plugin, protocol):
            logger.error(
                "Plugin %r does not provide the %s interface and was ignored",
                entry_point.name,
                kind,
            )
            continue

        if kind != "source" and getattr(plugin, "kind", None) != kind:
            logger.error(
                "Plugin %r registered as a %s but calls itself a %r; ignored",
                entry_point.name,
                kind,
                getattr(plugin, "kind", None),
            )
            continue

        plugins.append(plugin)
    return plugins


def plugins(kind: str, *, refresh: bool = False) -> list:
    """Every usable plugin of ``kind``, third-party first, then the built-ins."""
    if kind not in GROUPS:
        raise ValueError(f"Unknown plugin kind {kind!r}; one of {sorted(GROUPS)}.")
    if kind not in _cache or refresh:
        _cache[kind] = [
            *_load_third_party(kind),
            *(plugin_class() for plugin_class in _builtin.get(kind, [])),
        ]
    return list(_cache[kind])


def find_plugin(kind: str, name: str):
    """The plugin of ``kind`` called ``name``, or ``None`` if it is not installed."""
    for plugin in plugins(kind):
        if plugin.name == name:
            return plugin
    return None


def find_any(name: str):
    """The plugin called ``name``, whatever kind it is, or ``None``.

    A name is unique across the instance -- it is what the policy rows key on -- so
    something addressed by name alone, like a logo, does not need to be told the kind as
    well (#106).
    """
    for kind in GROUPS:
        found = find_plugin(kind, name)
        if found is not None:
            return found
    return None


def connected_plugins(person=None) -> list:
    """Every installed plugin a person can connect to, whatever its kind.

    A built-in that needs nothing from anyone — the local document store — says so with
    ``needs_connection = False`` and is left off the list: there is no form to draw.

    Given a person, what an administrator has decided for them applies (#95). Without one
    this is every installed plugin, which is what an administration page wants.
    """
    from .policy import plugins_for

    found: list = []
    for kind in CONNECTED_KINDS:
        of_kind = plugins_for(person, kind) if person is not None else plugins(kind)
        found.extend(p for p in of_kind if getattr(p, "needs_connection", True))
    return found


def available_sources(*, refresh: bool = False) -> list[SourcePlugin]:
    """Every usable source, third-party first, then the built-ins."""
    return plugins("source", refresh=refresh)


def parse_page(url: str, html: str) -> tuple[JobPostingData, SourcePlugin] | None:
    """Ask each source in turn, and take the first answer.

    A source that raises is skipped rather than allowed to fail the capture. Parsing
    somebody else's markup is exactly the kind of work that throws unexpectedly, and the
    next source along may well cope.
    """
    for source in available_sources():
        try:
            if not source.can_handle(url):
                continue
            parsed = source.parse(url, html)
        except Exception:
            logger.exception("Capture source %r failed on %s", source.name, url)
            continue
        if parsed is not None:
            return parsed, source
    return None
