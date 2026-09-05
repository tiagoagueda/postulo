"""Small template helpers used across the interface."""

import functools
import re
from pathlib import Path

from django import template
from django.forms import BoundField
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

#: Where `npm run sync:icons` puts the Lucide icons listed in assets/icons.txt.
ICON_DIR = Path(__file__).resolve().parents[2] / "static" / "icons"

_ICON_NAME = re.compile(r"[a-z0-9-]+")


@functools.cache
def _icon_source(name: str) -> str:
    """The icon's SVG, trimmed to what the tag will dress up.

    Lucide ships each icon with a licence comment, a default class and a fixed 24 pixel
    width and height. The comment is noise in a page, the class is replaced by the
    caller's, and the size has to come from CSS so that one file serves a 16 pixel
    inline glyph and a 48 pixel empty-state illustration alike. The viewBox stays, and
    with it the geometry.
    """
    path = ICON_DIR / f"{name}.svg"
    if not _ICON_NAME.fullmatch(name) or not path.is_file():
        raise template.TemplateSyntaxError(
            f"No icon named {name!r}. Add it to assets/icons.txt and run `npm run sync:icons`."
        )
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    source = re.sub(r'\s+(?:width|height|class)="[^"]*"', "", source)
    source = re.sub(r"\s+", " ", source).replace(" >", ">").replace("> <", "><").strip()
    return source


@register.simple_tag
def icon(name: str, label: str = "", **attrs: str) -> str:
    """Inline an icon: ``{% icon "sun" class="size-5" %}``.

    Decorative by default (``aria-hidden``), because an icon beside a word adds nothing
    a screen reader should repeat. Give it a ``label`` when it stands alone — an
    icon-only button — and it becomes an image with a name. Any other keyword becomes an
    attribute, which is how a switch marks which of its icons is which.
    """
    source = _icon_source(name)
    css_class = attrs.pop("class", "size-4")
    rendered = [f'class="{escape(css_class)} shrink-0"', f'data-icon="{escape(name)}"']
    if label:
        rendered.append(f'role="img" aria-label="{escape(label)}"')
    else:
        rendered.append('aria-hidden="true"')
    for key, value in attrs.items():
        rendered.append(f'{escape(key.replace("_", "-"))}="{escape(value)}"')
    return mark_safe(source.replace("<svg", "<svg " + " ".join(rendered), 1))  # noqa: S308


@register.filter
def add_class(field: BoundField, css_classes: str) -> BoundField:
    """Append CSS classes to a form widget.

    Django offers no way to add a class to a widget from a template, and forking
    every form definition to set ``attrs`` is worse than a four-line filter.
    """
    existing = field.field.widget.attrs.get("class", "")
    merged = f"{existing} {css_classes}".strip()
    return field.as_widget(attrs={"class": merged})


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names: str, css_class: str = "nav-link-active") -> str:
    """Return ``css_class`` when the current view matches one of ``url_names``."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "nav-link"
    current = f"{match.app_name}:{match.url_name}" if match.app_name else match.url_name
    return css_class if current in url_names else "nav-link"
