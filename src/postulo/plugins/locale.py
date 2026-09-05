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


def locale_dir_of(module_name: str) -> Path | None:
    """The ``locale/`` directory of the top-level package ``module_name`` belongs to."""
    top = module_name.partition(".")[0]
    try:
        module = import_module(top)
    except Exception:  # pragma: no cover - the entry point itself failed to import
        return None
    file = getattr(module, "__file__", None)
    if not file:
        return None
    candidate = Path(file).resolve().parent / "locale"
    return candidate if candidate.is_dir() else None


def register_locale_dir(path: Path | str) -> bool:
    """Make Django read catalogues from ``path`` too. Returns whether anything changed.

    Django caches the merged catalogue per language the first time it is asked for it, so
    adding a path afterwards means throwing those caches away; the next ``gettext`` call
    rebuilds them with the new directory included.
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
