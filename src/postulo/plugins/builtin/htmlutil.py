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
from contextlib import contextmanager
from contextvars import ContextVar
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
        """Every element beneath this one, in document order.

        Walked with a stack of its own: a page can nest as deep as it likes, and a recursive
        generator raises ``RecursionError`` somewhere past a thousand levels (#587).
        """
        stack = [iter(self.children)]
        while stack:
            for child in stack[-1]:
                if isinstance(child, Element):
                    yield child
                    stack.append(iter(child.children))
                    break
            else:
                stack.pop()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<{self.tag} {self.attrs}>"


#: HTML lets a page leave out these end tags, and a browser closes the element when the next
#: one starts. Each entry: the tags a start tag closes, found by looking up the open elements
#: until one of the boundary tags, which an element is never closed across (#587).
_IMPLIED_END: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "li": (frozenset({"li"}), frozenset({"ul", "ol", "menu"})),
    "dt": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "dd": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "td": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "th": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "tr": (frozenset({"tr", "td", "th"}), frozenset({"table", "thead", "tbody", "tfoot"})),
    "thead": (frozenset({"tr", "td", "th", "thead", "tbody", "tfoot"}), frozenset({"table"})),
    "tbody": (frozenset({"tr", "td", "th", "thead", "tbody", "tfoot"}), frozenset({"table"})),
    "tfoot": (frozenset({"tr", "td", "th", "thead", "tbody", "tfoot"}), frozenset({"table"})),
    "option": (frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),
    "optgroup": (frozenset({"option", "optgroup"}), frozenset({"select", "datalist"})),
}  # fmt: skip

#: A start tag of these closes an open ``<p>``, which cannot hold them.
_CLOSES_P = frozenset(
    {
        "address", "article", "aside", "blockquote", "details", "dialog", "div", "dl",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section",
        "table", "ul", "li", "dt", "dd",
    }
)  # fmt: skip

#: What an open ``<p>`` is looked for beneath: a table cell or a button holds its own.
_P_SCOPE = frozenset({"table", "td", "th", "caption", "button", "object", "template", "select"})


