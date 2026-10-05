"""Remembered places: where a person's own corrections showed a field to be, per site (#267).

Every capture is corrected by a person before it becomes anything, and what they changed a
field to is a labelled example of how to read that site. When the corrected value can be
found in the page, the *place* it was found is remembered -- for that person and that site
only -- and the next page from the same site is read there first. Postulo owns the rows and
decides whose they are (`jobs.remembered`); this module is the part that reads a page: what
a place is, where the places on a page are, which one holds a corrected value, and what one
holds now.

**A place survives a small change to the page.** It is never a path of child positions,
which the first banner or cookie notice a site adds would break. It is one of, most stable
first:

``id``
    an element's own ``id``, where it does not look generated (no counter, no hash);
``itemprop``
    a microdata property name, which is vocabulary rather than layout;
``data``
    a ``data-*`` attribute and its value, which is what sites put there for their own tests;
``label``
    the words beside the value: the ``<dt>`` before a ``<dd>``, the ``<th>`` or first cell
    before a cell, the "Local:" before the rest of its line -- which is how public-sector
    notices lay out every field, and what survives a redesign that keeps the wording;
``class``
    a class name no other element of that tag carries, where it does not look generated;
``heading``
    a heading's position among its level under the page's one ``<main>`` or ``<article>``;
``meta``
    a ``<meta>`` property's name.

**Nothing is invented.** A place that is not on the page, or is there more than once, finds
nothing; what it does find is read exactly as the other tiers read the same field -- a
salary by `patterns`, a date only when it is whole, an employment type only when the
vocabulary knows it -- and bounded the same way. A place that finds nothing leaves the field
to the tiers below it.

**Learning keeps nothing of the page.** What the review screen learns from is a bounded list
of the places on the page and, for each, a digest of its text and the pay, date or
employment type it reads as -- enough to recognise a corrected value, and not enough to read
the page back. Postulo keeps that list beside a capture while it waits, and only where the
page's source was not kept (`jobs.pages`), and throws it away once the capture is decided.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from . import patterns
from .htmlutil import (
    BLOCK_TAGS,
    IGNORED_CONTENT_TAGS,
    Element,
    body_of,
    classes_of,
    find,
    language_of,
    text_of,
    tree_of,
)
from .vocabulary import employment_type

#: What a place can be remembered for, and the posting fields each one fills.
FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "company_name": ("company_name",),
    "location": ("location",),
    "employment_type": ("employment_type",),
    "salary": ("salary_min", "salary_max", "salary_currency", "salary_period"),
    "closes_at": ("closes_at",),
    "description": ("description",),
}

#: The posting fields that are empty as ``None`` rather than as "".
NULLABLE = frozenset({"salary_min", "salary_max", "closes_at"})

#: The kinds of place, most stable first. Where a value sits in several, the first is kept.
KINDS = ("id", "itemprop", "data", "label", "class", "heading", "meta")

#: The keys each kind of place is written with, and the only ones it may be read back with.
KEYS = {
    **{kind: (kind,) for kind in KINDS},
    "class": ("class", "tag"),
    "heading": ("heading", "in", "index"),
}

#: How many places of each kind one page offers to learn from, in the order they appear.
#: Per kind rather than all together, so that a page with a thousand ids still offers its
#: headings and its labels; together they are the most one page keeps, `MOST_PLACES`.
PER_KIND = {
    "id": 100,
    "itemprop": 40,
    "data": 60,
    "label": 60,
    "class": 100,
    "heading": 20,
    "meta": 20,
}
MOST_PLACES = sum(PER_KIND.values())

#: And what they may weigh together, written down: the list waits beside a capture, and a
#: page of long class names should not make it heavier than the capture itself.
MOST_BYTES = 32 * 1024

#: A short field's longest value, as every tier bounds it; a description's shortest, since
#: a place holding a line is not where an advert is.
SHORT = 500
DESCRIPTION_AT_LEAST = 200

#: A value read to recognise a salary or a date is a line, not a paragraph.
A_LINE = 120

#: A label is a few words. Anything longer is a sentence that happens to end in a colon.
LONGEST_LABEL = 80

#: The tags a label is written in. A ``dt``, a ``th`` and a row's first cell label what
#: follows them; the others label the rest of their line when their own text ends in ":".
LABELS = frozenset({"dt", "th", "td", "label", "strong", "b", "span"})
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

#: What a framework writes when it generates a name: a counter, a hash, a styling prefix.
GENERATED = re.compile(
    r"\d{3,}|^(?:css|sc|jsx|emotion|styled|svelte|ember|jss\d*|makeStyles)-", re.IGNORECASE
)
HASHLIKE = re.compile(r"[0-9a-f]{5,}", re.IGNORECASE)
#: Where CSS modules put the hash that every build changes: "JobCard_title__3xY9z". BEM
#: writes the same two underscores before an element's name, "job__location", which is
#: lower case and has no digit -- a hash almost always has one or the other.
MODULE_SUFFIX = re.compile(r"__([\w-]{5,8})$")


# ------------------------------------------------------------------ what is stable


def _hashed_suffix(name: str) -> bool:
    found = MODULE_SUFFIX.search(name)
    if found is None:
        return False
    suffix = found.group(1)
    mixed = any(c.isupper() for c in suffix) and any(c.islower() for c in suffix)
    return mixed or any(c.isdigit() for c in suffix)


def stable(name: str) -> bool:
    """Whether an id, a class or a ``data-*`` value looks written by a person."""
    if not (2 < len(name) <= 64) or not re.fullmatch(r"[A-Za-z][\w-]*", name):
        return False
    if GENERATED.search(name) or _hashed_suffix(name):
        return False
    return not any(any(c.isdigit() for c in run) for run in HASHLIKE.findall(name))


def _plain(text: str) -> str:
    return " ".join((text or "").split())


def digest(text: str) -> str:
    """A text as it is compared: spacing and capitals aside, and not kept."""
    return hashlib.sha256(_plain(text).casefold().encode("utf-8")).hexdigest()[:16]


def kind_of(place: dict) -> str:
    return next((kind for kind in KINDS if kind in place), "")


def _elements(root: Element):
    """Every element that can hold something a reader sees, in document order."""

    # Walked with a stack of its own: no page is deep enough to exhaust the recursion limit.
    stack = [iter(root.children)]
    while stack:
        for child in stack[-1]:
            if isinstance(child, Element) and child.tag not in IGNORED_CONTENT_TAGS:
                yield child
                stack.append(iter(child.children))
                break
        else:
            stack.pop()


class _Siblings:
    """Where each child sits among its parent's, found once per parent.

    Asking a parent "who follows this child?" by scanning from its first child costs the
    parent's width every time, and a row of ten thousand cells asks ten thousand times.
    """

    def __init__(self) -> None:
        self._index: dict[int, int] = {}
        self._next: dict[int, Element | None] = {}
        self._elements: dict[int, list[Element]] = {}

    def _load(self, parent: Element) -> list[Element]:
        elements = self._elements.get(id(parent))
        if elements is not None:
            return elements
        elements = self._elements[id(parent)] = []
        for position, child in enumerate(parent.children):
            self._index[id(child)] = position
            if isinstance(child, Element):
                if elements:
                    self._next[id(elements[-1])] = child
                self._next[id(child)] = None
                elements.append(child)
        return elements

    def position(self, element: Element) -> int:
        """The index of ``element`` in its parent's children."""
        self._load(element.parent)
        return self._index[id(element)]

    def elements(self, parent: Element) -> list[Element]:
        """The parent's children that are elements."""
        return self._load(parent)

    def next_element(self, element: Element) -> Element | None:
        if element.parent is None:
            return None
        self._load(element.parent)
        return self._next[id(element)]


