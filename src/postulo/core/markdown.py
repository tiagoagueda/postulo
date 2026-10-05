"""Basic Markdown, rendered on the server and sanitised twice (#665).

A listing's description may be written in Markdown, and the page it is shown on runs under a
policy that allows no inline script, so the rendering is ours. The module is the one place
that turns Markdown into markup: ``render()`` for the page that shows it as markup,
``plain()`` for every other place (a table cell, a search excerpt) that wants the words
without the markers.

Two halves of one defence, as with a picture (`core/pictures.py`):

1. The parser (markdown-it-py) is built from the ``zero`` preset with raw HTML off, images
   off, bare addresses left alone, and only the rules below turned on. What it is not told
   to read it writes out as text.
2. The allowlist (``nh3``) runs over what the parser wrote. With raw HTML off it should
   never change anything; it is there for the day somebody turns ``html`` on or adds a
   plugin, and it forces what every link carries.

The content security policy is the third layer, and is not relied on.
"""

from __future__ import annotations

import nh3
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

#: The rules that are on, and nothing else: paragraphs, lists, block quotes, headings and
#: reference definitions as blocks; escapes, entities, line breaks, emphasis, inline code,
#: links and ``<https://…>`` as spans. Not on: raw HTML, images, tables, task lists,
#: footnotes, fenced and indented code, autolinking of a bare address. A test asserts it.
BLOCK_RULES = ("paragraph", "list", "blockquote", "heading", "reference")
INLINE_RULES = ("escape", "entity", "newline", "emphasis", "backticks", "link", "autolink")

#: Headings are shifted down so they cannot compete with the page's own: ``#`` is an
#: ``<h3>``, and nothing is above ``<h6>``.
HEADING_SHIFT = 2
LOWEST_HEADING = 6

ALLOWED_TAGS = frozenset(
    {"p", "br", "strong", "em", "code", "ul", "ol", "li", "blockquote", "h3", "h4", "h5", "h6", "a"}
)
ALLOWED_ATTRIBUTES = {"a": {"href"}, "ol": {"start"}}
ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})
#: As the page's own "Original posting" link has it.
LINK_REL = "noopener noreferrer external"
FORCED_ATTRIBUTES = {"a": {"target": "_blank"}}


def parser() -> MarkdownIt:
    md = MarkdownIt("zero", {"html": False, "linkify": False, "breaks": True})
    md.enable([*BLOCK_RULES, *INLINE_RULES])
    return md


_MD = parser()


def _shift_headings(tokens) -> None:
    """``#`` becomes ``<h3>``; a text whose shallowest heading is ``##`` starts at ``<h3>``
    too, so a section never opens with a level the page skipped to reach."""
    headings = [t for t in tokens if t.type in ("heading_open", "heading_close")]
    if not headings:
        return
    shallowest = min(int(t.tag[1:]) for t in headings)
    for token in headings:
        level = int(token.tag[1:]) - shallowest + 1
        token.tag = f"h{min(level + HEADING_SHIFT, LOWEST_HEADING)}"


def _only_listed(tag: str, attribute: str, value: str) -> str | None:
    # The forced ones pass through the filter too, and are what the library writes over.
    forced = {"rel", *FORCED_ATTRIBUTES.get(tag, {})} if tag == "a" else set()
    return value if attribute in ALLOWED_ATTRIBUTES.get(tag, set()) | forced else None


def sanitize(html: str) -> str:
    """The allowlist over already-rendered markup."""
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes={tag: set(names) for tag, names in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_SCHEMES),
        # An address with no scheme would be this site's own, or a scheme a browser reads
        # through an encoding: neither is an advert's to link to.
        url_relative="deny",
        # The library's generic attributes (``title``, ``lang``) are not on the list.
        attribute_filter=_only_listed,
        link_rel=LINK_REL,
        set_tag_attribute_values={tag: dict(values) for tag, values in FORCED_ATTRIBUTES.items()},
        strip_comments=True,
    )


def render(text: str) -> SafeString:
    """Markdown as markup: safe to print, and the only thing here that is."""
    tokens = _MD.parse(text or "")
    _shift_headings(tokens)
    html = _MD.renderer.render(tokens, _MD.options, {})
    return mark_safe(sanitize(html))  # noqa: S308 -- ``sanitize`` is the allowlist above


_BLOCK_ENDS = {"paragraph_close", "heading_close", "list_item_close", "blockquote_close"}


def plain(text: str) -> str:
    """The same parse, as words: no markers, a line for each paragraph and list item."""
    lines: list[str] = []
    current: list[str] = []
    for token in _MD.parse(text or ""):
        if token.type == "inline":
            for child in token.children or ():
                if child.type in ("text", "code_inline"):
                    current.append(child.content)
                elif child.type in ("softbreak", "hardbreak"):
                    current.append(" ")
        elif token.type in _BLOCK_ENDS:
            line = "".join(current).strip()
            if line:
                lines.append(line)
            current = []
    tail = "".join(current).strip()
    if tail:
        lines.append(tail)
    return "\n".join(lines)
