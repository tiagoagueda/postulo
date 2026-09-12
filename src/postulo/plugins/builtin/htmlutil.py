"""Pulling structure and text out of a page.

Built on the standard library's HTML parser rather than BeautifulSoup and lxml. Neither
justifies a C extension in a project somebody has to install on their own server.

What the parser gives is a stream of tags, and three of the things read here -- microdata,
RDFa and the readable part of a page -- are questions about *nesting*: which item holds
this property, is this paragraph inside the navigation. So the stream is assembled into a
small tree first, a few dozen lines of it, and everything else is a walk over that tree.

That is also what keeps this honest against the browser extension, which reads the same
pages through a real DOM (`postulo-chromium/src/lib/parse.js`). Function for function the
two are the same walk, so a page read in somebody's browser is the page read here when it
arrives. Changing one without the other is how they drift.
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

#: One tag with no end, so one break rather than two.
VOID_BLOCK_TAGS = frozenset({"br", "hr"})

#: Tags HTML closes for you, which never hold anything.
VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)  # fmt: skip

#: Landmarks that surround an advert without ever being part of one.
#:
#: The controls are here and the ``<form>`` around them is not, deliberately: an advert is
#: never written inside a button, but whole pages are still served wrapped in one big form
#: -- every ASP.NET board is -- and dropping those would leave the description empty.
CHROME_TAGS = frozenset(
    {"nav", "header", "footer", "aside", "button", "input", "select", "textarea", "option"}
)

#: The same, where a page states its shape with a role instead of with a tag.
CHROME_ROLES = frozenset(
    {
        "navigation", "banner", "contentinfo", "complementary",
        "search", "menu", "menubar", "dialog", "alertdialog",
    }
)  # fmt: skip

#: The attribute a property element carries its value in, per the microdata specification.
VALUE_ATTRIBUTE = {
    "meta": "content",
    "audio": "src",
    "embed": "src",
    "iframe": "src",
    "img": "src",
    "source": "src",
    "track": "src",
    "video": "src",
    "a": "href",
    "area": "href",
    "link": "href",
    "object": "data",
    "data": "value",
    "meter": "value",
    "time": "datetime",
}


# ----------------------------------------------------------------- a very small tree


class Element:
    """One tag, its attributes and what is inside it. Text is a child that is a ``str``."""

    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Element | None = None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Element | str] = []
        self.parent = parent

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def has(self, name: str) -> bool:
        return name in self.attrs

    def iter(self):
        """Every element beneath this one, in document order."""
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.iter()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<{self.tag} {self.attrs}>"


class _TreeBuilder(HTMLParser):
    """Assemble the tag stream into a tree, forgiving the things real pages do.

    An end tag with nothing open to match it is dropped, and one that matches something
    further up closes everything between -- which is what a browser does, and what makes an
    unclosed ``<p>`` cost a paragraph break rather than the rest of the page.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("", {})
        self._open: list[Element] = [self.root]

    @property
    def _here(self) -> Element:
        return self._open[-1]

    def handle_starttag(self, tag: str, attrs) -> None:
        element = Element(
            tag,
            {name.lower(): (value or "") for name, value in attrs},
            self._here,
        )
        self._here.children.append(element)
        if tag not in VOID_TAGS:
            self._open.append(element)

    def handle_startendtag(self, tag: str, attrs) -> None:
        element = Element(
            tag,
            {name.lower(): (value or "") for name, value in attrs},
            self._here,
        )
        self._here.children.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        self._here.children.append(data)


def parse_html(html: str) -> Element:
    """The page as a tree. Whatever was built before a parser error is still worth having."""
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:
        logger.warning("Could not finish parsing the page", exc_info=True)
    return builder.root


def find(root: Element, tag: str):
    """Every element of one tag, in document order."""
    return [element for element in root.iter() if element.tag == tag]


def body_of(root: Element) -> Element | None:
    found = find(root, "body")
    return found[0] if found else None


