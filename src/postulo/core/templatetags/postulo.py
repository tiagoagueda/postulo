"""Small template helpers used across the interface."""

import functools
import json
import posixpath
import re
import secrets
import zlib
from pathlib import Path

from django import template
from django.forms import BoundField, Select, TextInput
from django.urls import reverse
from django.utils.formats import number_format
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

# The flag lookup lives in `core.flags`, below the forms that ask it directly (#248). This
# library registers it as the `flag_url` tag, and hands `FLAG_DIR` on to the flag tests.
from postulo.core.brands import BRAND_DIR, brand_source  # noqa: F401 - re-exported: BRAND_DIR
from postulo.core.flags import FLAG_DIR, flag_url  # noqa: F401 - re-exported: FLAG_DIR

register = template.Library()
register.simple_tag(flag_url)

#: Where `npm run sync:icons` puts the Lucide icons listed in assets/icons.txt.
ICON_DIR = Path(__file__).resolve().parents[2] / "static" / "icons"

_ICON_NAME = re.compile(r"[a-z0-9-]+")

#: The opening ``<svg ...>`` tag, whatever it is spread over. A negated class matches
#: newlines, which is what makes this work on Lucide's multi-line files.
_ROOT_SVG = re.compile(r"<svg\b[^>]*>")

#: What the root carries and the tag replaces.
_SIZE_OR_CLASS = re.compile(r'\s+(?:width|height|class)="[^"]*"')


def _without_size_and_class(match: re.Match) -> str:
    return _SIZE_OR_CLASS.sub("", match.group(0))


@functools.cache
def _icon_source(name: str) -> str:
    """The icon's SVG, trimmed to what the tag will dress up.

    Lucide ships each icon with a licence comment, a default class and a fixed 24 pixel
    width and height. The comment is noise in a page, the class is replaced by the
    caller's, and the size has to come from CSS so that one file serves a 16 pixel
    inline glyph and a 48 pixel empty-state illustration alike. The viewBox stays, and
    with it the geometry.

    **Only the root element is stripped.** Doing it to the whole file also took the width
    and height off any child that had them, and on a `<rect>` those are not a size but the
    shape itself: an envelope became a lone flap, a screen became a bare stand. Five icons
    were drawing wrongly — briefcase, calendar, layout-dashboard, mail and monitor — three
    of them in the settings sidebar, and had been since this was written.
    """
    path = ICON_DIR / f"{name}.svg"
    if not _ICON_NAME.fullmatch(name) or not path.is_file():
        raise template.TemplateSyntaxError(
            f"No icon named {name!r}. Add it to assets/icons.txt and run `npm run sync:icons`."
        )
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    source = re.sub(_ROOT_SVG, _without_size_and_class, source, count=1)
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


@register.simple_tag
def brand(name: str, label: str = "", **attrs: str) -> str:
    """Inline a brand mark: ``{% brand "mastodon" class="size-4" %}`` (#654).

    Beside ``{% icon %}`` and not in it, because a mark is somebody else's logo with its
    own file, notice and rule (``TRADEMARKS.md``), and the icon's selectors and tests read
    ``data-icon``. It is decorative by default, ``aria-hidden``, since the service's name
    is written beside it; give it a ``label`` where it stands alone. The mark is drawn in
    its owner's published colour and never recoloured, so there is no ``currentColor``.
    A name that is not a shipped mark, or is a path, is a template error.
    """
    source = brand_source(name)
    css_class = attrs.pop("class", "size-4")
    rendered = [f'class="{escape(css_class)} shrink-0"', f'data-brand="{escape(name)}"']
    if label:
        rendered.append(f'role="img" aria-label="{escape(label)}"')
    else:
        rendered.append('aria-hidden="true"')
    for key, value in attrs.items():
        rendered.append(f'{escape(key.replace("_", "-"))}="{escape(value)}"')
    return mark_safe(source.replace("<svg", "<svg " + " ".join(rendered), 1))  # noqa: S308


