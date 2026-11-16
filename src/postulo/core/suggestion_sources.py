"""Where the suggestions a field offers come from, and who may be asked (#1252).

A field that offers suggestions -- an industry, a job title, a city -- used to read its list
from a module of core's. Reference data is not core's to carry: a person turns a vocabulary
off, replaces it, or installs another for their country, and core keeps the mechanism. So the
list is a registry, supplied by `suggestion-source` plugins the way link services are
(`core.link_services`).

A source says what it knows about itself:

- the **fields** it can suggest for (``industry``, ``job_title``, ``skill``, ``city`` ...);
- the **languages** its names come in, or none where it reads in whatever language it is asked;
- whether it is **offline**: a list read from files that ship with it, which a page may ask on
  every view; or needs the **network**, which it may do only on a press, and only on the
  ``hosts`` it names;
- the **licence** its data is under, and ``offer``, the function that answers.

**Only offline sources are asked by a page.** A source that calls a service names its hosts and
is asked on a press and never on a page load (#879, #1187); this module refuses one that names
none, and `offered` never asks one.

**With no source enabled a field is a plain input.** Nothing is an error: the person types
their own word, which is what almost everybody does anyway.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass

from . import modules

logger = logging.getLogger(__name__)

#: How long a source's key may be.
MAX_KEY_LENGTH = 40


@dataclass(frozen=True)
class Source:
    """One vocabulary that can suggest values for some fields.

    ``offer(field, exclude=(), language="", first=())`` -- and ``typed=""``, what has been
    typed so far, passed only to an ``offer`` that names it -- returns ``(text, group)`` pairs, in
    the order they should be shown: ``exclude`` are the values the person already has,
    ``first`` is a hint (a list of codes, source-specific) of what to put at the head, and
    ``group`` a short label the field may narrow by (an industry's sector), or ``""``.
    """

    #: The source's own key, unique across the sources installed.
    key: str
    #: What it is called where the person is told where a suggestion came from.
    label: str
    #: The fields it suggests for.
    fields: tuple[str, ...]
    #: The languages its names are in; empty where it reads in any.
    languages: tuple[str, ...]
    #: Whether it answers from files it ships with, with no network.
    offline: bool
    #: The licence its data is under.
    licence: str
    #: The function that answers; see the class.
    offer: Callable[..., list[tuple[str, str]]]
    #: The hosts a network source asks, and the only ones it may.
    hosts: tuple[str, ...] = ()


def _usable(source) -> bool:
    """Whether what a plugin handed over is a source this module can ask."""
    try:
        return (
            isinstance(source, Source)
            and bool(source.key)
            and len(source.key) <= MAX_KEY_LENGTH
            and bool(source.fields)
            and callable(source.offer)
            and bool(source.licence)
            # A source that dials out says where, and one that does not has nothing to say.
            and (bool(source.hosts) != bool(source.offline))
        )
    except Exception:  # pragma: no cover - a hostile object
        return False


def sources(field: str = "", user=None) -> list[Source]:
    """Every usable source of the enabled plugins, those that suggest for ``field`` if given.

    With a ``user``, the plugins switched off for that person are left out: a bundled source
    is on until an administrator says otherwise (`plugins.policy`), and a person may switch
    off one they installed. Postulo's own come first, then others in the order they were
    installed; the first to claim a key keeps it and a second is logged and left out.
    """
    from postulo.plugins import registry as plugin_registry

    shipped = tuple(plugin_registry.builtins().get("suggestion-source", ()))
    found: dict[str, Source] = {}
    installed = plugin_registry.plugins("suggestion-source")
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        if user is not None and not modules.is_on(user, plugin.name):
            continue
        try:
            offered = list(getattr(plugin, "sources", ()) or ())
        except Exception:
            logger.exception("Plugin %r's suggestion sources could not be read", plugin)
            continue
        for source in offered:
            if not _usable(source):
                logger.warning(
                    "Plugin %r offered a suggestion source that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    source,
                )
            elif source.key in found:
                logger.warning(
                    "Suggestion source %r was offered twice; the first one keeps it", source.key
                )
            else:
                found[source.key] = source
    return [s for s in found.values() if not field or field in s.fields]


def _takes_typed(offer) -> bool:
    """Whether ``offer`` can be told what has been typed: a source that searches (a table
    of places) needs it, and one that lists everything (a classification) need not."""
    try:
        parameters = inspect.signature(offer).parameters
    except (TypeError, ValueError):
        return False
    return "typed" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def offered(
    field: str, exclude=(), language: str = "", first=(), user=None, typed: str = ""
) -> list[tuple[str, str]]:
    """What a field's input offers: ``(text, group)`` from every enabled offline source.

    Merged in source order, one entry per text however many sources have it (the first
    keeps it). ``typed`` is what the person has typed, for a source that searches. A source
    that raises is logged and left out, so a broken plugin makes a
    plainer field and never a broken page.
    """
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources(field, user):
        if not source.offline:
            continue
        try:
            extra = {"typed": typed} if typed and _takes_typed(source.offer) else {}
            answer = source.offer(field, exclude=exclude, language=language, first=first, **extra)
        except Exception:
            logger.exception("Suggestion source %r failed for %r", source.key, field)
            continue
        for text, group in answer:
            folded = str(text).casefold()
            if folded not in seen:
                seen.add(folded)
                merged.append((text, group))
    return merged
