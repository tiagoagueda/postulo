"""Finding the sources that are installed.

A plugin is an ordinary Python package that advertises itself through an entry point:

.. code-block:: toml

    [project.entry-points."postulo.sources"]
    my-board = "my_package.source:MyBoardSource"

Installing the package registers the source; uninstalling it removes it. Postulo itself
needs no change, which is the whole point — the person who cares about a particular job
board should not have to wait for this project to accept a patch about it.

Third-party sources are tried before the built-in ones. A plugin written for a specific
site knows more about it than a general parser does, so it gets first refusal.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from .base import JobPostingData, SourcePlugin
from .builtin import BUILTIN_SOURCES

logger = logging.getLogger(__name__)

#: The entry point group third-party sources register themselves under.
ENTRY_POINT_GROUP = "postulo.sources"

_cache: list[SourcePlugin] | None = None


def _load_third_party() -> list[SourcePlugin]:
    """Instantiate every registered plugin, skipping any that will not load.

    A broken plugin disables itself and is logged. It does not take capture down with
    it: the built-in sources are still perfectly able to read the page.
    """
    plugins: list[SourcePlugin] = []
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            plugin = entry_point.load()()
        except Exception:
            logger.exception("Capture plugin %r could not be loaded", entry_point.name)
            continue

        if not isinstance(plugin, SourcePlugin):
            logger.error(
                "Capture plugin %r does not provide the source interface and was ignored",
                entry_point.name,
            )
            continue

        plugins.append(plugin)
    return plugins


def available_sources(*, refresh: bool = False) -> list[SourcePlugin]:
    """Every usable source, third-party first, then the built-ins."""
    global _cache
    if _cache is None or refresh:
        _cache = [*_load_third_party(), *(source() for source in BUILTIN_SOURCES)]
    return list(_cache)


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
