"""One HTTP client for every plugin that talks to another service.

Plugins do not roll their own: timeouts, size limits, a user agent and the destination
policy come from here, and so does the one place the policy can be reasoned about.

Capture refuses private addresses outright, because the URL came from a stranger's page.
A connection is different: the destination is what the person typed, and self-hosted
services — a Paperless on the same LAN, a mail server in the same Compose network — are
exactly where private addresses live. So the *operator* decides, once, with
``POSTULO_CONNECTIONS_ALLOW_PRIVATE``. Off by default, and the check runs on every
request the client makes, redirects included, so a public hostname cannot bounce a plugin
onto the router's administration page.
"""

from __future__ import annotations

import httpx
from django.conf import settings

from .fetching import USER_AGENT, UnsafeURL, public_addresses_for, validate_public_url

DEFAULT_TIMEOUT = 10.0
MAX_REDIRECTS = 3


class DestinationRefused(Exception):
    """The address is private and the operator has not allowed private destinations."""


def private_destinations_allowed() -> bool:
    return bool(getattr(settings, "POSTULO_CONNECTIONS_ALLOW_PRIVATE", False))


def check_destination(url: str) -> None:
    """Raise unless ``url`` may be reached under the instance's policy."""
    if private_destinations_allowed():
        return
    try:
        validate_public_url(url)
    except UnsafeURL as exc:
        raise DestinationRefused(
            f"{exc} Connections may only reach private or local addresses when the operator "
            "sets POSTULO_CONNECTIONS_ALLOW_PRIVATE=true."
        ) from exc


def _refused(exc: UnsafeURL) -> DestinationRefused:
    """The connection policy's wording for an address the public check turned down."""
    return DestinationRefused(
        f"{exc} Connections may only reach private or local addresses when the operator "
        "sets POSTULO_CONNECTIONS_ALLOW_PRIVATE=true."
    )


def _guard(request: httpx.Request) -> None:
    """Approve the destination, and then connect to the address that was approved.

    Checking and connecting have to be one act, which is rule 5 of `docs/THREAT-MODEL.md`.
    `check_destination` resolved the name and threw the answer away, and httpx then resolved
    it again to open the socket: a name with a one-second lifetime is free to answer publicly
    for the check and with `127.0.0.1` or a metadata address a moment later, and the check
    would have passed on an address nothing ever contacted.

    Where the operator has allowed private destinations there is nothing left to enforce --
    every address passes -- so the name is left for httpx to resolve as it always did. That
    keeps self-hosted setups working the way they do today, including the ones whose names
    resolve differently inside a Compose network.
    """
    if private_destinations_allowed():
        return
    original = request.headers.get("Host")
    try:
        addresses = public_addresses_for(str(request.url))
    except UnsafeURL as exc:
        raise _refused(exc) from exc
    _pin(request, addresses[0])
    if original:
        request.headers["Host"] = original


def approve_host(host: str) -> str:
    """The address to dial for a plugin that speaks something other than HTTP.

    A mailbox, a message queue, anything with a socket of its own: resolve the name, hold
    every address it answers with to the instance's policy, and hand back the one to connect
    to. Keep the *name* for TLS — the certificate is proved against what somebody typed, and
    a certificate checked against a number never matches.

    Raises :class:`DestinationRefused`, whose message is for the person who typed the host.
    """
    from postulo.core import destinations

    try:
        return str(destinations.approve(host, allow_private=private_destinations_allowed()))
    except (destinations.Refused, destinations.Unresolvable) as error:
        raise DestinationRefused(str(error)) from error


def _pin(request: httpx.Request, address) -> None:
    """Send this request to ``address``, while still speaking to the host it names.

    Rewriting the URL is what makes the connection go to the address that was actually
    approved. The ``Host`` header and the TLS server name keep the original hostname, so
    the site sees the request it expects and the certificate is checked against the name
    a person typed rather than against a number.
    """
    host = request.url.host
    if host == str(address):
        return
    request.extensions = {**request.extensions, "sni_hostname": host}
    request.url = request.url.copy_with(host=str(address))


def _public_only(request: httpx.Request) -> None:
    """Refuse anything not publicly routable, and connect to what was approved.

    ``check_destination`` exists for connections, where a private address is the whole
    point. Some things Postulo fetches are public by definition — a portfolio address a
    recruiter will click, a job posting from a stranger's page — and for those the
    operator's connection policy is not the question being asked.

    The hook runs on every request the client makes, redirects included, and each time it
    resolves the name, approves every address it answers with, and then pins the request
    to one of them. Checking and connecting have to be one act: two lookups leave a gap
    that a short-lived record can answer differently.
    """
    original = request.headers.get("Host")
    try:
        addresses = public_addresses_for(str(request.url))
    except UnsafeURL as exc:
        raise DestinationRefused(str(exc)) from exc
    _pin(request, addresses[0])
    if original:
        request.headers["Host"] = original


def _build(guard, timeout: float, kwargs: dict) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    hooks = kwargs.pop("event_hooks", {})
    hooks = {**hooks, "request": [guard, *hooks.get("request", [])]}
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("max_redirects", MAX_REDIRECTS)
    return httpx.Client(timeout=timeout, headers=headers, event_hooks=hooks, **kwargs)


def client(*, timeout: float = DEFAULT_TIMEOUT, **kwargs) -> httpx.Client:
    """An ``httpx.Client`` with Postulo's defaults and the destination policy attached.

    Use it as a context manager. Extra keyword arguments go to ``httpx.Client``; a
    plugin needing basic auth, for instance, passes ``auth=``.
    """
    return _build(_guard, timeout, kwargs)


def public_only_client(*, timeout: float = DEFAULT_TIMEOUT, **kwargs) -> httpx.Client:
    """Like :func:`client`, but private addresses are refused however the instance is set.

    The hook runs on every request the client makes rather than once on the address it
    was handed, which is the part that matters: a public host is perfectly free to answer
    ``302 Location: http://127.0.0.1:9000/``, and a client following that redirect on its
    own would make the request before anything had a chance to object. It also pins each
    request to an address that passed, so the connection cannot land somewhere the check
    never saw.
    """
    return _build(_public_only, timeout, kwargs)