def _label_key(text: str) -> str:
    return _plain(text).rstrip(":：").strip().casefold()


def _ends_a_label(text: str) -> bool:
    return _plain(text).endswith((":", "："))


def _rest_of_line(element: Element, siblings: _Siblings) -> str:
    """What follows a "Label:" on its line: up to a break, a block, or the next label."""
    parent = element.parent
    if parent is None:
        return ""
    parts: list[str] = []
    children = parent.children
    for at in range(siblings.position(element) + 1, len(children)):
        child = children[at]
        if isinstance(child, str):
            parts.append(child)
            continue
        if child.tag in IGNORED_CONTENT_TAGS:
            continue
        if child.tag in BLOCK_TAGS:
            break
        if child.tag in LABELS and _ends_a_label(text_of(child)):
            break
        parts.append(text_of(child))
    return _plain("".join(parts))


def _labelled(element: Element, own: str, siblings: _Siblings) -> str:
    """What a label element labels, or "": the ``<dd>`` after a ``<dt>``, the cell after a
    ``<th>`` or a row's first of two cells, the rest of a line after "Label:"."""
    if element.tag in ("dt", "th", "td"):
        following = siblings.next_element(element)
        wanted = {"dt": "dd", "th": "td", "td": "td"}[element.tag]
        if following is None or following.tag != wanted:
            return ""
        if element.tag == "td":
            cells = siblings.elements(element.parent)
            if len(cells) != 2 or cells[0] is not element:
                return ""
        return text_of(following)
    if not _ends_a_label(own):
        return ""
    return _rest_of_line(element, siblings)


