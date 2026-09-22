"""A plugin's logo: read out of its own package, re-encoded, and served by Postulo.

> plugins also must include a logo

The seventh thing a plugin says about itself (#97 gave it the other six), and the only one
that is not a string. Three constraints decided the shape of this, and each had already been
decided once elsewhere in Postulo.

**It cannot be a static file.** Plugins are installed at run time onto the data volume;
``collectstatic`` ran when the image was built, and production serves through
``CompressedManifestStaticFilesStorage``, which raises on a file the manifest never learned
rather than returning a dead link. A logo inside a plugin wheel is invisible to the static
machinery, always — the same wall #88 hit. So it is a view: the bytes come out of the
installed package and Postulo serves them.

**It cannot be a URL.** ``jobs/logos.py`` settled this for company logos and the reasoning
transfers exactly: the production policy is ``img-src 'self'``, and an ``<img>`` pointing at
a vendor's CDN would tell that vendor which instances run their plugin, how many people use
it, and when. Postulo serves it or it is not shown.

**Raster only**, for the reason that module gives: SVG is the format logos usually arrive in
and the one that needs care, because it can carry scripts and references to other files, and
a direct visit to the file is not the ``<img>`` context where a browser refuses to run them.
The bytes are decoded and written out again as PNG, so what is served is an image Postulo
produced from what the plugin shipped rather than the plugin's file passed through.

**A plugin with no logo is normal**, not an error: the interface falls back to the initials
tile it already uses for a person with no picture and a company with no logo, so nothing is
ever a broken image. Postulo ships no logo for any of its own built-ins, and that is a
decision rather than an omission — see *Writing a plugin* in the wiki.
"""

from __future__ import annotations

import logging
from importlib import resources

from postulo.core import pictures

from .base import manifest_of

logger = logging.getLogger(__name__)

#: Refused before it is decoded. A logo is not a photograph.
MAX_BYTES = 2 * 1024 * 1024

#: What a resolved logo is: the PNG bytes, and the plugin they belong to.
#: What is served for each plugin, and what it is. The type travels with the bytes since
#: #264: a mark may be a sanitised SVG or a re-encoded PNG, and a cache holding only bytes
#: left the view to guess.
_cache: dict[str, tuple[bytes, str] | None] = {}

#: A plugin's mark, re-encoded, may weigh this much. The same budget the company logo uses.
MAX_STORED_BYTES = 1024 * 1024


class Unusable(ValueError):
    """The declared file is not one Postulo will serve, and the message says why.

    Not translated, and deliberately: every one of these reaches a log line an
    administrator reads while working out why a plugin they installed shows no mark.
    Nothing here is ever rendered to somebody using Postulo -- `logo_for` turns all of it
    into the initials tile -- so putting it through thirty-nine catalogues would be work
    for text no reader sees.
    """


def declared_by(plugin) -> str:
    """The file name a plugin asks for, or empty."""
    return (manifest_of(plugin).logo or "").strip()


def _package_of(plugin) -> str:
    """The package to read the file out of: the one the plugin's class is defined in."""
    module = type(plugin).__module__
    package = module.rpartition(".")[0] if "." in module else module
    # A plugin that is a single module keeps its logo beside that module, which is its
    # package; one that is a package keeps it inside itself. Both resolve to a package,
    # which is what `importlib.resources` reads from and what makes the file un-escapable.
    return module if _is_package(module) else package


def _is_package(module: str) -> bool:
    import importlib.util

    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):  # pragma: no cover - a module that will not import
        return False
    return bool(spec and spec.submodule_search_locations)


def raw_bytes(plugin) -> bytes:
    """The declared file, read from inside the plugin's package. Raises :class:`Unusable`.

    The manifest gives a **name**, never a path, and this is where that is enforced: a
    separator or a leading dot is refused outright rather than normalised, because a plugin
    that wants a file outside its own package is asking for something this will not do.
    `importlib.resources` reads from the package, so even a name that got past this cannot
    name a file elsewhere.
    """
    name = declared_by(plugin)
    if not name:
        raise Unusable("this plugin declares no logo")
    if "/" in name or "\\" in name or name.startswith("."):
        raise Unusable(f"a logo is a file name, not a path: {name!r}")

    package = _package_of(plugin)
    try:
        handle = resources.files(package).joinpath(name)
        data = handle.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError) as error:
        raise Unusable(f"{name!r} could not be read from {package}") from error

    if len(data) > MAX_BYTES:
        raise Unusable(f"{name!r} is {len(data)} bytes, over the {MAX_BYTES} a logo may be")
    return data


def logo_for(plugin) -> tuple[bytes, str] | None:
    """The logo as a square PNG, or ``None`` where there is not one to show.

    Never raises: a plugin whose logo is missing or unreadable shows the initials tile,
    exactly as one that declares no logo does. A broken image beside a plugin's name would
    be a worse answer to "this file is wrong" than no image, and the log is where an
    administrator finds out.

    Cached by plugin name for the life of the process. A plugin's own files do not change
    while it is installed, and installing one rebuilds the registry.
    """
    name = getattr(plugin, "name", "")
    if name in _cache:
        return _cache[name]

    result: tuple[bytes, str] | None = None
    if declared_by(plugin):
        try:
            result = _render(raw_bytes(plugin))
        except Unusable as error:
            logger.warning("Plugin %r declares a logo that cannot be shown: %s", name, error)
    _cache[name] = result
    return result


def _render(data: bytes) -> tuple[bytes, str]:
    """What will be served, and what it is: sanitised SVG, or a re-encoded PNG.

    **An SVG is the preferred form for a plugin's mark** (#264). The threat model is not
    the one a stranger's upload has: a plugin already runs arbitrary Python inside this
    process, so an administrator who installed it has extended far more trust than a
    picture could abuse, and a mark is authored as a vector — forcing it through a raster
    round trip degrades it at the 24 pixels a list shows it at, for nothing.

    **It still goes through the sanitiser**, and the reason is privacy rather than script.
    This module exists so that a vendor's server never learns which instances run their
    plugin. An SVG carrying `<image href="https://vendor.cdn/…">`, an `@import` or a remote
    webfont puts that leak back, and it would arrive by accident far more often than by
    malice: a designer's export is full of such references.

    Imported from `core` rather than from `jobs`: this used to reach into the job-search
    app through a function-level import to borrow its idea of a picture, which is the wrong
    direction and one of the cycles #248 counts.
    """
    try:
        if pictures.looks_like_svg(data):
            return pictures.sanitise_svg(data), "image/svg+xml"
        return pictures.as_stored(data, budget=MAX_STORED_BYTES), "image/png"
    except pictures.UnusablePicture as error:
        raise Unusable(str(error)) from error


def forget(name: str = "") -> None:
    """Drop what is remembered, for one plugin or for all of them."""
    if name:
        _cache.pop(name, None)
    else:
        _cache.clear()
