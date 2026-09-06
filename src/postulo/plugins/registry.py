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

from .base import CONNECTED_KINDS, ConnectedPlugin, JobPostingData, SourcePlugin
from .builtin import BUILTIN_SOURCES
from .locale import register_plugin_locale

logger = logging.getLogger(__name__)

#: The entry point group third-party sources register themselves under.
ENTRY_POINT_GROUP = "postulo.sources"

#: Every group, by the kind of plugin it holds.
GROUPS = {"source": ENTRY_POINT_GROUP, **CONNECTED_KINDS}

_cache: dict[str, list] = {}
_builtin: dict[str, list[type]] = {"source": list(BUILTIN_SOURCES)}


def _protocol_for(kind: str):
    return SourcePlugin if kind == "source" else ConnectedPlugin


def register_builtin(kind: str, plugin_class: type) -> None:
    """Add a plugin that ships inside this process — Postulo's own, or a test's.

    Built-ins come after third-party plugins of the same kind, as with sources.
    """
    if kind not in GROUPS:
        raise ValueError(f"Unknown plugin kind {kind!r}; one of {sorted(GROUPS)}.")
    registered = _builtin.setdefault(kind, [])
    if plugin_class not in registered:
        registered.append(plugin_class)
    _cache.pop(kind, None)


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
    protocol = _protocol_for(kind)
    plugins: list = []
    for entry_point in entry_points(group=GROUPS[kind]):
        try:
            plugin = entry_point.load()()
        except Exception:
            logger.exception("Plugin %r (%s) could not be loaded", entry_point.name, kind)
            continue

        # Every plugin holds its own translations: a locale/ next to its package.
        register_plugin_locale(entry_point.module)

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


def connected_plugins() -> list:
    """Every installed plugin a person can connect to, whatever its kind.

    A built-in that needs nothing from anyone — the local document store — says so with
    ``needs_connection = False`` and is left off the list: there is no form to draw.
    """
    found: list = []
    for kind in CONNECTED_KINDS:
        found.extend(p for p in plugins(kind) if getattr(p, "needs_connection", True))
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