def _labels(root: Element) -> list[tuple[str, str]]:
    """Every label on the page that labels something: ``(key, what it labels)``.

    Only an element that labels something counts, so a label wrapped twice -- a ``<span>``
    around a ``<strong>`` -- is one label and not two, and a word in bold that labels
    nothing is not a label at all.
    """
    found: list[tuple[str, str]] = []
    siblings = _Siblings()
    for element in _elements(root):
        if element.tag not in LABELS:
            continue
        own = _plain(text_of(element))
        if not own or len(own) > LONGEST_LABEL:
            continue
        labelled = _labelled(element, own, siblings)
        if labelled.strip():
            found.append((_label_key(own), labelled))
    return found


def _landmark(root: Element) -> tuple[str, Element]:
    """The one place an advert is: the page's ``<main>``, else its one ``<article>``."""
    mains = [
        element
        for element in root.iter()
        if element.tag == "main" or element.get("role").strip().lower() == "main"
    ]
    if len(mains) == 1:
        return "main", mains[0]
    articles = find(root, "article")
    if len(articles) == 1:
        return "article", articles[0]
    return "body", body_of(root) or root


def _meta_key(element: Element) -> str:
    return (element.get("property") or element.get("name")).strip().lower()


# ------------------------------------------------------------------ finding a place


def _short_text(value, longest: int = 120) -> bool:
    return isinstance(value, str) and 0 < len(value) <= longest


def clean(place) -> dict | None:
    """A remembered place as it may be trusted: the shape this module writes, and only it.

    Places come back from the database, from a capture waiting for review and from an
    archive somebody hands over, and are checked here rather than believed. A place
    carrying any key its kind does not have is not one this module wrote, and is refused
    rather than trimmed. What comes back is a new dict.
    """
    if not isinstance(place, dict):
        return None
    kind = kind_of(place)
    if set(place) != set(KEYS.get(kind, ("",))):
        return None
    value = place.get(kind)
    if kind == "data":
        if (
            isinstance(value, list)
            and len(value) == 2
            and all(_short_text(part, 80) for part in value)
            and value[0].startswith("data-")
        ):
            return {"data": [value[0], value[1]]}
        return None
    if kind == "heading":
        index = place.get("index")
        if (
            value in HEADINGS
            and place.get("in") in ("main", "article", "body")
            and isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < 50
        ):
            return {"heading": value, "in": place["in"], "index": index}
        return None
    if kind == "class":
        tag = place.get("tag")
        if _short_text(value) and _short_text(tag, 20):
            return {"class": value, "tag": tag}
        return None
    if kind and _short_text(value):
        return {kind: value}
    return None


def locate(root: Element, place: dict) -> Element | str | None:
    """What one place holds on this page: the element, or for a label the text after it.

    ``None`` unless the place is on the page exactly once -- a place that has become two is a
    page that changed, and guessing between them is the thing this does not do.
    """
    place = clean(place)
    if place is None:
        return None
    kind = kind_of(place)
    if kind == "label":
        found = [value for key, value in _labels(root) if key == place["label"]]
        return found[0] if len(found) == 1 else None
    if kind == "heading":
        name, landmark = _landmark(root)
        if name != place["in"]:
            return None
        headings = [e for e in _elements(landmark) if e.tag == place["heading"]]
        return headings[place["index"]] if place["index"] < len(headings) else None
    if kind == "id":
        found = [e for e in _elements(root) if e.get("id") == place["id"]]
    elif kind == "itemprop":
        found = [e for e in _elements(root) if place["itemprop"] in e.get("itemprop").split()]
    elif kind == "data":
        name, value = place["data"]
        found = [e for e in _elements(root) if e.get(name) == value]
    elif kind == "class":
        found = [
            e for e in _elements(root) if e.tag == place["tag"] and place["class"] in classes_of(e)
        ]
    elif kind == "meta":
        found = [e for e in find(root, "meta") if _meta_key(e) == place["meta"]]
    else:  # pragma: no cover - `clean` admits no other kind
        return None
    return found[0] if len(found) == 1 else None


