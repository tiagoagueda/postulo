"""Small template helpers used across the interface."""

import functools
import re
import zlib
from pathlib import Path

from django import template
from django.forms import BoundField
from django.urls import reverse
from django.utils.html import escape, format_html
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


#: Backgrounds for the initials tile, chosen by name so two people look different. They
#: are spelled out here rather than built at runtime because the stylesheet is compiled
#: from what appears in the source, and a class assembled from pieces would never be found.
AVATAR_COLOURS = (
    # Each gives white text at least 5:1, which the two small letters need.
    "bg-brand-600",
    "bg-emerald-700",
    "bg-amber-700",
    "bg-rose-700",
    "bg-sky-700",
    "bg-violet-600",
    "bg-teal-700",
    "bg-orange-700",
)


def initials_for(user) -> str:
    """Two letters for the tile: first and last name, or what the display name offers."""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if first and last:
        return (first[0] + last[0]).upper()
    words = [word for word in (user.display_name or "").replace(".", " ").split() if word]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].upper()
    return "?"


@register.simple_tag
def company_identifier(company, column_key: str) -> str:
    """The cell of an identifier column: the value, linked where the scheme has a home."""
    identifier = company.identifier(column_key.removeprefix("id_"))
    if identifier is None:
        return "—"
    if identifier.url:
        return format_html(
            '<a href="{}" rel="noopener noreferrer external" target="_blank" '
            'class="text-brand-600 underline dark:text-brand-400">{}</a>',
            identifier.url,
            identifier.value,
        )
    return identifier.value


@register.simple_tag
def avatar(user, css_class: str = "size-7 text-xs") -> str:
    """An initials tile for ``user``, until a picture exists to show instead.

    Decorative: it always stands beside the person's name. The colour is a stable
    function of the display name, so it is the same on every page and every device.
    """
    profile = getattr(user, "profile", None) if getattr(user, "pk", None) else None
    picture = getattr(profile, "picture", None) if profile is not None else None
    if picture:
        url = reverse("accounts:avatar", args=[user.pk])
        return mark_safe(  # noqa: S308
            f'<img src="{url}?v={profile.picture_version}" alt="" '
            f'class="{escape(css_class)} shrink-0 rounded-full object-cover">'
        )
    name = user.display_name or ""
    colour = AVATAR_COLOURS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLOURS)]
    return mark_safe(  # noqa: S308
        f'<span class="{escape(css_class)} {colour} inline-flex shrink-0 select-none '
        'items-center justify-center rounded-full font-semibold text-white" '
        f'aria-hidden="true">{escape(initials_for(user))}</span>'
    )


@register.simple_tag
def company_logo(company, css_class: str = "size-6 text-[0.6rem]") -> str:
    """A company's logo, or an initials tile until there is one.

    Decorative: it always stands beside the company's name, so it carries no alternative
    text of its own. The image comes from this instance — never from the company's own
    server — which is what keeps every page free of a request that would tell somebody
    else who is looking at them.
    """
    if company is None:
        return ""
    if getattr(company, "logo", None):
        url = reverse("jobs:company_logo", args=[company.pk])
        stamp = int(company.logo_fetched_at.timestamp()) if company.logo_fetched_at else 0
        return mark_safe(  # noqa: S308
            f'<img src="{url}?v={stamp}" alt="" '
            f'class="{escape(css_class)} shrink-0 rounded object-contain">'
        )
    name = (company.name or "").strip()
    colour = AVATAR_COLOURS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLOURS)]
    letters = "".join(word[0] for word in name.replace(".", " ").split()[:2]).upper() or "?"
    return mark_safe(  # noqa: S308
        f'<span class="{escape(css_class)} {colour} inline-flex shrink-0 select-none '
        'items-center justify-center rounded font-semibold text-white" '
        f'aria-hidden="true">{escape(letters)}</span>'
    )


@register.filter
def add_class(field: BoundField, css_classes: str) -> BoundField:
    """Append CSS classes to a form widget.

    Django offers no way to add a class to a widget from a template, and forking
    every form definition to set ``attrs`` is worse than a four-line filter.
    """
    existing = field.field.widget.attrs.get("class", "")
    merged = f"{existing} {css_classes}".strip()
    return field.as_widget(attrs={"class": merged})


def _sidebar(context, sections) -> dict:
    request = context.get("request")
    return {
        "sections": [
            {"section": section, "active": request is not None and section.is_active(request)}
            for section in sections
        ]
    }


@register.inclusion_tag("settings/sidebar.html", takes_context=True)
def settings_sidebar(context) -> dict:
    """The sections of the Settings area, with the one being looked at marked."""
    from postulo.core.settings_sections import sections

    return _sidebar(context, sections())


@register.inclusion_tag("settings/sidebar.html", takes_context=True)
def server_sidebar(context) -> dict:
    """The sections of the Server settings area, for administrators."""
    from postulo.core.server_sections import SECTIONS

    return _sidebar(context, SECTIONS)


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names: str, css_class: str = "nav-link-active") -> str:
    """Return ``css_class`` when the current view matches one of ``url_names``."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "nav-link"
    current = f"{match.app_name}:{match.url_name}" if match.app_name else match.url_name
    return css_class if current in url_names else "nav-link"


@register.simple_tag(takes_context=True)
def nav_active_names(context, url_names, css_class: str = "nav-link-active") -> str:
    """``nav_active`` for a navigation item, which carries its names as a sequence."""
    return nav_active(context, *url_names, css_class=css_class)


@register.filter
def highlight(text, query: str) -> str:
    """Wrap every occurrence of ``query`` in ``text`` in a <mark>, escaping everything else.

    Case-insensitive, so the passage keeps its own capitals; what is marked is what was
    typed. The result is safe because both halves are escaped before being joined.
    """
    text = str(text or "")
    query = (query or "").strip()
    if not query:
        return escape(text)
    pieces = []
    lowered, needle, position = text.lower(), query.lower(), 0
    while True:
        found = lowered.find(needle, position)
        if found == -1:
            pieces.append(escape(text[position:]))
            break
        pieces.append(escape(text[position:found]))
        pieces.append(f"<mark>{escape(text[found : found + len(query)])}</mark>")
        position = found + len(query)
    return mark_safe("".join(pieces))  # noqa: S308
