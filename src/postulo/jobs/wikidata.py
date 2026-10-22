"""Reading one Wikidata item a person already named, when they press for it (#673, #701).

A company can carry a Wikidata identifier (#42). This is the one place that asks Wikidata
about it: one ``GET https://www.wikidata.org/wiki/Special:EntityData/<Q>.json``, through the
public-only client (rule 5 of the threat model), with a deadline, a body limit, at most three
redirects (a merged item redirects to the one it was merged into), each held to
``www.wikidata.org``, and Wikimedia's user-agent form. Never on a page load, a save, a merge
or a schedule: a person presses *Fetch from Wikidata*, sees what came back, and keeps it or
not.

**What it reads, and what it keeps of it.** (#941: also whether the item ended -- P576 -- and
which item replaced it -- P1366 -- which the review page only *offers*; a company's legal state
is never changed unseen.) The item's labels, in the languages Postulo
speaks (a label in a language nobody here can choose is a name nobody prints), with Wikidata's
``mul`` label standing in for a language that has none of its own. Wikidata is edited by
anyone, so each label is treated as untrusted text: normalised, stripped of control and
bidirectional-formatting characters, cut to the column's length, stored as plain text and
drawn escaped.

**The request says what it is about.** It names the organisation the person is looking at,
from the instance's address: acceptable because it is pressed for, one item at a time, to a
fixed public host, with an instance switch (the ``wikidata`` feature) to turn it off and a
per-person allowance (``POSTULO_WIKIDATA_RATE``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlsplit

import httpx
from django.utils.translation import gettext as _

from postulo import __version__
from postulo.core import languages
from postulo.plugins import http

HOST = "www.wikidata.org"
URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
#: An item is some tens of kilobytes; the University of Aveiro's is 50 KB. A megabyte is a
#: document nobody meant.
MAX_BYTES = 1024 * 1024
SECONDS = 15.0
MAX_REDIRECTS = 3
#: How long a name may be, as `CompanyName.name` holds it.
MAX_NAME = 200

_QID = re.compile(r"Q[1-9][0-9]{0,11}")


class WikidataRefused(Exception):
    """Why nothing came back, in a sentence a person can read."""


@dataclass
class Item:
    """What one item said that Postulo keeps: its names, and where it is (#701)."""

    qid: str
    names: dict[str, str] = field(default_factory=dict)
    #: ``(latitude, longitude)`` from the preferred, or the only, coordinate location (P625).
    point: tuple[float, float] | None = None
    #: Whether the item says the organisation ended (P576), and the day when it says one
    #: (#941). Only offered for confirmation; nothing here changes a company by itself.
    dissolved: bool = False
    dissolved_on: date | None = None
    #: The items that replaced it (P1366), as identifiers.
    replaced_by: list[str] = field(default_factory=list)


def user_agent() -> str:
    """Wikimedia's form: the client, its version, how to reach its makers, the library."""
    return (
        f"Postulo/{__version__} (https://source.tiagoagueda.com/postulo/postulo) "
        f"httpx/{httpx.__version__}"
    )


def clean(text) -> str:
    """A label as plain text: normalised, no control or formatting characters, one line."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    kept = "".join(
        " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in text
    )
    return " ".join(kept.split())[:MAX_NAME]


def names_from(labels: dict) -> dict[str, str]:
    """The labels in the languages Postulo speaks, keyed by canonical tag.

    A label in ``mul`` -- Wikidata's default for every language -- stands for a language
    Postulo speaks that has none of its own, under that language's base tag.
    """
    spoken = list(languages.NATIVE_NAMES)
    found: dict[str, str] = {}
    for code, entry in (labels or {}).items():
        if code == "mul" or not isinstance(entry, dict):
            continue
        tag = languages.tag(code)
        if not languages.well_formed(tag) or not languages.match(tag, spoken):
            continue
        name = clean(entry.get("value"))
        if name:
            found[tag] = name
    default = (
        clean((labels or {}).get("mul", {}).get("value"))
        if isinstance((labels or {}).get("mul"), dict)
        else ""
    )
    if default:
        for tag in spoken:
            base = languages.primary(tag)
            if base and not any(languages.primary(have) == base for have in found):
                found.setdefault(base, default)
    return found


#: The Earth, as Wikidata names the globe a coordinate is on; a crater on the Moon is not a
#: place on the map.
EARTH = "http://www.wikidata.org/entity/Q2"


def point_from(claims: dict) -> tuple[float, float] | None:
    """Where the item is, from its coordinate location (P625), or ``None`` (#701).

    The statement ranked *preferred*, or the only one there is; none, or several with none
    preferred, is nothing rather than a guess. A headquarters (P159) is another item and
    another request, and is not followed.
    """
    statements = [
        row
        for row in (claims.get("P625") if isinstance(claims, dict) else None) or []
        if isinstance(row, dict) and row.get("rank") != "deprecated"
    ]
    preferred = [row for row in statements if row.get("rank") == "preferred"]
    chosen = preferred if preferred else statements
    if len(chosen) != 1:
        return None
    value = (((chosen[0].get("mainsnak") or {}).get("datavalue") or {}).get("value")) or {}
    if not isinstance(value, dict) or value.get("globe", EARTH) != EARTH:
        return None
    try:
        lat, lon = float(value["latitude"]), float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


_TIME = re.compile(r"\+(\d{4})-(\d{2})-(\d{2})T")


def _live(statements) -> list[dict]:
    """The statements of a property that are not deprecated."""
    return [
        row for row in statements or [] if isinstance(row, dict) and row.get("rank") != "deprecated"
    ]


def dissolved_from(claims: dict) -> tuple[bool, date | None]:
    """Whether the item has a *dissolved, abolished or demolished* date (P576), and the day.

    The day only where there is exactly one statement and it is precise to the day (#941):
    a year is not a day, and a company is not dissolved on January 1st because a year was
    all Wikidata knew. An item with the property but no usable day is still dissolved.
    """
    statements = _live(claims.get("P576") if isinstance(claims, dict) else None)
    if not statements:
        return False, None
    if len(statements) != 1:
        return True, None
    value = (((statements[0].get("mainsnak") or {}).get("datavalue") or {}).get("value")) or {}
    if not isinstance(value, dict) or not isinstance(value.get("time"), str):
        return True, None
    match = _TIME.match(value["time"])
    if not match or value.get("precision") != 11:
        return True, None
    try:
        return True, date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return True, None


def replaced_by_from(claims: dict) -> list[str]:
    """The items that replaced this one (P1366), as ``Q`` identifiers, at most three."""
    found: list[str] = []
    for row in _live(claims.get("P1366") if isinstance(claims, dict) else None):
        value = (((row.get("mainsnak") or {}).get("datavalue") or {}).get("value")) or {}
        qid = value.get("id") if isinstance(value, dict) else None
        if isinstance(qid, str) and _QID.fullmatch(qid) and qid not in found:
            found.append(qid)
    return found[:3]


def fetch(qid: str) -> Item:
    """Ask Wikidata for one item. Raises `WikidataRefused` with the reason."""
    qid = (qid or "").strip().upper()
    if not _QID.fullmatch(qid):
        raise WikidataRefused(_("That is not a Wikidata item's identifier."))
    document = _get(URL.format(qid=qid))
    entities = document.get("entities") if isinstance(document, dict) else None
    if not isinstance(entities, dict) or not entities:
        raise WikidataRefused(_("Wikidata's answer was not an item."))
    # A merged item answers with the item it was merged into, under that one's identifier.
    found_qid, entity = next(iter(entities.items()))
    if not isinstance(entity, dict):
        raise WikidataRefused(_("Wikidata's answer was not an item."))
    claims = entity.get("claims") or {}
    dissolved, dissolved_on = dissolved_from(claims)
    return Item(
        qid=str(found_qid),
        names=names_from(entity.get("labels") or {}),
        point=point_from(claims),
        dissolved=dissolved,
        dissolved_on=dissolved_on,
        replaced_by=replaced_by_from(claims),
    )


def _get(url: str) -> dict:
    deadline = http.deadline_in(SECONDS)
    current = url
    try:
        with (
            http.public_only_client(
                timeout=SECONDS,
                follow_redirects=False,
                headers={"User-Agent": user_agent(), "Accept": "application/json"},
            ) as client,
            http.until(deadline),
        ):
            for _hop in range(MAX_REDIRECTS + 1):
                if urlsplit(current).scheme != "https" or urlsplit(current).hostname != HOST:
                    raise WikidataRefused(_("Wikidata sent the request somewhere else."))
                with client.stream(
                    "GET", current, headers={"Accept-Encoding": http.ACCEPT_ENCODING}
                ) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("Location", ""))
                        continue
                    if response.status_code == 404:
                        raise WikidataRefused(_("Wikidata has no item with that identifier."))
                    if response.status_code == 429:
                        raise WikidataRefused(
                            _("Wikidata asked for fewer requests. Try again later.")
                        )
                    if response.status_code != 200:
                        raise WikidataRefused(
                            _("Wikidata answered %(status)s.") % {"status": response.status_code}
                        )
                    body = http.read_body(response, limit=MAX_BYTES, deadline=deadline)
                    break
            else:
                raise WikidataRefused(_("Wikidata redirected too many times."))
    except WikidataRefused:
        raise
    except http.DestinationRefused as error:
        raise WikidataRefused(str(error)) from error
    except http.BodyTooLarge as error:
        raise WikidataRefused(_("Wikidata's answer was larger than an item should be.")) from error
    except (http.BodyTooSlow, httpx.TimeoutException) as error:
        raise WikidataRefused(_("Wikidata took too long to answer.")) from error
    except http.BodyRefused as error:
        raise WikidataRefused(str(error)) from error
    except httpx.HTTPError as error:
        raise WikidataRefused(_("Wikidata could not be reached.")) from error
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WikidataRefused(_("Wikidata's answer was not an item.")) from error
