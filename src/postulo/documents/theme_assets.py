"""The files a theme carries in its own package, written into the document it sets (#343).

A theme could not bring a file of its own. The renderer fetches nothing while it draws
(#163): WeasyPrint is handed a fetcher that opens ``data:`` addresses and nothing else, with
no base address to resolve a relative one against, and Chromium aborts every request the
page makes. So the rule for a theme was "its CSS inline, and anything else as a `data:`
address" -- and nothing helped a theme follow it. A logo, an icon or a typeface had to be
pasted into the template by hand as base64, or be left out.

**Inlined before the renderer starts, and the fetchers stay closed.** The alternative was an
address the fetcher would map to a package's files, which keeps each document smaller and
opens the fetcher a little. Inlining opens nothing: by the time the HTML reaches either
renderer the file is already in it, so `tests/security/test_pdf_fetching.py` holds exactly
what it held before, and the preview shows what the PDF shows because it is the same HTML.

**One tag, three answers, chosen by what the file is rather than by what it is called.** The
first bytes of the file decide (`kind_of`), and a name ending ``.png`` that holds something
else is refused:

* an **SVG** is written as markup, sanitised by the allowlist Postulo's own pictures use
  (`core.pictures`): no script, no event handler, no ``foreignObject``, no reference outside
  the file, ``<use href>`` to a fragment only. Its styles are turned into presentation
  attributes first, so a drawing made in an editor keeps its colours (see `_styles_to_attributes`);
* a **raster image** -- PNG, JPEG, WebP, GIF -- is written as a ``data:`` address, for an
  ``src`` or a ``url()``;
* a **font** -- WOFF2, WOFF, TrueType, OpenType -- is written as an ``@font-face`` rule whose
  source is a ``data:`` address, for the theme's ``<style>``.

**The name is held to the package's own directory.** Read through `importlib.resources`, so
a theme installed as a wheel, or imported from a zip, reads the same way; a ``..``, an
absolute path, a drive, a backslash, a hidden file or a symbolic link out of the package is
refused rather than normalised, as a plugin's logo name is (`plugins.logos`).

**Two ceilings**, because every drawn document carries what its theme inlined, and a filed
document keeps its HTML: `MAX_ASSET_BYTES` for one file and `MAX_DOCUMENT_BYTES` for all of a
document's, counted as the files are read. A file over either is not written, and the log
says why; the document still draws. The wiki's *Writing a plugin* states both numbers.

**A theme's fonts are checked the way the server's are (#74).** A theme declares the scripts
each font draws (`Theme.font_scripts`), and a declaration is not a glyph: the font's own
character map is asked for the same character ``check_fonts`` asks the server's fonts for. A
document in a script its theme's font does not both declare and draw gets no ``@font-face``
for that font, so the theme's ``font-family`` list falls through to the server's fonts --
the whole document in one of them, rather than a word in the theme's font beside a word in
the server's, and never a box.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as parse_xml
from django.utils.safestring import SafeString, mark_safe

from postulo.core import languages, pictures

logger = logging.getLogger(__name__)

#: What one file a theme inlines may weigh, as read from its package. A logo is kilobytes;
#: a Latin typeface as WOFF2 is tens of kilobytes, one covering Latin, Greek and Cyrillic a
#: few hundred.
MAX_ASSET_BYTES = 1024 * 1024

#: What all of one document's inlined files may weigh together, counted the same way, and
#: each time a file is written: a logo drawn twice costs twice. Written as base64, a third
#: larger, into every PDF drawn and every filed document's HTML.
MAX_DOCUMENT_BYTES = 3 * 1024 * 1024

#: The key the renderer hands the document's `Inliner` under. It begins with an underscore,
#: which the template language refuses as a variable name, so a template cannot reach it,
#: replace it or print it: only the tag reads it, from Python.
CONTEXT_KEY = "_theme_assets"


class Refused(ValueError):
    """A file a theme asked for that will not be written, and the reason.

    Not translated, like `plugins.logos.Unusable`: it reaches the log an administrator or a
    theme's author reads, and never a page somebody using Postulo sees -- the tag writes
    nothing instead.
    """


# ---------------------------------------------------------------- what a file is

#: Raster images, by the bytes they begin with.
_IMAGES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: Fonts, by theirs: the sfnt version of TrueType and OpenType, and the two WOFF signatures.
_FONTS = (
    (b"wOF2", "font/woff2"),
    (b"wOFF", "font/woff"),
    (b"\x00\x01\x00\x00", "font/ttf"),
    (b"true", "font/ttf"),
    (b"OTTO", "font/otf"),
)

#: The ``format()`` hint `@font-face` gives for each, so a renderer need not sniff.
_FONT_FORMATS = {
    "font/woff2": "woff2",
    "font/woff": "woff",
    "font/ttf": "truetype",
    "font/otf": "opentype",
}

SVG = "svg"
IMAGE = "image"
FONT = "font"


def kind_of(data: bytes) -> tuple[str, str]:
    """``(kind, media type)`` of a file, read from its content. Raises `Refused`.

    The name a file was given says nothing a reader can rely on; its first bytes are what
    the renderer will act on, so they are what is asked.
    """
    for signature, media_type in _IMAGES:
        if data.startswith(signature):
            return IMAGE, media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IMAGE, "image/webp"
    for signature, media_type in _FONTS:
        if data.startswith(signature):
            return FONT, media_type
    if pictures.looks_like_svg(data):
        return SVG, "image/svg+xml"
    raise Refused("not an SVG, a PNG, JPEG, WebP or GIF image, or a WOFF2, WOFF, TTF or OTF font")


# ---------------------------------------------------------- reading it, and only it


def _parts(name: str) -> list[str]:
    """The path's parts, or a refusal of a name that could point anywhere but down."""
    if not isinstance(name, str) or not name:
        raise Refused("no file named")
    if "\\" in name or ":" in name or "\x00" in name or name.startswith("/"):
        raise Refused(f"{name!r} is not a path inside the theme's package")
    parts = name.split("/")
    for part in parts:
        if not part or part.startswith("."):
            # `..`, `.`, an empty part from `a//b`, and a hidden file: none is a file a theme
            # ships to be drawn, and the first is the one this exists to refuse.
            raise Refused(f"{name!r} is not a path inside the theme's package")
    return parts