class _TreeBuilder(HTMLParser):
    """Assemble the tag stream into a tree, forgiving the things real pages do.

    An end tag with nothing open to match it is dropped, and one that matches something
    further up closes everything between -- which is what a browser does, and what makes an
    unclosed ``<p>`` cost a paragraph break rather than the rest of the page. A start tag
    that implies the end of the one before (``<li>`` after an ``<li>``, a ``<dd>`` after a
    ``<dt>``, a cell after a cell) closes it, as a browser does, so a list written without
    its end tags is a row of siblings and not a chain as deep as the list is long (#587).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("", {})
        self._open: list[Element] = [self.root]

    @property
    def _here(self) -> Element:
        return self._open[-1]

    def _close_up_to(self, closing: frozenset[str], boundary: frozenset[str]) -> None:
        """Close the outermost open element named in ``closing``, and what is in it, unless a
        boundary is in the way: ``<tr>`` ends the row before it and the cell it was in."""
        found = 0
        for index in range(len(self._open) - 1, 0, -1):
            name = self._open[index].tag
            if name in boundary:
                break
            if name in closing:
                found = index
        if found:
            del self._open[found:]

    def _imply_end_tags(self, tag: str) -> None:
        if tag in _IMPLIED_END:
            self._close_up_to(*_IMPLIED_END[tag])
        if tag in _CLOSES_P:
            self._close_up_to(frozenset({"p"}), _P_SCOPE)

    def handle_starttag(self, tag: str, attrs) -> None:
        self._imply_end_tags(tag)
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


#: The page being read and its tree, while a caller has said it will be read more than once.
_SHARED: ContextVar[tuple[str, Element] | None] = ContextVar("postulo_shared_page", default=None)


def parse_html(html: str) -> Element:
    """The page as a tree. Whatever was built before a parser error is still worth having.

    Inside `one_parse` the same string is parsed once, however many sources read it.
    """
    shared = _SHARED.get()
    if shared is not None and shared[0] is html:
        return shared[1]
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:
        logger.warning("Could not finish parsing the page", exc_info=True)
    return builder.root


@contextmanager
def one_parse(html: str):
    """Parse ``html`` once for everything read inside the block, and hand back its tree.

    A page is read by several sources in turn, each asking for the tree; none of them
    changes it, so they can all be given the same one. Matched by identity, so a different
    string (a fragment, a description) is parsed as it always was.
    """
    root = parse_html(html)
    token = _SHARED.set((html, root))
    try:
        yield root
    finally:
        _SHARED.reset(token)


def tree_of(page: Element | str) -> Element:
    """A page as a tree, parsing it only if it is not one already."""
    return page if isinstance(page, Element) else parse_html(page)


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

    # A stack of the children being read, and for each whether a break follows it: an
    # explicit one, so that no page is deep enough to exhaust the recursion limit (#587).
    stack = [iter(node.children)]
    breaks = [False]
    while stack:
        for child in stack[-1]:
            if isinstance(child, str):
                parts.append(child)
                continue
            if child.tag in IGNORED_CONTENT_TAGS or (drop is not None and drop(child)):
                continue
            block = child.tag in BLOCK_TAGS
            if block:
                parts.append("\n")
            stack.append(iter(child.children))
            breaks.append(block and child.tag not in VOID_BLOCK_TAGS)
            break
        else:
            stack.pop()
            if breaks.pop():
                parts.append("\n")
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


#: What is known of a run of text once its whitespace is collapsed: how long it is, and
#: whether it starts and ends with the one space a run of whitespace becomes.
_Run = tuple[int, bool, bool]
_NO_RUN: _Run = (0, False, False)


def _join(left: _Run, right: _Run) -> _Run:
    if not left[0]:
        return right
    if not right[0]:
        return left
    return (left[0] + right[0] - (left[2] and right[1]), left[1], right[2])


def _run_of(text: str) -> _Run:
    collapsed = re.sub(r"\s+", " ", text)
    if not collapsed:
        return _NO_RUN
    return (len(collapsed), collapsed[0] == " ", collapsed[-1] == " ")


def _stripped(run: _Run) -> int:
    length, lead, trail = run
    if length == 1 and lead:
        return 0
    return length - lead - trail


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
    # One pass from the leaves up gives every node its text, its links and their text, so
    # the page costs what it holds and not what it holds times how deep it nests.
    elements = list(body.iter())
    runs: dict[int, _Run] = {}
    link_counts: dict[int, int] = {}
    linked: dict[int, int] = {}
    for element in [*reversed(elements), body]:
        run = _NO_RUN
        count = 0
        share = 0
        for child in element.children:
            if isinstance(child, str):
                run = _join(run, _run_of(child))
            else:
                run = _join(run, runs[id(child)])
                count += link_counts[id(child)]
                share += linked[id(child)]
        runs[id(element)] = run
        if element.tag == "a":
            count += 1
            share += _stripped(run)
        link_counts[id(element)] = count
        linked[id(element)] = share

    farms: set[int] = set()
    inside: set[int] = set()
    for element in elements:
        parent = id(element.parent)
        # The outermost one is enough; everything inside it goes with it.
        if parent in farms or parent in inside:
            inside.add(id(element))
            continue
        if element.tag not in FARM_TAGS:
            continue
        key = id(element)
        if link_counts[key] - (element.tag == "a") < FARM_LINKS:
            continue
        total = _stripped(runs[key])
        if total < FARM_FLOOR:
            continue
        if linked[key] / total >= FARM_SHARE:
            farms.add(key)
    return farms


def main_text(page: Element | str) -> str:
    """The readable text of a page, less its navigation, header, footer and sidebars.

    A page's own landmarks say which part is the advert: where it declares a ``<main>`` or
    an ``<article>`` carrying real text, read that, and otherwise read the body without the
    furniture around it. This is the fallback's text, and it goes to somebody who is about
    to read and correct it, so it would rather keep a stray line than cut the advert.
    """
    root = tree_of(page)
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


def heading_title(page: Element | str) -> str:
    """The one heading a page gives itself, where it gives exactly one.

    A board with no structured data still says what the advert is called, in the element
    HTML has for saying it. That is worth more than the title the page declares for sharing,
    which carries whatever else the board wants a link to read: LinkedIn's ``og:title`` is
    "<company> hiring <job> in <place> | LinkedIn" and its ``<h1>`` is the job.

    Exactly one, and not inside the furniture. Two headings is a page that has not said
    which is the subject, and guessing between them is the thing this does not do.
    """
    root = tree_of(page)
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


def flatten(node) -> list[dict]:
    """Walk the shapes JSON-LD is allowed to take, and yield the objects inside.

    A document may be one object, a list of them, or an ``@graph`` holding either, and real
    sites use all three. A board that publishes several postings on one page wraps them in
    an ``ItemList`` or hangs them off ``mainEntity``, so those are followed too: the posting
    being looked at is often inside one, and picking it back out is the source's job. A page
    that wraps its advert in a page-level item (a ``WebPage`` with the posting as its
    ``mainEntity``, ``hasPart`` or ``about``) is followed the same way, whichever spelling
    wrote it (#589).
    """
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(flatten(item))
    elif isinstance(node, dict):
        found.append(node)
        for key in ("@graph", "itemListElement", "mainEntity", "item", "hasPart", "about"):
            if node.get(key) is not None:
                found.extend(flatten(node[key]))
    return found


def extract_jsonld(page: Element | str) -> list[dict]:
    """Return every JSON-LD object in the page.

    A block that will not parse is skipped rather than failing the capture: pages routinely
    carry several, and one being malformed says nothing about the others.
    """
    objects: list[dict] = []
    for script in find(tree_of(page), "script"):
        if "ld+json" not in script.get("type").lower():
            continue
        raw = "".join(child for child in script.children if isinstance(child, str))
        try:
            objects.extend(flatten(json.loads(raw)))
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

    stack = [iter(element.children)]
    while stack:
        for child in stack[-1]:
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
                stack.append(iter(child.children))
                break
        else:
            stack.pop()
    return item


def _top_level(root: Element, opens, names) -> list[Element]:
    """The items a page states on its own, as the microdata specification counts them: one
    that is nobody's property, wherever it sits (#589). A theme that wraps the whole page in
    a ``WebPage`` item must not hide the posting inside it; an item that *is* a property is
    read as part of the item holding it, and so is not read twice.
    """
    return [
        element
        for element in root.iter()
        if opens(element)
        and (not names(element) or not any(opens(parent) for parent in element.ancestors()))
    ]


def extract_microdata(page: Element | str) -> list[dict]:
    """Every microdata item in the page, shaped as the object JSON-LD would have given."""
    root = tree_of(page)

    def scope_of(element: Element) -> bool:
        return element.has("itemscope")

    return [
        _read_item(
            element,
            scope_of,
            lambda node: node.get("itemtype"),
            lambda node: node.get("itemprop").split(),
        )
        for element in _top_level(
            root, lambda node: node.has("itemscope"), lambda node: node.get("itemprop").split()
        )
        if element.has("itemtype")
    ]


def extract_rdfa(page: Element | str) -> list[dict]:
    """Every RDFa item in the page, shaped the same way.

    RDFa Lite is all a board ever uses here: ``typeof`` opens an item and ``property`` names
    a value. The ``vocab`` in force is not tracked, because a type name is what the reader
    matches on either way, and a prefix like "schema:title" is dropped for the same reason.
    """
    root = tree_of(page)

    return [
        _read_item(
            element,
            lambda node: node.has("typeof"),
            lambda node: node.get("typeof"),
            lambda node: [name.split(":")[-1] for name in node.get("property").split()],
        )
        for element in _top_level(
            root, lambda node: node.has("typeof"), lambda node: node.get("property").split()
        )
    ]


# ----------------------------------------------------------------- metadata


def extract_meta(page: Element | str) -> dict[str, str]:
    """Return ``{"title": ..., "og:title": ..., ...}`` for what the page declares."""
    root = tree_of(page)
    meta: dict[str, str] = {}
    for tag in find(root, "meta"):
        key = tag.get("property") or tag.get("name")
        content = tag.get("content")
        if key and content:
            meta.setdefault(key.lower(), content)
    titles = find(root, "title")
    title = text_of(titles[0]) if titles else ""
    return {"title": title, **meta}


def page_language(page: Element | str) -> str:
    """The language a page says it is written in, exactly as it says it; "" if it does not.

    Its ``<html lang>`` first, which is where HTML asks for it; then an ``og:locale`` or a
    ``Content-Language`` it declares, whichever comes first. What comes back is a stranger's
    text and is checked by whoever uses it (`patterns.tag_of`).
    """
    return language_of(tree_of(page))


def language_of(root: Element) -> str:
    """`page_language`, for a page already parsed."""
    for element in find(root, "html"):
        declared = (element.get("lang") or element.get("xml:lang")).strip()
        if declared:
            return declared
    for tag in find(root, "meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("http-equiv")).strip().lower()
        if key in {"og:locale", "content-language", "language"} and tag.get("content").strip():
            return tag.get("content").strip()
    return ""
