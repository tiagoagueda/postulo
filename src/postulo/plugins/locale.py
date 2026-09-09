"""Every plugin holds its own translations.

A plugin's labels, help texts and messages are the plugin's to translate: Postulo's own
catalogues never carry them, so a plugin author can add a language without a Postulo
release and a plugin translated into a language Postulo does not yet speak still works.
The rule is simple — a ``locale/`` directory next to the package, laid out the way
Django's ``makemessages`` lays it out — and the registry does the rest: when a plugin is
loaded, its ``locale/`` joins the paths Django reads catalogues from.
"""

from __future__ import annotations

import logging
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.utils.translation import trans_real

logger = logging.getLogger(__name__)

_registered: list[str] = []


def _directory_of(module_name: str) -> Path | None:
    """Where a module lives: its own directory if it is a package, else its package's."""
    try:
        module = import_module(module_name)
    except Exception:  # pragma: no cover - the entry point itself failed to import
        return None
    file = getattr(module, "__file__", None)
    return Path(file).resolve().parent if file else None


def nearest_directory(module_name: str, name: str) -> Path | None:
    """The nearest directory called ``name`` at or above the package ``module_name`` is in.

    Nearest, rather than the top-level package's, because a plugin Postulo ships lives
    *inside* ``postulo`` — and ``postulo/locale`` is Postulo's own catalogue, so a rule
    that looked only at the top level could never find a built-in's own (#127). Looking
    outward from the plugin finds ``plugins/builtin/locale`` for one that has moved its
    strings, and ``postulo/locale`` for one that has not, which is the correct answer in
    both cases and lets #129 move them one at a time.

    For a third-party plugin the two rules agree: a package with its catalogues beside it
    is found at the first step, and one that keeps them at the distribution root is found
    on the way up.

    Written once and given a parameter because ``locale/`` turned out not to be the only
    thing a plugin keeps beside itself: ``templates/`` follows the identical rule for the
    identical reason (#132), and two copies of this loop would be two places to fix the
    day the rule is wrong.
    """
    inner = _directory_of(module_name)
    outer = _directory_of(module_name.partition(".")[0])
    if inner is None or outer is None:
        return None
    candidate = inner
    while True:
        found = candidate / name
        if found.is_dir():
            return found
        if candidate == outer or candidate.parent == candidate:
            return None
        candidate = candidate.parent


def locale_dir_of(module_name: str) -> Path | None:
    """The nearest ``locale/`` at or above the package ``module_name`` lives in."""
    return nearest_directory(module_name, "locale")


def register_locale_dir(path: Path | str) -> bool:
    """Make Django read catalogues from ``path`` too. Returns whether anything changed.

    Django caches the merged catalogue per language the first time it is asked for it, so
    adding a path afterwards means throwing those caches away; the next ``gettext`` call
    rebuilds them with the new directory included. Every caller is a plugin being
    registered, and every plugin is registered while the apps are loading, so the throwing
    away happens at start-up and never during a request.

    Appended, never prepended, and that decides who wins: Django merges
    ``reversed(LOCALE_PATHS)`` with each merge overriding the last, so the *first* path
    wins a msgid that two catalogues both define. Postulo's own is first, so a plugin
    cannot change a word in Postulo's interface by translating the same English string
    differently (#127).
    """
    path = str(Path(path).resolve())
    current = [str(Path(p).resolve()) for p in settings.LOCALE_PATHS]
    if path in current or path in _registered:
        return False
    settings.LOCALE_PATHS = [*settings.LOCALE_PATHS, path]
    _registered.append(path)
    trans_real._translations = {}
    trans_real._default = None
    logger.debug("Reading translations from %s", path)
    return True


def register_plugin_locale(module_name: str) -> bool:
    """Register the ``locale/`` of the package that ``module_name`` lives in, if it has one."""
    directory = locale_dir_of(module_name)
    return register_locale_dir(directory) if directory else False
