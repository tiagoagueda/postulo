"""What Postulo believes from whatever sits in front of it.

A reverse proxy tells the application about the connection it terminated, in headers the
application cannot verify: ``X-Forwarded-Proto`` says the client arrived over HTTPS,
``X-Forwarded-For`` says which address they came from. Both are ordinary request headers,
so anybody who can reach Postulo directly can send them and be believed.

Postulo used to set ``SECURE_PROXY_SSL_HEADER`` unconditionally, which is Django's way of
saying "believe ``X-Forwarded-Proto``" and which Django's own documentation warns about in
as many words. The assumption underneath was that a proxy is always in front. Plenty of
self-hosted instances will not have one: the Compose file publishes a port, and exposing
it directly is a normal thing for somebody to do on their own network.

So the headers are believed **from a proxy and nowhere else**, and the question of what
counts as a proxy has an answer that fits how these instances are actually run: something
on a private network. A reverse proxy in the same Compose project, on the same LAN or on
the same host is at a private address; a request arriving straight off the internet is
not. ``POSTULO_TRUSTED_PROXIES`` names the ranges, so an operator whose proxy sits
somewhere else can say where.

Believing ``X-Forwarded-For`` when it comes from a proxy is the other half, and it is not
only tidiness. Rate limits key on ``REMOTE_ADDR``, which behind a proxy is the proxy for
everybody — so a limit meant to slow one persistent stranger down would be shared by every
person on the instance, and a stranger could exhaust it for all of them.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from django.conf import settings

#: Where a reverse proxy lives on a self-hosted instance, unless told otherwise. Loopback
#: for a proxy on the same host, the three private IPv4 ranges for Docker and for a LAN,
#: and their IPv6 equivalents.
DEFAULT_TRUSTED_PROXIES = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)

#: Headers a proxy sets about the connection it terminated. Removed outright when the
#: request did not come from one, so nothing downstream has to remember to be suspicious.
FORWARDING_HEADERS = (
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_FORWARDED",
)


def _networks(values: Iterable[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            # A typo in one entry must not quietly widen what is trusted, and must not
            # take the instance down either. Skipping it is the safe direction: the
            # header goes unbelieved rather than believed from the wrong place.
            continue
    return networks


def trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return _networks(getattr(settings, "POSTULO_TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES))


def is_trusted(address: str) -> bool:
    """Whether ``address`` is one Postulo will accept forwarding headers from."""
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return any(parsed in network for network in trusted_networks())


def client_address(remote_addr: str, forwarded_for: str) -> str:
    """The address the request really came from, given a trusted proxy said so.

    ``X-Forwarded-For`` is a list that grows on the left as it passes through proxies, so
    the client is the leftmost entry and the nearest proxy is the rightmost. Only the part
    on the right can be relied on, because the client is free to send whatever prefix it
    likes: the answer is the first address from the right that is not itself a trusted
    proxy. With nothing usable there the peer address stands, which is what happens today.
    """
    for candidate in reversed([part.strip() for part in forwarded_for.split(",")]):
        if not candidate:
            continue
        # A port may be appended to an IPv6 address in brackets, or to an IPv4 one.
        if candidate.startswith("[") and "]" in candidate:
            candidate = candidate[1 : candidate.index("]")]
        elif candidate.count(":") == 1:
            candidate = candidate.split(":")[0]
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return remote_addr
        if not is_trusted(candidate):
            return candidate
    return remote_addr


class TrustedProxyMiddleware:
    """Strip forwarding headers that did not come from a proxy, and honour those that did.

    First in the chain, so nothing else ever sees a header this would have removed —
    ``SecurityMiddleware`` reads ``X-Forwarded-Proto`` through ``SECURE_PROXY_SSL_HEADER``
    to decide whether to redirect, and it runs second.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote_addr = request.META.get("REMOTE_ADDR", "")
        if is_trusted(remote_addr):
            forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if forwarded_for:
                # Kept under its own name as well: something may want to know the request
                # was forwarded, and the original list is evidence rather than a claim.
                request.META["REMOTE_ADDR"] = client_address(remote_addr, forwarded_for)
                request.META["POSTULO_PROXY_ADDR"] = remote_addr
        else:
            for header in FORWARDING_HEADERS:
                request.META.pop(header, None)
        return self.get_response(request)
