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

from .fetching import USER_AGENT, UnsafeURL, validate_public_url

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


def _guard(request: httpx.Request) -> None:
    check_destination(str(request.url))


def _public_only(request: httpx.Request) -> None:
    """Refuse anything that is not publicly routable, whatever the operator allowed.

    ``check_destination`` exists for connections, where a private address is the whole
    point. Some things Postulo fetches are public by definition — a portfolio address a
    recruiter will click — and for those the operator's connection policy is not the
    question being asked.
    """
    try:
        validate_public_url(str(request.url))
    except UnsafeURL as exc:
        raise DestinationRefused(str(exc)) from exc


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
    own would make the request before anything had a chance to object.
    """
    return _build(_public_only, timeout, kwargs)