@register.simple_tag
def option_icons(names) -> str:
    """The icons a select's options name, for the script to copy: ``{% option_icons names %}``.

    An option says which icon it draws in ``data-icon``, and the control ``app.js`` builds
    beside a select copies that icon out of a ``<template data-option-icons>`` on the page
    (#301). A widget that knows its icons draws its own (`core.option_icons.OptionIcons`);
    this is for a select a template writes out, and is drawn once for however many selects
    share the icons -- the board's two hundred cards, one set of statuses. Nothing is drawn
    for no names, and a name Postulo does not ship is left out rather than raised on.
    """
    from postulo.core.option_icons import distinct

    drawn = [icon(name) for name in distinct(names or ())]
    if not drawn:
        return ""
    return mark_safe(f"<template data-option-icons>{''.join(drawn)}</template>")  # noqa: S308


@register.simple_tag
def language_flag(code: str, css_class: str = "flag", **attrs: str) -> str:
    """The flag that stands for a language: ``{% language_flag "pt-PT" %}``.

    ``flag`` takes a country; this takes a language code and asks `languages.flag_country`
    whose flag stands for it, which is nothing for a language with no single home -- and
    then draws nothing, as ``flag`` does for an unknown country (#208).
    """
    from postulo.core import languages

    return flag(languages.flag_country(code or ""), css_class, **attrs)


@register.simple_tag
def flag(country: str, css_class: str = "flag", **attrs: str) -> str:
    """A country's flag: ``{% flag "pt" %}``.

    An image rather than the two regional indicator characters that used to be here.
    Those cost no request and drew a flag on macOS, iOS, Android and most of Linux, and
    on Windows they drew ``PT``, because Segoe UI Emoji has never contained the pairs and
    Microsoft does not intend to add them (#88).

    Decorative by default, like ``{% icon %}`` and for the same reason: a flag next to
    "português (Portugal)" tells a screen reader nothing the words beside it do not
    already say, and "Portugal flag, português (Portugal)" is worse than silence. Pass a
    ``label`` where the flag stands alone and must speak for itself.
    """
    url = flag_url(country)
    if not url:
        return ""
    label = attrs.pop("label", "")
    rendered = [
        f'src="{escape(url)}"',
        f'class="{escape(css_class)}"',
        f'data-flag="{escape(country.strip().lower())}"',
        # 4:3, stated so the row does not reflow when the image arrives.
        'width="20"',
        'height="15"',
        'loading="lazy"',
        'decoding="async"',
        f'alt="{escape(label)}"' if label else 'alt="" aria-hidden="true"',
    ]
    for key, value in attrs.items():
        rendered.append(f'{escape(key.replace("_", "-"))}="{escape(value)}"')
    return mark_safe(f"<img {' '.join(rendered)}>")  # noqa: S308


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


def _tile(
    css_class: str, shape: str, *, picture: str = "", colour: str = "", letters: str = ""
) -> str:
    """Basecoat's `avatar` (#291): a box that holds a picture, or initials on a colour.

    One shape for a person, a company and a plugin, which were three copies of the same
    inline-flex tile. The size classes go on the box and the text-size ones on the initials
    inside it, because Basecoat sets a size of its own on the inner span and a utility on
    that span is what overrides it. The picture is decorative and the initials are hidden
    from assistive technology: each stands beside its name.
    """
    classes = css_class.split()
    box = " ".join(c for c in classes if not c.startswith("text-"))
    text = " ".join(c for c in classes if c.startswith("text-"))
    if picture:
        return mark_safe(  # noqa: S308
            f'<span class="avatar {escape(box)} {shape}"><img src="{picture}" alt=""></span>'
        )
    return mark_safe(  # noqa: S308
        f'<span class="avatar {escape(box)} {colour} {shape} font-semibold text-white" '
        f'aria-hidden="true"><span class="{escape(text)}">{escape(letters)}</span></span>'
    )


@register.simple_tag
def avatar(user, css_class: str = "size-7 text-xs") -> str:
    """An initials tile for ``user``, until a picture exists to show instead.

    Decorative: the picture and the initials are both hidden from assistive
    technology, and the caller provides the name -- the person's name beside it, or
    the control's own ``aria-label`` where it stands alone as the masthead's account
    button (#282). The colour is a stable function of the display name, so it is the
    same on every page and every device.
    """
    profile = getattr(user, "profile", None) if getattr(user, "pk", None) else None
    picture = getattr(profile, "picture", None) if profile is not None else None
    if picture:
        url = reverse("accounts:avatar", args=[user.pk])
        return _tile(css_class, "rounded-full", picture=f"{url}?v={profile.picture_version}")
    name = user.display_name or ""
    colour = AVATAR_COLOURS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLOURS)]
    return _tile(css_class, "rounded-full", colour=colour, letters=initials_for(user))


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
        return _tile(css_class, "rounded", picture=f"{url}?v={stamp}")
    name = (company.name or "").strip()
    colour = AVATAR_COLOURS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLOURS)]
    letters = "".join(word[0] for word in name.replace(".", " ").split()[:2]).upper() or "?"
    return _tile(css_class, "rounded", colour=colour, letters=letters)


