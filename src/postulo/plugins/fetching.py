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
  exceeds the size limit -- read as it streams in, so the limit is on what is held rather
  than on what was already held by the time anybody measured it (#321).

``robots.txt`` is honoured. A person capturing a posting they are looking at is not a
crawler, but Postulo is not in a position to prove that to the site, and one page fetch
is not worth an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import http
from .base import CaptureError

# The address check lives below both this module and `http`, which each need it (#248). The
# names are handed out here too, because this is where the capture code, the links on a CV and
# the server's own page have always found them.
from .public_addresses import (  # noqa: F401 - re-exported: resume.links, server_views, tests
    USER_AGENT,
    UnsafeURL,
    public_addresses_for,
    validate_public_url,
)

#: Generous for an advert, mean for anything that is not one.
MAX_BYTES = 2_000_000
#: The longest any one wait for the network may take.
TIMEOUT_SECONDS = 10.0
#: The longest the whole page may take -- every redirect and every byte -- where
#: `TIMEOUT_SECONDS` is only each wait, and a server sending a byte every nine seconds would
#: never trip it (#321). Thirty seconds reads the whole two megabytes at 67 KB/s, slower than
#: any connection that can open a web page. Every wait inside it is cut to what is left
#: (`http.until`), so a fetch is over by then, the name lookups aside.
DOWNLOAD_SECONDS = 30.0
MAX_REDIRECTS = 3
ROBOTS_TIMEOUT_SECONDS = 5.0
#: robots.txt gets its own, shorter budget, spent before the page's begins.
ROBOTS_DOWNLOAD_SECONDS = 10.0
#: RFC 9309 §2.5 asks a crawler to read at least this much of a robots.txt. What comes after
#: it is not read, and the rules before it are kept, as the large crawlers do.
ROBOTS_MAX_BYTES = 500 * 1024


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

    try:
        parts = urlparse(url)
    except ValueError:
        # `http://[::1` and the like. `fetch_page` refuses such an address before it asks;
        # a caller that did not gets the answer an unreadable robots.txt gets.
        return True
    robots_url = urlunparse((parts.scheme, parts.netloc, "/robots.txt", "", "", ""))

    owned_client = client is None
    if client is None:
        # Guarded, not bare. Every caller today passes the client `fetch_page` opened, but
        # this function is public in a plugin-facing module, and one that reached robots.txt
        # on an address nothing had approved would be a way round the whole policy (#215).
        # Not following redirects, as `fetch_page`'s client does not: httpx reads every
        # redirect's body whole before it follows one, and a redirected robots.txt has
        # always counted as none.
        client = http.public_only_client(timeout=ROBOTS_TIMEOUT_SECONDS, follow_redirects=False)
    # The encodings on the request, not the client, so they hold whichever client it is:
    # httpx would offer brotli, which `read_body` refuses, and a refusal allows everything.
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": http.ACCEPT_ENCODING}
    try:
        # Streamed and capped like the page: the server that answers for robots.txt is the
        # same stranger's, and it is asked first (#321).
        deadline = http.deadline_in(ROBOTS_DOWNLOAD_SECONDS)
        with http.until(deadline), client.stream("GET", robots_url, headers=headers) as response:
            if response.status_code != 200:
                return True
            body, cut = http.read_start(response, limit=ROBOTS_MAX_BYTES, deadline=deadline)
        if cut:
            # The last line may be half a rule, and half of "Disallow: /private/" says
            # something else entirely.
            body = body[: body.rfind(b"\n") + 1]
        parser = RobotFileParser()
        parser.parse(_text(body, response).splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        # A site that cannot serve its own robots.txt has not disallowed anything.
        return True
    finally:
        if owned_client:
            client.close()


def _too_slow() -> FetchFailed:
    return FetchFailed(
        ngettext(
            "That page took longer than %(seconds)s second to arrive, so it was not read.",
            "That page took longer than %(seconds)s seconds to arrive, so it was not read.",
            int(DOWNLOAD_SECONDS),
        )
        % {"seconds": int(DOWNLOAD_SECONDS)}
    )


def _not_fetched(error: Exception) -> FetchFailed:
    return FetchFailed(
        _("That page could not be fetched: %(reason)s") % {"reason": str(error)[:200]}
    )


def _text(body: bytes, response: httpx.Response) -> str:
    """``body`` as text, in the character set the site named where that is one.

    The name is the site's to choose, and Python knows codecs that are not character sets:
    ``charset=hex`` is a `LookupError` and ``charset=idna`` a `UnicodeError`, whatever the
    error handler. Such a page is read as UTF-8, which is what it would have been read as
    had it named nothing.
    """
    try:
        return body.decode(response.encoding or "utf-8", errors="replace")
    except (LookupError, ValueError):
        return body.decode("utf-8", errors="replace")


def _read_capped(response: httpx.Response, deadline: float) -> str:
    """Read a streamed response, giving up at the size limit or when its time runs out."""
    try:
        body = http.read_body(response, limit=MAX_BYTES, deadline=deadline)
    except http.BodyTooLarge as exc:
        raise FetchFailed(
            _("That page is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // 1_000_000}
        ) from exc
    except http.BodyTooSlow as exc:
        raise _too_slow() from exc
    except http.BodyInAnotherCoding as exc:
        raise FetchFailed(
            _(
                "That page was sent with the content encoding “%(coding)s”, which Postulo "
                "does not unpack, so it was not read."
            )
            % {"coding": exc.coding[:40]}
        ) from exc
    except http.BodyUnreadable as exc:
        raise _not_fetched(exc) from exc
    return _text(body, response)


def fetch_page(url: str) -> FetchedPage:
    """Fetch one page, following redirects by hand so each hop can be checked.

    Every hop is a request of its own on the public-only client, whose hook checks it and
    connects to the address it checked; and every one is streamed, so a redirect's body is
    closed unread and the page's is read only as far as the size limit, all of it within
    `DOWNLOAD_SECONDS` (#321).

    Whatever stops it is a `CaptureError`, with a sentence for the person who asked: that
    is all its callers catch, and the address and every byte of the answer are a stranger's
    to choose. So the refusals that are not one already are turned into one here:

    * the client's hook looks the name up again for every request, and a name that has
      stopped resolving, or begun to answer with a private address, since
      `validate_public_url` looked is refused there as `DestinationRefused` (#607);
    * httpx reads a redirect's ``Location`` before anything here does, and one it cannot
      make an address of (``http:127.0.0.1/x``) is an `httpx.InvalidURL`, which is not an
      `httpx.HTTPError`;
    * an address nothing can parse is a `ValueError` from whichever library met it first:
      urllib for a bracket with no partner, the ``idna`` package inside httpx for a label
      that is not valid punycode.
    """
    try:
        return _fetch(url)
    except http.DestinationRefused as exc:
        raise UnsafeURL(str(exc)) from exc
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise _not_fetched(exc) from exc


def _fetch(url: str) -> FetchedPage:
    current = validate_public_url(url)

    with http.public_only_client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en,*;q=0.5",
            "Accept-Encoding": http.ACCEPT_ENCODING,
        },
    ) as client:
        if not robots_allow(current, client=client):
            raise RobotsDisallowed(
                _(
                    "This site's robots.txt asks automated clients not to fetch that "
                    "page. Copy the posting text in by hand instead."
                )
            )

        deadline = http.deadline_in(DOWNLOAD_SECONDS)
        with http.until(deadline):
            for _hop in range(MAX_REDIRECTS + 1):
                if http.past(deadline):
                    raise _too_slow()
                try:
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            if not location:
                                raise FetchFailed(_("The site redirected without saying where to."))
                            # Revalidate: a public hostname is perfectly free to redirect
                            # inwards. Joined to the address asked for, not to the request's
                            # own: that one was pinned to a number, and a relative redirect
                            # joined to it would ask the next hop for the number rather than
                            # for the site.
                            current = validate_public_url(urljoin(current, location))
                            continue

                        if response.status_code >= 400:
                            raise FetchFailed(_describe_failure(response.status_code))

                        content_type = response.headers.get("content-type", "")
                        if "html" not in content_type.lower():
                            raise FetchFailed(
                                _("That address returned %(kind)s rather than a web page.")
                                % {"kind": content_type.split(";")[0] or _("an unknown file type")}
                            )

                        return FetchedPage(url=current, html=_read_capped(response, deadline))
                except httpx.TimeoutException as exc:
                    if http.past(deadline):
                        # A wait `http.until` cut short, before the body or inside it.
                        raise _too_slow() from exc
                    raise

    raise FetchFailed(_("That address redirected too many times."))
