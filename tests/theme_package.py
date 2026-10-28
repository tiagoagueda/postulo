"""A throwaway plugin package whose theme carries a font, an SVG and an image (#343).

Built in a temporary directory for each test that wants one, so nothing binary is kept in the
repository: the font is drawn by fontTools, the image by Pillow, the SVG written out here.
Shared by `tests/test_theme_assets.py` and the browser test of the preview.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

#: The family the test font calls itself, and the name a PDF embeds it under.
FAMILY = "Vellum Test"
POSTSCRIPT = "VellumTest-Regular"

#: What the test font draws: printable ASCII, so a CV's words are set in it.
CHARACTERS = "".join(chr(code) for code in range(0x21, 0x7F))

#: The test SVG: a gradient named by `url(#…)`, a `<use>` of a fragment, an editor's
#: `<style>` with a class rule, and an inline `style=`.
LOGO = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     viewBox="0 0 40 20" width="40" height="20" class="vellum-logo">
  <style>.ink { fill: #123456; stroke: #654321 } rect.ink { stroke-width: 2 }</style>
  <defs>
    <linearGradient id="g"><stop offset="0" stop-color="#fff"/><stop offset="1"
      stop-color="#000"/></linearGradient>
    <circle id="dot" r="3"/>
  </defs>
  <rect class="ink" width="20" height="20"/>
  <rect x="20" width="20" height="20" fill="url(#g)" style="opacity: 0.5"/>
  <use xlink:href="#dot" x="10" y="10"/>
</svg>
"""


def font_bytes(*, family: str = FAMILY, flavor: str | None = "woff2", characters=CHARACTERS):
    """A font that draws ``characters`` as boxes, as WOFF2 by default."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    names = {character: f"u{ord(character):04X}" for character in characters}
    order = [".notdef", "space", *names.values()]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({32: "space", **{ord(c): name for c, name in names.items()}})

    def box():
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0))
        pen.lineTo((50, 700))
        pen.lineTo((550, 700))
        pen.lineTo((550, 0))
        pen.closePath()
        return pen.glyph()

    def empty():
        return TTGlyphPen(None).glyph()

    builder.setupGlyf({name: (empty() if name == "space" else box()) for name in order})
    builder.setupHorizontalMetrics(dict.fromkeys(order, (600, 50)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "psName": family.replace(" ", "") + "-Regular",
        }
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    if flavor:
        builder.font.flavor = flavor
    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue()


def png_bytes(size=(4, 4), colour="teal") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


#: The theme's CV: the font in its `<style>`, the logo as markup, the image as an address.
CV_TEMPLATE = """{% load theme_assets %}<!doctype html>
<html lang="{{ document_language }}" dir="{{ document_direction|default:'ltr' }}">
<head><meta charset="utf-8"><title>{{ cv.name }}</title>
<style{% if csp_nonce %} nonce="{{ csp_nonce }}"{% endif %}>
{% theme_asset "fonts/Vellum.woff2" family="Vellum Test" weight="400" %}
body { font-family: "Vellum Test", sans-serif; }
</style></head>
<body><header>{% theme_asset "images/logo.svg" %}</header>
<img class="mark" src="{% theme_asset "images/mark.png" %}" alt="">
<p>{{ cv.name }}</p></body></html>
"""

PLUGIN = """from postulo.plugins.api import Theme, ThemeKind


class Vellum:
    name = "{module}"
    kind = "feature"
    themes = [
        Theme(
            name="{module}",
            label="Vellum",
            templates={{ThemeKind.CV: "{module}/cv.html"}},
            font_scripts={{"fonts/Vellum.woff2": ("Latn", "Grek")}},
            package="postulo",
        )
    ]
"""


def build(root: Path, module: str = "vellumassets") -> Path:
    """Write the package under ``root`` and put ``root`` on the import path.

    The theme declares Greek for a font that draws none, which is the declaration the
    server holds to the font's own character map. It also names `postulo` as its package,
    which registration replaces with its own.
    """
    package = root / module
    (package / "templates" / module).mkdir(parents=True)
    (package / "fonts").mkdir()
    (package / "images").mkdir()
    (package / "__init__.py").write_text(PLUGIN.format(module=module), encoding="utf-8")
    (package / "templates" / module / "cv.html").write_text(CV_TEMPLATE, encoding="utf-8")
    (package / "fonts" / "Vellum.woff2").write_bytes(font_bytes())
    (package / "images" / "logo.svg").write_text(LOGO, encoding="utf-8")
    (package / "images" / "mark.png").write_bytes(png_bytes())
    sys.path.insert(0, str(root))
    return package


def remove(root: Path, module: str = "vellumassets") -> None:
    """Undo `build`: the import path, the module, the theme, its templates and the cache."""
    from postulo.documents import theme_assets, themes
    from postulo.plugins import themes as plugin_themes

    if str(root) in sys.path:
        sys.path.remove(str(root))
    sys.modules.pop(module, None)
    themes.forget(module)
    templates = str((root / module / "templates").resolve())
    if templates in plugin_themes._registered:
        plugin_themes._registered.remove(templates)
        plugin_themes._forget_the_engines()
    theme_assets.forget()


def register(module: str = "vellumassets") -> None:
    import importlib

    from postulo.plugins.themes import register_plugin_themes

    plugin = importlib.import_module(module).Vellum
    assert register_plugin_themes(plugin, module) == 1
