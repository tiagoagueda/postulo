"""How a theme gets from an installed plugin to the renderer, and how far that goes.

The rule is the one `locale.py` already states for translations, applied to markup: a
plugin that brings a theme brings its own ``templates/`` directory, and registering the
plugin puts that directory on the path Django looks for templates in. Nothing is copied,
nothing is compiled, and the plugin keeps its templates where its author put them.

**This is the only door, and it opens exactly as wide as installing a plugin does.**
Rendering executes a template, so a theme is code — which is why there is no upload form
anywhere near this and never will be (`postulo.documents.themes` argues that at length).
A plugin's templates run because an administrator installed the plugin, having seen its
author, licence and source; that is the same trust that already lets it parse job adverts
and hold somebody's credentials, not a new one being granted here.

**Appended, never prepended, and that decides who wins.** Django's filesystem loader walks
``DIRS`` in order and takes the first template it finds, so Postulo's own directory stays
first and a plugin cannot replace a page of Postulo's interface by shipping a file at the
same path. Between plugins the first one registered wins, for the same reason.

**A plugin that declares no theme gets no template directory.** Not caution for its own
sake: an unused search path is a file somebody can shadow by accident, and there is no
reason to add one for a plugin that only parses adverts.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from postulo.documents import themes

from .locale import nearest_directory

logger = logging.getLogger(__name__)

_registered: list[str] = []


def template_dir_of(module_name: str) -> Path | None:
    """The nearest ``templates/`` at or above the package ``module_name`` lives in.

    The same walk that finds a plugin's catalogues, with a different name: a plugin Postulo
    ships lives *inside* ``postulo``, whose own ``templates/`` is already on the path, so
    stopping at the top-level package gives the right answer for a built-in and for a
    third-party package alike.
    """
    return nearest_directory(module_name, "templates")


def register_template_dir(path: Path | str) -> bool:
    """Make Django look for templates in ``path`` too. Returns whether anything changed.

    The engine is built the first time a template is rendered and holds its own copy of the
    directories, so a path added afterwards would never be looked in. Every caller here is a
    plugin being registered while the apps load — before any request — but the engine is
    thrown away anyway, because "before any request" is a claim about call order that a
    future caller could quietly break.
    """
    path = str(Path(path).resolve())
    configured = settings.TEMPLATES[0].setdefault("DIRS", [])
    if path in [str(Path(one).resolve()) for one in configured] or path in _registered:
        return False
    settings.TEMPLATES[0]["DIRS"] = [*configured, path]
    _registered.append(path)
    _forget_the_engines()
    logger.debug("Reading templates from %s", path)
    return True


def _forget_the_engines() -> None:
    """Drop Django's cached template engines so the new directory is picked up.

    The same three lines Django's own ``setting_changed`` receiver runs for ``TEMPLATES``,
    which is the supported way to say "this setting moved" — there is no public API for it
    outside the test signals.
    """
    from django.template import engines

    try:
        del engines.templates
    except AttributeError:
        pass
    engines._engines = {}


def themes_of(plugin) -> list[themes.Theme]:
    """What this plugin says it can set documents in.

    A sequence attribute or a method, because a theme whose label is translated is cheaper
    to build once at import than on every call, and a theme assembled from something the
    plugin had to look up is cheaper as a method. Neither is the right answer for both.
    """
    declared = getattr(plugin, "themes", None)
    if callable(declared):
        try:
            declared = declared()
        except Exception:
            logger.exception("Plugin %r could not list its themes", getattr(plugin, "name", plugin))
            return []
    if not declared:
        return []
    return [one for one in declared if isinstance(one, themes.Theme)]


def register_plugin_themes(plugin, module_name: str = "") -> int:
    """Take a plugin's themes, and its templates with them. Returns how many were taken.

    ``module_name`` is where to look for the ``templates/`` directory; by default the module
    the plugin is defined in, which is right for a class and for an instance of one.
    """
    declared = themes_of(plugin)
    if not declared:
        return 0

    module_name = module_name or getattr(plugin, "__module__", "") or type(plugin).__module__
    directory = template_dir_of(module_name)
    if directory is not None:
        register_template_dir(directory)
    else:
        logger.warning(
            "Plugin %r declares themes but has no templates/ directory",
            getattr(plugin, "name", module_name),
        )

    return sum(1 for one in declared if themes.register(one))