def _text(found: Element | str) -> str:
    if isinstance(found, str):
        return found
    if found.tag == "meta":
        return found.get("content")
    return text_of(found)


def read(found: Element | str | None, field: str, tag: str = "") -> dict:
    """The posting fields one place fills, read as the other tiers read them; {} for none."""
    if found is None or field not in FIELDS:
        return {}
    text = _text(found)
    if field == "closes_at" and isinstance(found, Element) and found.tag == "time":
        # A <time> states its date in an attribute, whatever it shows.
        text = found.get("datetime") or text
    plain = _plain(text)
    if not plain:
        return {}
    if field in ("title", "company_name", "location"):
        return {field: plain} if len(plain) <= SHORT else {}
    if field == "description":
        return {"description": text.strip()} if len(plain) >= DESCRIPTION_AT_LEAST else {}
    if field == "employment_type":
        known = employment_type(plain)
        return {"employment_type": known} if known else {}
    if field == "closes_at":
        date = patterns.date_in(plain, tag) if len(plain) <= A_LINE else None
        return {"closes_at": date} if date else {}
    # What is left of `FIELDS` is the pay.
    pay = patterns.pay_in(plain, tag) if len(plain) <= A_LINE else None
    if pay is None:
        return {}
    low, high, currency, period = pay
    return {
        "salary_min": low,
        "salary_max": high,
        "salary_currency": currency,
        "salary_period": period,
    }


def fill(url: str, page: Element | str, hints, stated: dict) -> dict:
    """Fill what the tiers above left empty from a person's remembered places.

    ``hints`` are what Postulo hands a source (`plugins.base.RememberedPlace`): each has a
    ``field``, a ``place``, and an ``outcome`` this sets -- "used" where the place filled its
    field, "missed" where the field was open and the place found nothing, "" where a tier
    above had already stated the field and the place was never asked. ``stated`` is changed
    and returned.

    A place fills its whole group: the pay it found is the pay, with the currency and the
    period it found or none, and nothing below it adds a period to somebody else's figure.
    """
    hints = list(hints or ())
    for hint in hints:
        hint.outcome = ""
    if not hints:
        return stated
    root = tree_of(page)
    tag = patterns.tag_of(language_of(root))
    for hint in hints:
        targets = FIELDS.get(hint.field)
        if targets is None:
            continue
        # The pay's group is stated when either figure is; any other field, when it is.
        if any(stated.get(name) not in (None, "", []) for name in targets[:2]):
            continue
        found = read(locate(root, hint.place), hint.field, tag)
        if not found:
            hint.outcome = "missed"
            continue
        for name in targets:
            value = found.get(name)
            stated[name] = value if value not in (None, "") else (None if name in NULLABLE else "")
        hint.outcome = "used"
    return stated


# ------------------------------------------------------------------ learning a place


def _record(place: dict, holder: Element | str, tag: str) -> dict | None:
    """One place on the page and what it holds, as the review screen will compare it."""
    plain = _plain(_text(holder))
    if not plain:
        return None
    record: dict = {"place": place, "text": digest(plain), "size": len(plain)}
    if len(plain) <= A_LINE:
        pay = patterns.pay_in(plain, tag)
        if pay is not None:
            record["salary"] = ["" if value is None else str(value) for value in pay[:2]]
        when = holder.get("datetime") if isinstance(holder, Element) else ""
        date = patterns.date_in(when or plain, tag)
        if date is not None:
            record["date"] = date.isoformat()
        known = employment_type(plain)
        if known:
            record["employment"] = known
    return record


def _best_name(element: Element, ids, props, data, classes) -> dict | None:
    """The most stable name an element has that names nothing else on the page."""
    own_id = element.get("id")
    if own_id and ids[own_id] == 1 and stable(own_id):
        return {"id": own_id}
    for token in element.get("itemprop").split():
        if props[token] == 1 and len(token) <= 120:
            return {"itemprop": token}
    for name, value in sorted(element.attrs.items()):
        if (
            name.startswith("data-")
            and len(name) <= 80
            and value
            and data[(name, value)] == 1
            and stable(value)
        ):
            return {"data": [name, value]}
    for name in sorted(classes_of(element)):
        if classes[(element.tag, name)] == 1 and stable(name) and len(element.tag) <= 20:
            return {"class": name, "tag": element.tag}
    return None