def read(package: str, name: str) -> bytes:
    """The file ``name`` from inside ``package``, within the ceiling. Raises `Refused`.

    ``name`` is a path relative to the package's own directory, with ``/`` between its
    parts. Each part is followed with `importlib.resources`, so a zipped package reads like
    one on disk. On disk, a symbolic link anywhere on the way is refused, and what the name
    finally resolves to has to be inside the package: either alone would do, and both are
    cheap.
    """
    parts = _parts(name)
    if not package:
        raise Refused("this theme has no package to read files from")
    try:
        root = resources.files(package)
    except (ModuleNotFoundError, TypeError, ValueError) as error:
        raise Refused(f"{package!r} is not a package files can be read from") from error

    node = root
    for part in parts:
        node = node.joinpath(part)
        if isinstance(node, Path) and node.is_symlink():
            raise Refused(f"{name!r} goes through a symbolic link")
    if isinstance(node, Path) and isinstance(root, Path):
        if not node.resolve().is_relative_to(root.resolve()):
            raise Refused(f"{name!r} is outside the theme's package")  # pragma: no cover
    try:
        if not node.is_file():
            raise Refused(f"{name!r} is not a file in {package}")
        if isinstance(node, Path) and node.stat().st_size > MAX_ASSET_BYTES:
            raise Refused(f"{name!r} is over the {MAX_ASSET_BYTES} bytes a theme's file may be")
        data = node.read_bytes()
    except OSError as error:
        raise Refused(f"{name!r} could not be read from {package}") from error
    if len(data) > MAX_ASSET_BYTES:
        raise Refused(f"{name!r} is over the {MAX_ASSET_BYTES} bytes a theme's file may be")
    return data


# ------------------------------------------------------------------- an SVG's styles

#: The CSS properties an SVG editor writes that SVG also takes as attributes, and that the
#: allowlist in `core.pictures` keeps. A declaration of anything else is dropped.
PRESENTATION_PROPERTIES = frozenset(
    {
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-miterlimit",
        "opacity",
        "color",
        "stop-color",
        "stop-opacity",
        "clip-path",
        "clip-rule",
        "mask",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "dominant-baseline",
        "letter-spacing",
    }
)

