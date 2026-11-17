"""Reading what a company's own website declares about itself (#1256).

Two halves, both of what the site says and nothing guessed from the shape of its markup:

- **its icon**, exactly as *Find logo* has always looked for it: an apple-touch-icon, a
  declared icon by size, the logo of a schema.org Organization in JSON-LD, an Open Graph or
  Twitter image, five of those at most, and then the conventional ``/favicon.ico``. The order
  is the order *Find logo* tries them in, and it has not changed (#321, #959);
- **the rest**, read from a schema.org ``Organization`` (or one of its kinds) in JSON-LD,
  microdata or RDFa: its legal and other names, its mailbox and number, its social profiles
  and Wikidata item (``sameAs``), its address, its LEI, when it was founded and how many it
  employs.

Each value says which property it was read from and in which syntax, and points at the page.
What comes back is cleaned by Postulo before anybody sees it; here it is only read.
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from postulo.plugins.api import (
    OfferedValue,
    RegisterAddress,
    extract_jsonld,
    extract_microdata,
    extract_rdfa,
)

logger = logging.getLogger(__name__)

#: Five declared images at most, so that the conventional favicon is always the sixth try.
MAX_DECLARED = 5
FAVICON = "/favicon.ico"

#: The kinds of organisation schema.org names that do not say so in their own name.
ORGANISATIONS = frozenset(
    {
        "corporation",
        "localbusiness",
        "ngo",
        "airline",
        "consortium",
        "onlinebusiness",
        "onlinestore",
        "store",
    }
)

#: A Wikidata item's page, which ``sameAs`` may name.
_ITEM = re.compile(r"https?://(?:www\.|m\.)?wikidata\.org/(?:wiki|entity)/(Q[1-9][0-9]*)")


# ------------------------------------------------------------------ its icon


class _LogoLinks(HTMLParser):
    """The addresses a page offers for its own icon, best first, each with where it was read.

    Only what the site declares about itself: an apple-touch-icon, a declared icon, an
    Open Graph image, or the logo in a schema.org Organization block. Nothing is guessed
    from the markup's shape.
    """

    def __init__(self) -> None:
        super().__init__()
        self.apple: list[tuple[int, str]] = []
        self.icons: list[tuple[int, str]] = []
        self.og: list[tuple[str, str]] = []
        self.json_ld: list[str] = []
        self._in_ld = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "link":
            rel = values.get("rel", "").lower()
            href = values.get("href", "")
            if not href:
                return
            size = _largest(values.get("sizes", ""))
            if "apple-touch-icon" in rel:
                self.apple.append((size, href))
            elif "icon" in rel.split():
                self.icons.append((size, href))
        elif tag == "meta":
            name = (values.get("property") or values.get("name") or "").lower()
            if name in ("og:image", "og:logo", "twitter:image") and values.get("content"):
                self.og.append((values["content"], name))
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_ld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_ld and data.strip():
            self.json_ld.append(data)

    def candidates(self) -> list[tuple[str, str]]:
        """``(address as written, where it was read)``, best first, each address once."""
        found = [
            (href, 'link rel="apple-touch-icon"') for _s, href in sorted(self.apple, reverse=True)
        ]
        found += [(href, 'link rel="icon"') for _s, href in sorted(self.icons, reverse=True)]
        found += [
            (href, 'schema.org Organization, "logo" (JSON-LD)')
            for href in _organisation_logos(self.json_ld)
        ]
        found += [(href, f'meta "{name}"') for href, name in self.og]
        seen: dict[str, str] = {}
        for href, origin in found:
            if href and href not in seen:
                seen[href] = origin
        return list(seen.items())


def _largest(sizes: str) -> int:
    # A number is read from where its digits begin. Without the look-behind, a run of
    # digits with no `x` after it is tried again from every digit in it: 40,000 of them in
    # a page's `sizes` took fifteen seconds, and a page may be two megabytes (#321).
    numbers = [
        int(match) for match in re.findall(r"(?<!\d)(\d{1,5})x\d+", sizes or "", re.IGNORECASE)
    ]
    return max(numbers, default=0)


def _organisation_logos(blocks: list[str]) -> list[str]:
    """The ``logo`` of any schema.org Organization the page declares."""
    found: list[str] = []
    for block in blocks:
        try:
            payload = json.loads(block)
        except (ValueError, RecursionError):  # RecursionError: nested thousands deep (#959)
            continue
        for node in _walk(payload):
            if not isinstance(node, dict):
                continue
            types = node.get("@type", "")
            types = types if isinstance(types, list) else [types]
            if not any("organization" in str(one).lower() for one in types):
                continue
            logo = node.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url")
            if isinstance(logo, str) and logo:
                found.append(logo)
    return found


def _walk(node):
    """Every node of a parsed document, without recursing: depth is the page's to choose (#959)."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def logos(html: str, base: str) -> list[OfferedValue]:
    """The icons the page declares, five at most, and then the conventional favicon."""
    parser = _LogoLinks()
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover - a broken page is not an error worth showing
        logger.exception("Could not read the markup of a company's website")
    found = [
        OfferedValue("logo", urljoin(base, href), origin=origin, url=base)
        for href, origin in parser.candidates()[:MAX_DECLARED]
    ]
    found.append(OfferedValue("logo", urljoin(base, FAVICON), origin=FAVICON, url=base))
    return found


