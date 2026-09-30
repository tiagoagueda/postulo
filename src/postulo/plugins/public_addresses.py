"""Whether an address is one Postulo may dial: the check, and nothing that dials.

Every address a hostname resolves to has to be publicly routable -- loopback, private,
link-local, shared and reserved ranges are all refused, and so is an IPv6 address carrying one
of them -- and the addresses come back to the caller, which connects to one of them rather
than resolving the name again. `fetching` applies it to a page somebody pasted, and `http` to
every request a plugin's client makes. What counts as public is `core.destinations.is_public`,
which the guard for mail servers asks too (#321).

It was the top of `fetching`, which meant `http` imported the module that fetches pages in
order to check an address, while `fetching` imported `http` for its client: a pair held
apart by an import inside a function (#248). Here it needs the standard library, the error a
capture raises and the verdict from `core.destinations`, a layer below, and `fetching` still
hands the same names out.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from django.utils.translation import gettext as _

from postulo.core.destinations import is_public

from .base import CaptureError

USER_AGENT = "Postulo (+https://source.tiagoagueda.com/postulo/postulo)"


class UnsafeURL(CaptureError):
    """The URL points somewhere Postulo will not go."""


def _addresses_for(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        # `UnicodeError`: the name is encoded before it is looked up, and one with an empty
        # label or one over 63 characters cannot be. Nothing answers to such a name either.
        raise UnsafeURL(_("That hostname could not be resolved.")) from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def public_addresses_for(url: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address ``url``'s host answers with, once they have all been approved.

    Every resolved address is checked, not just the first: a hostname answering with one
    public and one private address would otherwise be a way through.

    The list comes back rather than being thrown away because the caller has to *connect*
    to one of these. Resolving again at connection time is the gap this closes: a name
    with a one-second lifetime is free to answer with a public address for the check and
    a private one a moment later, and the check would have passed on an address nobody
    ever contacted.
    """
    try:
        parts = urlparse(url.strip())
        # Read here, where a refusal has a sentence: urllib raises for a bracket without its
        # partner as it parses, and for a port that is not a number in range only when asked.
        host, _port = parts.hostname, parts.port
    except ValueError as exc:
        raise UnsafeURL(_("That does not look like a complete web address.")) from exc

    if parts.scheme not in {"http", "https"}:
        raise UnsafeURL(_("Only http and https addresses can be captured."))
    if not host:
        raise UnsafeURL(_("That does not look like a complete web address."))

    addresses = _addresses_for(host)
    if not addresses:
        raise UnsafeURL(_("That hostname could not be resolved."))
    if not all(is_public(address) for address in addresses):
        raise UnsafeURL(
            _(
                "That address is on a private or local network, and Postulo will not "
                "fetch it. Paste the posting text in by hand instead."
            )
        )
    return addresses


def validate_public_url(url: str) -> str:
    """Check a URL is one Postulo is willing to fetch, and return it normalised."""
    public_addresses_for(url)
    return urlunparse(urlparse(url.strip()))