#: What a value written as an attribute may not hold: an escape, which is how a word CSS
#: would refuse is spelt so that it is not refused, and markup.
_UNSAFE_VALUE = re.compile(r"[\\<>]")


def _declarations(tokens) -> list[tuple[str, str]]:
    """The presentation declarations in a block, as ``(property, value)``, in order."""
    import tinycss2

    found = []
    for item in tinycss2.parse_blocks_contents(tokens, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration" or item.lower_name not in PRESENTATION_PROPERTIES:
            continue
        value = tinycss2.serialize(item.value).strip()
        if value and not _UNSAFE_VALUE.search(value):
            found.append((item.lower_name, value))
    return found


def _styles_to_attributes(root) -> None:
    """Turn the drawing's ``<style>`` rules and ``style=`` attributes into attributes.

    **Why not give the drawing's ``<style>`` the preview's nonce.** It would draw, and it
    would be the less safe of the two. A stylesheet inside an inline SVG is not the
    drawing's: it is the whole document's, so ``.cls-1`` in a logo restyles every element of
    the CV with that class, and a rule for ``body`` restyles the CV. It is also everything
    else a stylesheet can be -- ``@import``, ``@font-face``, ``url()`` -- handed the
    nonce that marks it as the theme's own. Presentation attributes are none of that: each
    one paints the element it is on and what that element contains, they are not CSS for
    the policy to refuse, and every renderer reads them. They are also the same in the PDF
    and in the preview, where a nonce exists in only one of them.

    So each rule's selectors are matched against the drawing (`cssselect2`, which WeasyPrint
    already brings) and its presentation declarations written onto the elements it
    matches, in cascade order -- specificity, then the order they were written in -- and an
    element's own ``style=`` last, as it wins in CSS. Everything else in a stylesheet is
    dropped: an at-rule, a pseudo-element, a property that is not a presentation attribute.
    What survives is then held to the allowlist like any other attribute, ``url(#…)`` alone
    among addresses.
    """
    import cssselect2
    import tinycss2

    sheets = [
        element.text or "" for element in root.iter() if pictures._local(element.tag) == "style"
    ]
    css = "\n".join(sheets)
    if css.strip():
        matcher = cssselect2.Matcher()
        for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
            if rule.type != "qualified-rule":
                continue  # an at-rule, or a parse error
            declarations = _declarations(rule.content)
            if not declarations:
                continue
            try:
                selectors = cssselect2.compile_selector_list(rule.prelude)
            except cssselect2.SelectorError:
                continue
            for selector in selectors:
                if selector.pseudo_element is None:
                    matcher.add_selector(selector, declarations)
        wrapper = cssselect2.ElementWrapper.from_xml_root(root)
        for element in wrapper.iter_subtree():
            for match in sorted(matcher.match(element), key=lambda one: one[:2]):
                for name, value in match[-1]:
                    element.etree_element.set(name, value)
    for element in root.iter():
        inline = element.attrib.pop("style", None)
        if inline:
            for name, value in _declarations(tinycss2.parse_component_value_list(inline)):
                element.set(name, value)


#: ``url(#id)`` inside an attribute value: the only address an allowed one may hold.
_FRAGMENT_URL = re.compile(r"url\(\s*(['\"]?)#([^'\")\s]+)\1\s*\)")


def _prefix_ids(root, prefix: str) -> None:
    """Give every ``id`` in the drawing a prefix of its own, and every reference to one.

    An inline SVG's identifiers are the document's: two logos exported from one editor
    both call their first gradient ``linearGradient1``, and the second would be painted
    with the first's. A prefix made from the file's name keeps two files apart, and the
    same file drawn twice still agrees with itself.
    """
    renamed = {}
    for element in root.iter():
        found = element.get("id")
        if found:
            renamed[found] = f"{prefix}{found}"
            element.set("id", renamed[found])
    if not renamed:
        return

    def fragment(match: re.Match) -> str:
        target = renamed.get(match.group(2), match.group(2))
        return f"url(#{target})"

    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name in pictures.REFERENCE_ATTRIBUTES and value.startswith("#"):
                element.set(name, "#" + renamed.get(value[1:], value[1:]))
            elif "url(" in value:
                element.set(name, _FRAGMENT_URL.sub(fragment, value))


def svg_markup(data: bytes, prefix: str) -> str:
    """An SVG file as markup to write into a document, sanitised. Raises `Refused`.

    Parsed with `defusedxml` (no document type, no entity), its styles turned into
    attributes, then held to the allowlist `core.pictures` keeps Postulo's own pictures
    to: what is not on it -- a script, an event handler, a ``foreignObject``, an
    ``image``, a reference with a scheme or a path -- is gone, subtree and all.
    """
    try:
        root = parse_xml(data, forbid_dtd=True)
    except Exception as error:
        raise Refused("not an SVG that could be read") from error
    if pictures._local(root.tag) != "svg":
        raise Refused("not an SVG that could be read")
    _styles_to_attributes(root)
    pictures._clean(root)
    _prefix_ids(root, prefix)
    ElementTree.register_namespace("", pictures.SVG_NS)
    ElementTree.register_namespace("xlink", pictures.XLINK_NS)
    return ElementTree.tostring(root, encoding="unicode")


# ----------------------------------------------------------------------- a font


@dataclass(frozen=True)
class Asset:
    """A file read and checked once, in the form a document will carry it."""

    kind: str
    media_type: str
    size: int
    #: The markup for an SVG; the ``data:`` address for an image or a font.
    written: str
    #: For a font: each declared script, and whether the font draws it.
    scripts: tuple[tuple[str, bool], ...] = ()
    #: For a font: whether its character map could be read at all.
    readable: bool = True


#: A family name the rule will carry: letters, digits, spaces, hyphens and underscores, so
#: that nothing in it can close the string, the rule or the `<style>` element.
_FAMILY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,62}")
_WEIGHT = re.compile(r"(normal|bold|[1-9]00)( [1-9]00)?")
_STYLES = frozenset({"normal", "italic", "oblique"})