@register.simple_tag
def plugin_logo(plugin, css_class: str = "size-6 text-[0.6rem]") -> str:
    """A plugin's logo, or an initials tile until it ships one (#106).

    Decorative: it always stands beside the plugin's name, so it carries no alternative
    text of its own. The image comes from this instance, never from whoever wrote the
    plugin -- an `<img>` at their server would tell them which instances run their code.

    A plugin with no logo is the normal case rather than a failure, so this asks whether
    there is one to show instead of assuming there is and rendering a broken image.
    """
    if plugin is None:
        return ""
    name = getattr(plugin, "name", "") or ""
    label = str(getattr(plugin, "label", "") or name)
    from postulo.plugins import logos

    if logos.logo_for(plugin) is not None:
        url = reverse("connections:logo", args=[name])
        return _tile(css_class, "rounded", picture=url)
    colour = AVATAR_COLOURS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLOURS)]
    letters = "".join(word[0] for word in label.replace(".", " ").split()[:2]).upper() or "?"
    return _tile(css_class, "rounded", colour=colour, letters=letters)


@register.simple_tag
def unique_id(prefix: str) -> str:
    """An id for an element that another element points at, unique on whatever page it lands.

    Random rather than counted (#310). A counter starts again with every request, and a
    fragment htmx swaps into a page is a second request: its first menu and the header's
    first menu would both be `menu-1`, and `popovertarget` opens whichever of the two comes
    first in the document. Forty-eight random bits make a clash on one page a practical
    impossibility, and nothing has to know what else is on the page.
    """
    return f"{prefix}-{secrets.token_hex(6)}"


@register.simple_tag
def help_topic(slug: str):
    """The help topic called ``slug``: ``{% help_topic "your-picture" as about %}`` (#302).

    A name that is not a topic is a mistake in the template, and says so where it is drawn
    rather than drawing a question mark that leads nowhere.
    """
    from postulo.core import help as help_topics

    try:
        return help_topics.topic(slug)
    except KeyError:
        raise template.TemplateSyntaxError(
            f"No help topic named {slug!r}. The topics are listed in postulo/core/help.py."
        ) from None


@register.simple_tag
def question_mark_turned() -> bool:
    """Whether the page's language writes a question mark turned round (#302).

    The Arabic script does -- ؟ -- and that is a property of the script, not of the
    direction: Hebrew is read right to left too and writes it as Latin does. So it is asked
    of the script `core.languages` knows the page's language to be written in, which is
    the one list of that there is, and the stylesheet turns the help icon where a template
    says so.
    """
    from postulo.core import languages

    return languages.script_of(languages.current()) == "Arab"


@register.simple_tag(takes_context=True)
def help_body(context, topic) -> str:
    """A topic's help itself: ``{% help_body about %}`` (#302).

    The drawer on the card and the page at the topic's own address both draw it through
    here, with the same template and the same variables, so the two cannot disagree.

    Drawn in the context it is called from, as an ``{% include %}`` is, and not as a new
    page: a card's help is nine drawers on *Your details*, and a fresh request context for
    each would run every context processor nine times more.
    """
    template_ = context.template.engine.get_template(topic.template)
    with context.push(topic.variables()):
        return template_.render(context)


@register.simple_tag
def phone_link(number: str) -> str:
    """A stored number, spaced to be read aloud and linked so a phone can dial it.

    Half of these calls happen on a phone, where a number that is only text has to be
    copied out by hand. The `tel:` form is the digits; the visible form is grouped.

    Written left to right whatever the page is (#304). Each group of digits is a number to
    the bidirectional algorithm, and a right-to-left line lays numbers out from the right:
    `+33 6 98 76 54 32` drew as `32 54 76 98 6 33+` in Arabic and Hebrew.
    """
    from postulo.core import phones

    number = (number or "").strip()
    if not number:
        return ""
    dialled = phones.as_dialled(number)
    if not dialled:
        return escape(number)
    return format_html(
        '<a href="tel:{}" dir="ltr" class="text-brand-600 underline dark:text-brand-400">{}</a>',
        dialled,
        phones.readable(number),
    )