def classes_of(element: Element) -> set[str]:
    return set(element.get("class").split())


def by_class(root: Element, *names: str, without: str = "") -> list[Element]:
    """Elements carrying all of ``names``, and not ``without``. In document order.

    Enough of a selector for a board recipe, and deliberately no more: a recipe names the
    classes a board puts on the thing it wants, and nothing here needs descendant
    combinators or attribute operators to do that.
    """
    wanted = set(names)
    return [
        element
        for element in root.iter()
        if wanted <= classes_of(element) and (not without or without not in classes_of(element))
    ]


def first_by_class(root: Element, *names: str, without: str = "") -> Element | None:
    found = by_class(root, *names, without=without)
    return found[0] if found else None


# ----------------------------------------------------------------- readable text


def text_of(node: Element, drop=None) -> str:
    """A node's readable text, with paragraph breaks kept: tidy enough to edit, no more.

    ``drop`` names elements to leave out, for the fallback that has a whole page to read.
    """
    parts: list[str] = []

    def walk(parent: Element) -> None:
        for child in parent.children:
            if isinstance(child, str):
                parts.append(child)
                continue
            if child.tag in IGNORED_CONTENT_TAGS or (drop is not None and drop(child)):
                continue
            block = child.tag in BLOCK_TAGS
            if block:
                parts.append("\n")
            walk(child)
            if block and child.tag not in VOID_BLOCK_TAGS:
                parts.append("\n")

    walk(node)
    text = "".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_chrome(element: Element) -> bool:
    """True for an element that is page furniture rather than the advert.

    Landmarks only: what a page declares about its own shape, never a guess at a class
    name. A board that marks nothing loses nothing -- it is read exactly as it was before.
    """
    if element.get("aria-hidden") == "true" or element.has("hidden"):
        return True
    # An explicit role overrides the tag: <nav role="main"> means what it says.
    role = element.get("role").strip().lower()
    if role:
        return role in CHROME_ROLES
    return element.tag in CHROME_TAGS


def html_to_text(html: str) -> str:
    """Flatten a page to readable text, keeping paragraph breaks.

    Everything the page says, furniture included. `main_text` is what the fallback reads;
    this stays as it was for a fragment, which has no furniture to leave out.
    """
    return text_of(parse_html(html))


#: A block is a list of links rather than prose once it is this much link, and this many.
FARM_LINKS = 6
FARM_SHARE = 0.7
FARM_FLOOR = 100

#: The containers a run of links is gathered into.
FARM_TAGS = frozenset({"section", "div", "ul", "ol", "aside", "nav"})


def _text_length(node: Element) -> int:
    """How much text is under a node, whitespace collapsed."""
    parts: list[str] = []

    def walk(parent: Element) -> None:
        for child in parent.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                walk(child)

    walk(node)
    return len(re.sub(r"\s+", " ", "".join(parts)).strip())


def _link_farms(body: Element) -> set[int]:
    """Blocks that are a list of links rather than something written, by object identity.

    "Similar searches", "people also viewed", "explore top content": every large board ends
    an advert with thousands of characters of them, and marks them with a class name and
    nothing else -- LinkedIn's are plain ``<section>``s, so no landmark rule reaches them.

    What does reach them is what they are: text that is almost entirely inside links, a lot
    of them. That is a measurement rather than a guess at a board's markup, it is the same
    measurement every reading-mode extractor makes, and prose does not look like it. The
    floor keeps it off a short run of links inside a real paragraph.
    """
    farms: set[int] = set()
    for element in body.iter():
        if element.tag not in FARM_TAGS:
            continue
        # The outermost one is enough; everything inside it goes with it.
        if any(id(parent) in farms for parent in element.ancestors()):
            farms.add(id(element))
            continue
        links = [node for node in element.iter() if node.tag == "a"]
        if len(links) < FARM_LINKS:
            continue
        total = _text_length(element)
        if total < FARM_FLOOR:
            continue
        linked = sum(_text_length(link) for link in links)
        if linked / total >= FARM_SHARE:
            farms.add(id(element))
    return farms