def font_face(asset: Asset, *, family: str, weight: str, style: str) -> str:
    """An ``@font-face`` rule whose source is the font itself. Raises `Refused`.

    The family, weight and style come from the template and go into CSS, so each is held
    to a shape that cannot close the string, the rule or the element it is written in.
    """
    if not _FAMILY.fullmatch(family or ""):
        raise Refused(f"a font needs a family name of letters, digits and spaces: {family!r}")
    weight = str(weight or "normal")
    if not _WEIGHT.fullmatch(weight):
        raise Refused(f"{weight!r} is not a font weight")
    style = style or "normal"
    if style not in _STYLES:
        raise Refused(f"{style!r} is not a font style")
    return (
        f'@font-face {{ font-family: "{family}"; '
        f'src: url({asset.written}) format("{_FONT_FORMATS[asset.media_type]}"); '
        f"font-weight: {weight}; font-style: {style}; }}"
    )


def _probe(script: str) -> str:
    """The character a font is asked for to say it draws ``script``, or ``""``.

    The server's check's own table (`fonts.PROBES`), and a Latin letter for Latin, which
    that table leaves out because every font the server has draws it -- not true of a
    font a theme brings, which may be a Greek display face and nothing else.
    """
    from . import fonts

    if script == "Latn":
        return "a"
    probe = fonts.PROBES.get(script)
    return probe[1] if probe else ""


def drawn_scripts(data: bytes, scripts) -> dict[str, bool] | None:
    """For each script, whether this font's character map has its probe character.

    ``None`` when the font cannot be read at all: that is not the same as drawing nothing,
    and `check_fonts` says which. Read with fontTools, which WeasyPrint already brings and
    which reads all four formats -- WOFF2 included, through Brotli.
    """
    try:
        from fontTools.ttLib import TTFont

        with TTFont(io.BytesIO(data), lazy=True) as font:
            characters = font.getBestCmap() or {}
    except Exception:
        # Deliberately broad: a font that will not open is a font that draws nothing known,
        # whichever of a dozen errors a damaged table raises on the way.
        return None
    answer = {}
    for script in scripts:
        probe = _probe(script)
        answer[script] = bool(probe) and ord(probe) in characters
    return answer


# ------------------------------------------------------------- one asset, ready