@register.filter
def get_item(mapping, key):
    """One value out of a dictionary, by a key held in a variable.

    Django's template language can look up a constant key and not a variable one, and the
    alternative is building the same dictionary again in the view with the field names
    already resolved. One filter is less code and stays true when a field is added.
    """
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None


@register.filter
def named(label, what) -> str:
    """A declared label with its row filled in: ``label % {"what": what}``.

    A column that can be edited where it sits declares what its pencil is called --
    *Rename %(what)s* -- and the row is only known when the cell is drawn, so the two meet
    here rather than in a view that the first render never passes through (#252).
    """
    return str(label) % {"what": what}


@register.filter
def add_class(field: BoundField, css_classes: str) -> BoundField:
    """Append CSS classes to a form widget.

    Django offers no way to add a class to a widget from a template, and forking
    every form definition to set ``attrs`` is worse than a four-line filter.
    """
    existing = field.field.widget.attrs.get("class", "")
    merged = f"{existing} {css_classes}".strip()
    return field.as_widget(attrs={"class": merged})


@register.filter
def widgets_classed(field: BoundField, css_classes: str) -> BoundField:
    """The same field, with a class on every input its widget renders.

    ``add_class`` renders the field; this one hands it back, for the templates that
    iterate a choice widget's subwidgets (``{% for choice in form.theme %}``) and draw each
    ``choice.tag`` inside a label of their own. A radio or a checkbox is drawn by the
    stylesheet only when it says ``class="input"`` -- Basecoat's structure is keyed on that
    or on being inside a ``.field``, and a tag drawn in a loop is neither (#290). The widget
    is the form's own copy, so the attribute reaches nothing but this render.
    """
    attrs = field.field.widget.attrs
    attrs["class"] = f"{attrs.get('class', '')} {css_classes}".strip()
    return field


@register.filter
def file_name(value) -> str:
    """The name of a stored file, without the path Postulo keeps it at.

    A ``FieldFile`` prints as its storage name -- ``documents/1/2026/09/reference.txt`` --
    which carries the owner's account id and the month of the upload. Neither is the
    person's to care about, and a form that shows them is describing the filing system
    rather than answering "which file is this?" (#191).
    """
    return posixpath.basename(str(value or ""))


@register.inclusion_tag("partials/field_pinned.html")
def pinned_field(field: BoundField, variable: str) -> dict:
    """A field the environment sets: shown, readable, copyable, and not editable.

    ``readonly`` rather than ``disabled``. A disabled input leaves the tab order and is
    announced inconsistently by screen readers, so a value an administrator may well need
    to read and copy -- the SMTP host they are about to check against their relay's
    configuration -- would become one some of them cannot reach at all.

    A ``<select>`` has no readonly attribute. Marking one readonly does nothing: the
    browser lets you change it and the server then refuses, silently, which is the worst of
    both. So a pinned choice is rendered as a readonly text box holding the label it
    resolves to -- still a labelled control in the tab order, simply one whose value is
    settled somewhere else.

    None of this is the boundary. The form drops pinned fields whatever arrives for them;
    this is what an honest page looks like, not what stops a hand-written POST.
    """
    described_by = f"pinned-{field.name}"
    attrs = {"readonly": True, "aria-describedby": described_by}
    if isinstance(field.field.widget, Select):
        value = field.value()
        shown = dict(field.field.widget.choices).get(value, value)
        control = TextInput().render(
            field.html_name, str(shown), attrs={**attrs, "id": field.auto_id}
        )
    else:
        control = field.as_widget(attrs=attrs)
    return {"field": field, "variable": variable, "control": control, "described_by": described_by}


def _sidebar(context, sections) -> dict:
    """The pages of an area as sidebar entries, the one being looked at marked `page`."""
    request = context.get("request")
    return {
        "label": _("Settings sections"),
        "entries": [
            {
                "href": reverse(section.url_name),
                "label": section.label,
                "icon": section.icon,
                "count": None,
                "anchor": "",
                "current": "page" if request is not None and section.is_active(request) else "",
            }
            for section in sections
        ],
    }


