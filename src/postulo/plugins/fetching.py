"""Fetching a page somebody asked for.

Postulo makes no outbound request on its own. This module runs only when a person pastes
a URL, and it fetches exactly the one page they pasted.

Server-side fetching of a user-supplied URL is how applications get turned into probes of
the network they are running on. Postulo is self-hosted, which usually means it sits on a
home or office network next to a router administration page, a NAS and a hypervisor, so
the constraints below are not theoretical:

* only ``http`` and ``https``;
* every address the hostname resolves to must be publicly routable — loopback, private,
  link-local, shared and reserved ranges are all refused;
* the connection is then made to one of the addresses that was checked, rather than to
  whatever the name resolves to a moment later, so a record with a one-second lifetime
  cannot answer with a public address for the check and a private one for the connection;
* redirects are followed by hand, at most three, revalidating the destination each time,
  because a public hostname is free to redirect to ``127.0.0.1``;
* a response must be HTML, must arrive within the timeout, and is abandoned once it
  exceeds the size limit.

``robots.txt`` is honoured. A person capturing a posting they are looking at is not a
crawler, but Postulo is not in a position to prove that to the site, and one page fetch
is not worth an argument.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from django.utils.translation import gettext as _

from .base import CaptureError

USER_AGENT = "Postulo (+https://source.tiagoagueda.com/postulo/postulo)"

#: Generous for an advert, mean for anything that is not one.
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
ROBOTS_TIMEOUT_SECONDS = 5.0


class UnsafeURL(CaptureError):
    """The URL points somewhere Postulo will not go."""


class RobotsDisallowed(CaptureError):
    """The site asks automated clients not to fetch this page."""


class FetchFailed(CaptureError):
    """The page could not be retrieved."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str


def _describe_failure(status: int) -> str:
    """Say what a refusal means and what to do about it.

    A bare status code is true and useless. The 401 and 403 cases are worth spelling
    out: large employers routinely sit behind bot protection that refuses anything not
    driving a browser, so the page your browser is showing you right now is genuinely
    unreachable from the server — and the answer is to hand Postulo the page rather than
    to try harder at pretending.
    """
    if status in (401, 403):
        return str(
            _(
                "The site refused the request (%(status)s). Large sites often sit behind "
                "bot protection that turns away anything that is not a browser, even "
                "when the page is perfectly visible to you. Paste the page source in "
                "below instead."
            )
            % {"status": status}
        )
    if status == 404:
        return str(_("There is nothing at that address (404). Check the link."))
    if status == 429:
        return str(_("The site asked us to slow down (429). Try again in a few minutes."))
    if status >= 500:
        return str(
            _("The site is having trouble (%(status)s). That is their end, not yours.")
            % {"status": status}
        )
    return str(_("That page returned %(status)s.") % {"status": status})


def _addresses_for(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
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
    parts = urlparse(url.strip())

    if parts.scheme not in {"http", "https"}:
        raise UnsafeURL(_("Only http and https addresses can be captured."))
    if not parts.hostname:
        raise UnsafeURL(_("That does not look like a complete web address."))

    addresses = _addresses_for(parts.hostname)
    if not addresses:
        raise UnsafeURL(_("That hostname could not be resolved."))
    if not all(address.is_global for address in addresses):
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


def robots_allow(url: str, *, client: httpx.Client | None = None) -> bool:
    """Whether the site's robots.txt permits fetching ``url``.

    A missing, unreachable or unparseable robots.txt means yes, which is what the
    standard says and what every other client does.
    """
    # Imported here: the plugins package is the foundation that core builds capture on,
    # and the policy module needs the database models.
    from postulo.core import site

    if site.capture_ignore_robots():
        return True

    parts = urlparse(url)
    robots_url = urlunparse((parts.scheme, parts.netloc, "/robots.txt", "", "", ""))

    owned_client = client is None
    client = client or httpx.Client(timeout=ROBOTS_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        response = client.get(robots_url, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            return True
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        # A site that cannot serve its own robots.txt has not disallowed anything.
        return True
    finally:
        if owned_client:
            client.close()


def _read_capped(response: httpx.Response) -> str:
    """Read a response, giving up once it grows past the size limit."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            raise FetchFailed(
                _("That page is larger than %(limit)s MB, so it was not read.")
                % {"limit": MAX_BYTES // 1_000_000}
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    return body.decode(response.encoding or "utf-8", errors="replace")


def fetch_page(url: str) -> FetchedPage:
    """Fetch one page, following redirects by hand so each hop can be checked."""
    # Imported here: http builds on this module, so importing it at the top would be a
    # cycle. What it provides is the client that pins each request to an address that
    # passed the check, rather than resolving the name a second time to connect.
    from . import http

    current = validate_public_url(url)

    with http.public_only_client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en,*;q=0.5",
        },
    ) as client:
        if not robots_allow(current, client=client):
            raise RobotsDisallowed(
                _(
                    "This site's robots.txt asks automated clients not to fetch that "
                    "page. Copy the posting text in by hand instead."
                )
            )

        for _hop in range(MAX_REDIRECTS + 1):
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise FetchFailed(
                    _("That page could not be fetched: %(reason)s") % {"reason": str(exc)[:200]}
                ) from exc

            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise FetchFailed(_("The site redirected without saying where to."))
                # Revalidate: a public hostname is perfectly free to redirect inwards.
                current = validate_public_url(str(response.next_request.url))
                response.close()
                continue

            if response.status_code >= 400:
                raise FetchFailed(_describe_failure(response.status_code))

            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                raise FetchFailed(
                    _("That address returned %(kind)s rather than a web page.")
                    % {"kind": content_type.split(";")[0] or _("an unknown file type")}
                )

            return FetchedPage(url=current, html=_read_capped(response))

    raise FetchFailed(_("That address redirected too many times."))