# ------------------------------------------------------------ the organisation


def _is_organisation(item: dict) -> str:
    """The schema.org type that makes ``item`` an organisation, or nothing."""
    types = item.get("@type", "")
    for one in types if isinstance(types, list) else [types]:
        name = str(one).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if "organization" in name.lower() or name.lower() in ORGANISATIONS:
            return name
    return ""


def _items(html: str) -> list[tuple[str, dict]]:
    """Every item the page declares, in each syntax, with the syntax's name."""
    found: list[tuple[str, dict]] = []
    for syntax, extract in (
        ("JSON-LD", extract_jsonld),
        ("microdata", extract_microdata),
        ("RDFa", extract_rdfa),
    ):
        try:
            items = extract(html)
        except RecursionError:  # nested deeper than Python recurses (#959)
            continue
        except Exception:  # pragma: no cover - one syntax that will not read is not the rest
            logger.exception("Could not read the %s of a company's website", syntax)
            continue
        found += [(syntax, item) for item in items if isinstance(item, dict)]
    return found


def _all(value) -> list:
    return value if isinstance(value, list) else [value]


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    return value.strip() if isinstance(value, str) else ""


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _headcount(value):
    """``numberOfEmployees``, as a ``(low, high)`` pair: a number, or a QuantitativeValue
    with a ``value``, or a ``minValue`` and a ``maxValue``."""
    if isinstance(value, dict):
        exact = _number(value.get("value"))
        if exact is not None:
            return (exact, exact)
        low, high = _number(value.get("minValue")), _number(value.get("maxValue"))
        if low is not None:
            return (low, high)
        return None
    exact = _number(value)
    return (exact, exact) if exact is not None else None


def _address(value):
    if not isinstance(value, dict):
        return None
    country = value.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("identifier") or country.get("name") or ""
    country = country.strip().upper() if isinstance(country, str) else ""
    return RegisterAddress(
        street=_text(value.get("streetAddress")),
        postcode=_text(value.get("postalCode")),
        municipality=_text(value.get("addressLocality")),
        region=_text(value.get("addressRegion")),
        country=country if len(country) == 2 else "",
    )


def organisation(html: str, page: str) -> list[OfferedValue]:
    """What a schema.org Organization on the page says about the company."""
    found: list[OfferedValue] = []
    for syntax, item in _items(html):
        kind = _is_organisation(item)
        if not kind:
            continue

        def offer(field, value, prop, kind=kind, syntax=syntax):
            found.append(
                OfferedValue(
                    field, value, origin=f'schema.org {kind}, "{prop}" ({syntax})', url=page
                )
            )

        for prop in ("legalName", "name"):
            for name in _all(item.get(prop)):
                if _text(name):
                    offer("name", _text(name), prop)
        for name in _all(item.get("alternateName")):
            name = _text(name)
            if name:
                offer("acronym" if len(name) <= 15 else "name", name, "alternateName")
        points = [
            item,
            *(point for point in _all(item.get("contactPoint")) if isinstance(point, dict)),
        ]
        for point in points:
            prop = "email" if point is item else "contactPoint"
            for email in _all(point.get("email")):
                if _text(email):
                    offer("email", _text(email), prop)
            for number in _all(point.get("telephone")):
                if _text(number):
                    offer("phone", _text(number), "telephone" if point is item else prop)
        for link in _all(item.get("sameAs")):
            link = _text(link) if not isinstance(link, str) else link.strip()
            if not link:
                continue
            item_id = _ITEM.match(link)
            if item_id:
                offer("identifier", ("wikidata", item_id[1]), "sameAs")
            else:
                offer("social", link, "sameAs")
        for address in _all(item.get("address")):
            address = _address(address)
            if address is not None:
                offer("address", address, "address")
        for lei in _all(item.get("leiCode")):
            if _text(lei):
                offer("identifier", ("lei", _text(lei)), "leiCode")
        for founded in _all(item.get("foundingDate")):
            if _text(founded):
                offer("founded", _text(founded)[:10], "foundingDate")
        for count in _all(item.get("numberOfEmployees")):
            size = _headcount(count)
            if size is not None:
                offer("size", size, "numberOfEmployees")
    return found


def read(answers, subject) -> list[OfferedValue]:
    """The company's own page, read: what its organisation says, then its icons."""
    if not answers:
        return []
    page = answers[0]
    base = page.url or subject.website
    # The icons first, in the order *Find logo* tries them: a source offers forty values at
    # most, and a page listing many profiles must not push its favicon out.
    return [*logos(page.text, base), *organisation(page.text, base)]