@register.inclusion_tag("partials/sidebar.html", takes_context=True)
def settings_sidebar(context) -> dict:
    """The sections of the Settings area, with the one being looked at marked."""
    from postulo.core.settings_sections import sections

    return _sidebar(context, sections())


@register.inclusion_tag("partials/sidebar.html", takes_context=True)
def server_sidebar(context) -> dict:
    """The sections of the Server settings area, for administrators."""
    from postulo.core.server_sections import SECTIONS

    return _sidebar(context, SECTIONS)


@register.inclusion_tag("partials/sidebar.html")
def sidebar(entries, label) -> dict:
    """The same sidebar, for entries a page built itself -- its own sections, as anchors.

    Settings navigates between pages and Your career within one; the list is the same
    shape either way, and one template means the two cannot drift apart (#175).
    """
    return {"entries": entries, "label": label}


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


@register.simple_tag(takes_context=True)
def account_menu_active(context) -> str:
    """The account menu's trigger is current on the pages of either of its rows (#676)."""
    from postulo.core import navigation

    return nav_active(context, *navigation.ACCOUNT_NAMES)


@register.simple_tag(takes_context=True)
def account_row_current(context, row: str) -> str:
    """`aria-current="page"` on the account menu row whose pages these are (#676)."""
    from postulo.core import navigation

    names = navigation.ACCOUNT_ROWS[row]
    if nav_active(context, *names, css_class="current") == "current":
        return mark_safe(' aria-current="page"')
    return ""


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
    position = 0
    # Match on the text itself: lower-casing "İ" gives two code points, which shifts
    # every position after it (#377).
    for found in re.finditer(re.escape(query), text, re.IGNORECASE):
        pieces.append(escape(text[position : found.start()]))
        pieces.append(f"<mark>{escape(found.group())}</mark>")
        position = found.end()
    pieces.append(escape(text[position:]))
    return mark_safe("".join(pieces))  # noqa: S308


@register.simple_tag(takes_context=True)
def render_widget(context, rendered):
    """Draw one dashboard widget with the context it computed for itself.

    ``{% include %}`` would hand the widget the whole page's context, which is how one
    widget ends up quietly reading a variable another one set. Each is rendered against
    its own dict and the request, so a widget can only see what it asked for.
    """
    from django.template.loader import render_to_string

    return render_to_string(
        rendered.spec.template,
        {**rendered.context, "widget": rendered.spec},
        request=context.get("request"),
    )


@register.filter
def percent(value) -> str:
    """A share written as the reader's language writes one.

    English puts the sign against the number, French puts a space before it and Turkish
    puts it in front -- %42. Hard-coding ``{{ share }}%`` in the template settles that
    question for every language at once, and settles it wrongly for two of them (#225), so
    the whole thing is one string the catalogue can rearrange. The number arrives already
    formatted, usually by ``floatformat``, which localises the decimal mark itself.
    """
    return gettext("%(share)s%%") % {"share": value}


@register.filter
def grouped(value) -> str:
    """A whole number with its thousands grouped as the reader's language groups them.

    1,873 in English, 1 873 in French and 1.873 in Portuguese. Django groups only when
    ``USE_THOUSAND_SEPARATOR`` is on, which would regroup every number on every page -- a
    year, a port, a postcode typed as digits -- so a count that is read as a quantity asks
    for it here instead, as the salary and the money already do (#312).
    """
    return number_format(value, use_l10n=True, force_grouping=True)


@register.simple_tag
def ticked_counts(rows) -> str:
    """Every sentence the bulk bar can need, as JSON, indexed by how many rows are ticked.

    The bar counts ticks in the browser, and the browser has no plural rules: it used to
    choose between one string and another with ``n === 1``, which is right for English and
    wrong for Polish, Ukrainian, Irish, Welsh and nine more (#225). Nothing portable fixes
    that in JavaScript, but nothing has to. A page holds a bounded number of rows, so the
    server can write out the answer for each count in advance and let the script index it.

    Index 0 is empty: no ticks is a different sentence, and the bar keeps its own.
    """
    try:
        upper = len(rows)
    except TypeError:
        upper = int(rows or 0)
    counts = [""]
    for number in range(1, max(upper, 0) + 1):
        counts.append(
            ngettext("One row ticked.", "%(count)s rows ticked.", number)
            % {"count": number_format(number)}
        )
    return json.dumps(counts, ensure_ascii=False)
