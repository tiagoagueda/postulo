"""Pulling structure and text out of a page.

Built on the standard library's HTML parser rather than BeautifulSoup and lxml. The main
path here reads JSON-LD out of a ``<script>`` element, which needs no cleverness, and the
fallback only has to produce text a person is about to read and edit. Neither justifies a
C extension in a project somebody has to install on their own server.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

#: Content inside these is never part of an advert.
IGNORED_CONTENT_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})

#: Rendering these as a line break keeps paragraphs and list items apart in the text.
BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "aside", "nav",
        "ul", "ol", "li", "br", "hr", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)  # fmt: skip


class _JSONLDCollector(HTMLParser):
    """Collect the contents of every ``application/ld+json`` script element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = {name.lower(): (value or "").lower() for name, value in attrs}
        if "ld+json" in attributes.get("type", ""):
            self._capturing = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capturing:
            self.blocks.append("".join(self._buffer))
            self._capturing = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)


def _flatten(node) -> list[dict]:
    """Walk the shapes JSON-LD is allowed to take, and yield the objects inside.

    A document may be one object, a list of them, or an ``@graph`` holding either, and
    real sites use all three.
    """
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_flatten(item))
    elif isinstance(node, dict):
        found.append(node)
        graph = node.get("@graph")
        if graph is not None:
            found.extend(_flatten(graph))
    return found


def extract_jsonld(html: str) -> list[dict]:
    """Return every JSON-LD object in the page.

    A block that will not parse is skipped rather than failing the capture: pages
    routinely carry several, and one being malformed says nothing about the others.
    """
    collector = _JSONLDCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        # A parser error costs us the structured data, not the whole capture. Logged
        # rather than silenced: it is the first thing worth looking at when a site that
        # used to capture cleanly stops doing so.
        logger.warning("Could not parse the page while looking for JSON-LD", exc_info=True)

    objects: list[dict] = []
    for block in collector.blocks:
        try:
            objects.extend(_flatten(json.loads(block)))
        except (ValueError, TypeError):
            continue
    return objects


class _MetaAndTitleCollector(HTMLParser):
    """Collect ``<title>`` and the meta tags worth having."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        key = attributes.get("property") or attributes.get("name")
        content = attributes.get("content", "")
        if key and content:
            self.meta.setdefault(key.lower(), content)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()


def extract_meta(html: str) -> dict[str, str]:
    """Return ``{"title": ..., "og:title": ..., ...}`` for what the page declares."""
    collector = _MetaAndTitleCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        logger.warning("Could not parse the page while reading its metadata", exc_info=True)
    # Whatever was collected before the parser gave up is still worth having.
    return {"title": collector.title, **collector.meta}


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in IGNORED_CONTENT_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in IGNORED_CONTENT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Flatten a page to readable text, keeping paragraph breaks.

    This is the fallback for pages carrying no structured data, and its output goes
    straight into a form for somebody to read and correct. It aims to be tidy enough to
    edit, not to be a faithful rendering.
    """
    collector = _TextCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        logger.warning("Could not parse the page while extracting text", exc_info=True)

    text = "".join(collector.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_tags(value: str) -> str:
    """Remove markup from a fragment, for descriptions delivered as HTML inside JSON."""
    return html_to_text(unescape(value or ""))
