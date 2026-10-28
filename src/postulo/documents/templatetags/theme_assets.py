"""``{% load theme_assets %}``: a theme writes a file of its own package into its document.

The template half of the plugin surface for setting documents (#343). One tag:

``{% theme_asset "images/logo.svg" %}``
    an SVG as markup, sanitised;
``<img src="{% theme_asset "images/texture.png" %}" alt="">``
    a PNG, JPEG, WebP or GIF as a ``data:`` address;
``{% theme_asset "fonts/Vellum.woff2" family="Vellum" weight="400" style="normal" %}``
    a font as an ``@font-face`` rule, inside the theme's ``<style>``.

What the file is decides which, read from its bytes. Everything else -- whose package the
name is in, the ceilings, the scripts a font draws -- is `postulo.documents.theme_assets`.
A file that is refused writes nothing and leaves a line in the log, so a document with a
theme that has lost a file still draws.
"""

from __future__ import annotations

from django import template

from postulo.documents import theme_assets

register = template.Library()


@register.simple_tag(takes_context=True)
def theme_asset(context, name, family="", weight="normal", style="normal"):
    return theme_assets.write(context, name, family=family, weight=weight, style=style)