def main_text(html: str) -> str:
    """The readable text of a page, less its navigation, header, footer and sidebars.

    A page's own landmarks say which part is the advert: where it declares a ``<main>`` or
    an ``<article>`` carrying real text, read that, and otherwise read the body without the
    furniture around it. This is the fallback's text, and it goes to somebody who is about
    to read and correct it, so it would rather keep a stray line than cut the advert.
    """
    root = parse_html(html)
    body = body_of(root) or root
    farms = _link_farms(body)

    def drop(element: Element) -> bool:
        return is_chrome(element) or id(element) in farms

    whole = text_of(body, drop)

    for wanted in ("main", "role-main", "article"):
        for candidate in body.iter():
            if wanted == "role-main":
                if candidate.get("role").strip().lower() != "main":
                    continue
            elif candidate.tag != wanted:
                continue
            if is_chrome(candidate) or any(
                parent.tag in {"nav", "aside", "footer"} for parent in candidate.ancestors()
            ):
                continue
            text = text_of(candidate, drop)
            # A landmark holding almost none of the page is a teaser or a card, not the advert.
            if len(text) >= 200 and len(text) >= len(whole) * 0.25:
                return text
    return whole


def heading_title(html: str) -> str:
    """The one heading a page gives itself, where it gives exactly one.

    A board with no structured data still says what the advert is called, in the element
    HTML has for saying it. That is worth more than the title the page declares for sharing,
    which carries whatever else the board wants a link to read: LinkedIn's ``og:title`` is
    "<company> hiring <job> in <place> | LinkedIn" and its ``<h1>`` is the job.

    Exactly one, and not inside the furniture. Two headings is a page that has not said
    which is the subject, and guessing between them is the thing this does not do.
    """
    root = parse_html(html)
    body = body_of(root) or root
    headings = [
        heading
        for heading in find(body, "h1")
        if not is_chrome(heading)
        and not any(
            parent.tag in {"nav", "aside", "footer", "header"} for parent in heading.ancestors()
        )
    ]
    if len(headings) != 1:
        return ""
    text = re.sub(r"\s+", " ", text_of(headings[0])).strip()
    # A heading that runs to a paragraph is a page using h1 for something else.
    return text if 0 < len(text) <= 200 else ""


def strip_tags(value: str) -> str:
    """Remove markup from a fragment, for descriptions delivered as HTML inside JSON."""
    return html_to_text(unescape(value or ""))


# ----------------------------------------------------------------- JSON-LD


def _flatten(node) -> list[dict]:
    """Walk the shapes JSON-LD is allowed to take, and yield the objects inside.

    A document may be one object, a list of them, or an ``@graph`` holding either, and real
    sites use all three. A board that publishes several postings on one page wraps them in
    an ``ItemList`` or hangs them off ``mainEntity``, so those are followed too: the posting
    being looked at is often inside one, and picking it back out is the source's job.
    """
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_flatten(item))
    elif isinstance(node, dict):
        found.append(node)
        for key in ("@graph", "itemListElement", "mainEntity", "item"):
            if node.get(key) is not None:
                found.extend(_flatten(node[key]))
    return found


def extract_jsonld(html: str) -> list[dict]:
    """Return every JSON-LD object in the page.

    A block that will not parse is skipped rather than failing the capture: pages routinely
    carry several, and one being malformed says nothing about the others.
    """
    objects: list[dict] = []
    for script in find(parse_html(html), "script"):
        if "ld+json" not in script.get("type").lower():
            continue
        raw = "".join(child for child in script.children if isinstance(child, str))
        try:
            objects.extend(_flatten(json.loads(raw)))
        except (ValueError, TypeError):
            continue
    return objects


# ----------------------------------------- the same vocabulary, written into the markup


