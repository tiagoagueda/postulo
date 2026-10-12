"""Reading one Wikidata item a person already named, when they press for it (#673, #701).

A company can carry a Wikidata identifier (#42). This is the one place that asks Wikidata
about it: one ``GET https://www.wikidata.org/wiki/Special:EntityData/<Q>.json``, through the
public-only client (rule 5 of the threat model), with a deadline, a body limit, at most three
redirects (a merged item redirects to the one it was merged into), each held to
``www.wikidata.org``, and Wikimedia's user-agent form. Never on a page load, a save, a merge
or a schedule: a person presses *Fetch from Wikidata*, sees what came back, and keeps it or
not.

**What it reads, and what it keeps of it.** The item's labels, in the languages Postulo
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
    return Item(qid=str(found_qid), names=names_from(entity.get("labels") or {}))


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
