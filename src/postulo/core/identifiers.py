"""What every kind of external identifier knows about itself.

A company has a Wikidata item or a legal-entity identifier; a person has an ORCID. The
things being identified have nothing in common, but the machinery does: tidy what somebody
pasted, say whether the result is well-formed, and know where it links. That machinery
lives here so both registries are the same shape, and so neither app has to depend on the
other to get it.

Nothing here touches the network. The person typing an identifier knows what they typed,
and looking it up somewhere else is a deliberate act for another day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Scheme:
    key: str
    label: str
    #: What a well-formed value looks like, applied after normalisation.
    pattern: re.Pattern[str]
    #: Where the value links, with ``{value}`` standing for it; empty when there is nowhere.
    link: str = ""
    #: Shown beside the input.
    example: str = ""
    #: Path prefixes that identify a pasted URL of this scheme, so the slug can be lifted.
    url_paths: tuple[str, ...] = ()
    #: The hosts those URLs live on; an address elsewhere is not an identifier of this kind.
    hosts: tuple[str, ...] = ()
    #: Whether letters are folded to upper case.
    upper: bool = False

    def url_for(self, value: str) -> str:
        return self.link.format(value=value) if self.link else ""


def hosted_by(url: str, scheme: Scheme) -> bool:
    """Whether ``url`` is on one of the scheme's own hosts.

    Checked before anything is lifted out of a pasted address, so a link on somebody
    else's site cannot be read as an identifier of this kind.
    """
    host = (urlsplit(url).hostname or "").lower()
    return any(host == known or host.endswith("." + known) for known in scheme.hosts)


def value_from_url(url: str, scheme: Scheme, *, segments: int = 1) -> str | None:
    """The identifier inside a pasted URL, or nothing if it is not one of the scheme's."""
    if "://" not in url or not scheme.url_paths or not hosted_by(url, scheme):
        return None
    parts = urlsplit(url)
    # GLEIF keeps the record in the fragment; everyone else in the path.
    haystack = parts.path + ("#" + parts.fragment if parts.fragment else "")
    for prefix in scheme.url_paths:
        if prefix in haystack:
            tail = haystack.split(prefix, 1)[1].strip("/")
            return "/".join(tail.split("/")[:segments])
    return None