def _property_value(element: Element) -> str:
    """What one property element states: its attribute where it has one, else its text."""
    attribute = VALUE_ATTRIBUTE.get(element.tag)
    if attribute is not None:
        if element.has(attribute):
            return element.get(attribute).strip()
        # A <time> with no datetime states its date as text; the rest state nothing.
        if element.tag != "time":
            return ""
    return text_of(element)


def _type_name(raw: str) -> str:
    """The last segment of a type URL: "https://schema.org/JobPosting" -> "JobPosting"."""
    return ([piece for piece in re.split(r"[/#]", str(raw or "").strip()) if piece] or [""])[-1]


def _read_item(element: Element, scope_of, type_of, props_of) -> dict:
    """Read one item, and everything nested in it, into the plain object JSON-LD would be.

    ``scope_of`` says which element opens an item, ``type_of`` and ``props_of`` name its
    type and its properties. Microdata and RDFa differ only in those three, so one walk
    serves both and the reader downstream never learns which of them a page used.
    """
    item: dict = {}
    types = [name for name in (_type_name(part) for part in type_of(element).split()) if name]
    if types:
        item["@type"] = types[0] if len(types) == 1 else types

    def add(name: str, value) -> None:
        if value in ("", None):
            return
        # schema.org lets a property repeat; keep every value, as a list once there are two.
        if name in item:
            held = item[name]
            item[name] = [*held, value] if isinstance(held, list) else [held, value]
        else:
            item[name] = value

    def walk(parent: Element) -> None:
        for child in parent.children:
            if not isinstance(child, Element):
                continue
            names = props_of(child)
            nested = scope_of(child)
            if names:
                # A property that opens an item of its own is an object; else it is a value.
                value = (
                    _read_item(child, scope_of, type_of, props_of)
                    if nested
                    else _property_value(child)
                )
                for name in names:
                    add(name, value)
            # Descend unless the child began its own item, which has just read itself.
            if not nested:
                walk(child)

    walk(element)
    return item


def _outermost(root: Element, opens) -> list[Element]:
    """Only the outermost items: a nested one is read as part of the item that holds it."""
    return [
        element
        for element in root.iter()
        if opens(element) and not any(opens(parent) for parent in element.ancestors())
    ]


def extract_microdata(html: str) -> list[dict]:
    """Every microdata item in the page, shaped as the object JSON-LD would have given."""
    root = parse_html(html)

    def scope_of(element: Element) -> bool:
        return element.has("itemscope")

    return [
        _read_item(
            element,
            scope_of,
            lambda node: node.get("itemtype"),
            lambda node: node.get("itemprop").split(),
        )
        for element in _outermost(root, lambda node: node.has("itemscope"))
        if element.has("itemtype")
    ]


def extract_rdfa(html: str) -> list[dict]:
    """Every RDFa item in the page, shaped the same way.

    RDFa Lite is all a board ever uses here: ``typeof`` opens an item and ``property`` names
    a value. The ``vocab`` in force is not tracked, because a type name is what the reader
    matches on either way, and a prefix like "schema:title" is dropped for the same reason.
    """
    root = parse_html(html)

    return [
        _read_item(
            element,
            lambda node: node.has("typeof"),
            lambda node: node.get("typeof"),
            lambda node: [name.split(":")[-1] for name in node.get("property").split()],
        )
        for element in _outermost(root, lambda node: node.has("typeof"))
    ]


# ----------------------------------------------------------------- metadata


def extract_meta(html: str) -> dict[str, str]:
    """Return ``{"title": ..., "og:title": ..., ...}`` for what the page declares."""
    root = parse_html(html)
    meta: dict[str, str] = {}
    for tag in find(root, "meta"):
        key = tag.get("property") or tag.get("name")
        content = tag.get("content")
        if key and content:
            meta.setdefault(key.lower(), content)
    titles = find(root, "title")
    title = text_of(titles[0]) if titles else ""
    return {"title": title, **meta}