@functools.lru_cache(maxsize=128)
def load(package: str, name: str, declared: tuple[str, ...] = ()) -> Asset:
    """Read, check and prepare one file. Raises `Refused`.

    Kept for the life of the process, as a plugin's logo is (`plugins.logos`): a package's
    files do not change while it is installed, and every document drawn in the theme would
    otherwise read, parse and encode the same files again.
    """
    data = read(package, name)
    kind, media_type = kind_of(data)
    if kind == SVG:
        digest = hashlib.sha256(f"{package}/{name}".encode()).hexdigest()[:8]
        return Asset(kind, media_type, len(data), svg_markup(data, f"t{digest}-"))
    encoded = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
    if kind == IMAGE:
        return Asset(kind, media_type, len(data), encoded)
    drawn = drawn_scripts(data, declared)
    return Asset(
        kind,
        media_type,
        len(data),
        encoded,
        tuple((script, bool(drawn and drawn.get(script))) for script in declared),
        readable=drawn is not None,
    )


def forget() -> None:
    """Drop what was read, so the next document reads its files afresh."""
    load.cache_clear()


# --------------------------------------------------------------- one document's


class Inliner:
    """What one document has inlined so far, and the theme it reads from.

    The renderer makes one per document and hands it to the template under `CONTEXT_KEY`;
    the tag writes through it. It is what knows whose package a name is in, and what keeps
    the document under `MAX_DOCUMENT_BYTES` however many times a template asks.
    """

    def __init__(self, theme) -> None:
        self.theme = theme
        self.spent = 0

    def write(
        self, name: str, *, family: str = "", weight: str = "normal", style: str = "normal"
    ) -> str:
        """The file as the document carries it. Raises `Refused`.

        ``""`` for a font the document's script is set without: one whose declaration or
        whose character map leaves that script out (#74).
        """
        package = getattr(self.theme, "package", "") or ""
        declarations = getattr(self.theme, "font_scripts", None) or {}
        declared = declarations.get(name)
        asset = load(package, name, tuple(declared or ()))
        if asset.kind == FONT:
            if declared is None:
                raise Refused(f"{name!r} is a font the theme's font_scripts does not declare")
            script = languages.script_of(languages.current())
            if not dict(asset.scripts).get(script, False):
                logger.debug(
                    "Theme font %r does not draw %s; the document is in the server's fonts",
                    name,
                    script,
                )
                return ""
            written = font_face(asset, family=family, weight=weight, style=style)
        else:
            written = asset.written
        if self.spent + asset.size > MAX_DOCUMENT_BYTES:
            raise Refused(
                f"{name!r} would take the document over the {MAX_DOCUMENT_BYTES} bytes of "
                "files a theme may inline into one"
            )
        self.spent += asset.size
        if asset.kind == IMAGE:
            return written
        # An SVG sanitised by `svg_markup`, or a rule built from parts checked by `font_face`.
        return mark_safe(written)  # noqa: S308


def write(context, name: str, **font) -> SafeString | str:
    """What the template tag writes: the file, or nothing and a line in the log."""
    inliner = context.get(CONTEXT_KEY) if context is not None else None
    if inliner is None:
        logger.warning("theme_asset %r was used outside a document Postulo renders", name)
        return ""
    try:
        return inliner.write(name, **font)
    except Refused as refused:
        logger.warning(
            "Theme %r: %s",
            getattr(inliner.theme, "name", "?"),
            refused,
        )
        return ""


# ------------------------------------------------------- what check_fonts reports (#74)


@dataclass(frozen=True)
class FontCheck:
    """One font a theme declares: which of its declared scripts it draws, or why not known."""

    theme: str
    name: str
    scripts: tuple[tuple[str, bool], ...] = ()
    problem: str = ""


def theme_fonts() -> list[FontCheck]:
    """Every font every installed theme declares, asked the question a render asks.

    Not WeasyPrint's question: a theme's font is not one fontconfig resolves, it is a file
    whose character map is read directly, so this answers on a machine without Pango too.
    """
    from . import themes

    checks = []
    for theme in themes.all_themes():
        for name, declared in sorted((theme.font_scripts or {}).items()):
            label = str(theme.label)
            try:
                asset = load(theme.package, name, tuple(declared))
            except Refused as refused:
                checks.append(FontCheck(label, name, problem=str(refused)))
                continue
            if asset.kind != FONT:
                checks.append(FontCheck(label, name, problem="not a font"))
            elif not asset.readable:
                checks.append(FontCheck(label, name, problem="its character map cannot be read"))
            else:
                checks.append(FontCheck(label, name, asset.scripts))
    return checks