def _candidates(root: Element):
    """Every place on the page that names exactly one thing, with what it holds, by kind.

    Each element once, by the most stable name it has; each label, heading and ``<meta>``
    on top of that, because those are names a page gives a thing without its markup.
    """
    by_kind: dict[str, list[tuple[dict, Element | str]]] = {kind: [] for kind in KINDS}
    elements = list(_elements(root))
    ids = Counter(e.get("id") for e in elements if e.get("id"))
    props = Counter(token for e in elements for token in e.get("itemprop").split())
    data = Counter(
        (name, value)
        for e in elements
        for name, value in e.attrs.items()
        if name.startswith("data-") and value
    )
    classes = Counter((e.tag, name) for e in elements for name in classes_of(e))
    for element in elements:
        place = _best_name(element, ids, props, data, classes)
        if place is not None:
            by_kind[kind_of(place)].append((place, element))

    labels = _labels(root)
    counted = Counter(key for key, _value in labels)
    for key, value in labels:
        if counted[key] == 1:
            by_kind["label"].append(({"label": key}, value))

    name, landmark = _landmark(root)
    for level in HEADINGS:
        for index, heading in enumerate(e for e in _elements(landmark) if e.tag == level):
            if index >= PER_KIND["heading"]:
                break
            by_kind["heading"].append(({"heading": level, "in": name, "index": index}, heading))

    metas = find(root, "meta")
    keys = Counter(_meta_key(element) for element in metas)
    for element in metas:
        key = _meta_key(element)
        if key and keys[key] == 1 and len(key) <= 120 and element.get("content").strip():
            by_kind["meta"].append(({"meta": key}, element))
    return by_kind


def places(url: str, page: Element | str) -> list[dict]:
    """Every place on a page a correction could be remembered at, and what each holds.

    Bounded by kind, by count and by weight, and holding no text of the page: a digest of
    each place's text and what it reads as, which is what recognising a corrected value
    takes. Most stable kinds first, and each kind in the order the page has them.
    """
    root = tree_of(page)
    tag = patterns.tag_of(language_of(root))
    records: list[dict] = []
    weight = 2
    for kind, found in _candidates(root).items():
        taken = 0
        for place, holder in found:
            if taken >= PER_KIND[kind]:
                break
            record = _record(place, holder, tag)
            if record is None:
                continue
            size = len(json.dumps(record, ensure_ascii=False)) + 1
            if weight + size > MOST_BYTES:
                return records
            records.append(record)
            weight += size
            taken += 1
    return records


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _holds(record: dict, field: str, value) -> bool:
    """Whether one place held ``value`` for ``field`` when the page was read."""
    size = record.get("size")
    size = size if isinstance(size, int) else 0
    if field in ("title", "company_name", "location"):
        return size <= SHORT and record.get("text") == digest(str(value))
    if field == "description":
        return size >= DESCRIPTION_AT_LEAST and record.get("text") == digest(str(value))
    if field == "employment_type":
        return bool(value) and record.get("employment") == value
    if field == "closes_at":
        return isinstance(value, dt.date) and record.get("date") == value.isoformat()
    if field == "salary":
        held = record.get("salary")
        if not isinstance(held, list) or len(held) != 2 or not isinstance(value, tuple | list):
            return False
        return [_decimal(part) for part in held] == [_decimal(part) for part in value]
    return False


def _nothing(value) -> bool:
    if isinstance(value, tuple | list):
        return all(part in (None, "") for part in value)
    return value in (None, "")


def learn(records: list, field: str, value) -> dict | None:
    """The most stable place among ``records`` that held ``value`` for ``field``.

    ``value`` is what the person corrected the field to: text, a date, an employment type,
    or for the pay its ``(low, high)``. ``None`` where the page held it nowhere, which is
    most corrections -- and then nothing is learned, because nothing was shown.

    A kind of place that held the value twice says nothing about which of the two the person
    was reading, so it is passed over for the next kind that held it once; a value held
    twice at every kind is learned nowhere.
    """
    if field not in FIELDS or _nothing(value):
        return None
    by_kind: dict[str, list[dict]] = {}
    for record in records or ():
        if not isinstance(record, dict):
            continue
        place = clean(record.get("place"))
        if place is None or not _holds(record, field, value):
            continue
        by_kind.setdefault(kind_of(place), []).append(place)
    for kind in KINDS:
        held = by_kind.get(kind, [])
        if len(held) == 1:
            return held[0]
    return None
