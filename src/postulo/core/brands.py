"""The brand marks Postulo ships, and the one place that knows which (#654).

A mark is somebody else's logo, shown to say which service a link goes to and for nothing
else, under the rule in ``TRADEMARKS.md``. They are the Simple Icons files listed in
``assets/brands.txt``, copied by ``npm run sync:brands`` into ``static/brands/`` beside a
``NOTICE.txt`` that names each owner and the one mode it is drawn in: ``brand`` (the owner's
published colour) or ``single-colour`` (``currentColor``, where the owner's guidelines
allow it, which the stylesheet makes black on the light page and white on the dark one).
A service names a mark
by its slug; a slug Postulo does not ship draws the Lucide icon instead, which is the
rule's own neutral fallback.

Below the template tags and the link services, like ``core.flags``: both ask here.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

#: Where `npm run sync:brands` puts the marks listed in assets/brands.txt.
BRAND_DIR = Path(__file__).resolve().parents[1] / "static" / "brands"

BRAND_NAME = re.compile(r"[a-z0-9-]+")

BRAND = "brand"
SINGLE_COLOUR = "single-colour"


def brand_exists(name: str) -> bool:
    """Whether Postulo ships a mark of this name. The name is checked against the pattern
    before it becomes a path, so nothing a plugin or a template says can reach another
    file; and a name a plugin got wrong costs a picture, never a page."""
    return bool(name) and bool(BRAND_NAME.fullmatch(name)) and (BRAND_DIR / f"{name}.svg").is_file()


def brand_mode(name: str) -> str:
    """How the mark is drawn: ``brand``, or ``single-colour`` when `sync:brands` wrote it
    as ``currentColor``. The file is the record the page draws from, so the page cannot
    disagree with it."""
    from django import template

    if not brand_exists(name):
        raise template.TemplateSyntaxError(f"No brand mark named {name!r}.")
    root = re.search(r"<svg\b[^>]*>", (BRAND_DIR / f"{name}.svg").read_text(encoding="utf-8"))
    return SINGLE_COLOUR if root and 'fill="currentColor"' in root.group(0) else BRAND


@functools.cache
def brand_source(name: str) -> str:
    """The mark's SVG, trimmed to what the tag dresses up: its title (a mark is decorative
    unless it is given a label), its ``role`` and its fixed size. The ``fill`` the sync
    script wrote, which is the owner's published colour, is kept, and so is the viewBox."""
    from django import template

    if not brand_exists(name):
        raise template.TemplateSyntaxError(
            f"No brand mark named {name!r}. Add it to assets/brands.txt and run "
            "`npm run sync:brands`; a mark whose colour fails 3:1 on either page is not shipped."
        )
    source = (BRAND_DIR / f"{name}.svg").read_text(encoding="utf-8")
    source = re.sub(r"<title>.*?</title>", "", source, flags=re.S)
    source = re.sub(r"<svg\b[^>]*>", _root, source, count=1)
    return re.sub(r"\s+", " ", source).replace(" >", ">").replace("> <", "><").strip()


def _root(match: re.Match) -> str:
    return re.sub(r'\s+(?:role|width|height|class)="[^"]*"', "", match.group(0))
