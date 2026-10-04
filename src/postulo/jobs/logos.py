"""A company's logo: fetched once from an address, found on their site, or uploaded.

The production policy is ``img-src 'self'``, and that is not an obstacle to work around —
it is the reason this module exists. An ``<img>`` pointing at somebody else's server would
tell them, on every page view, which companies this person is applying to and when they
looked. So "from a URL" cannot mean "show the URL": Postulo fetches the image **once**,
from the server, re-encodes it, keeps it under private media and serves it itself.

Everything else follows from that:

* the fetch goes through the same guard capture uses — public addresses only, revalidated
  on redirect — because the address came from a page or from typing, and a job tracker
  must not be a way to make a server visit a router's administration page;
* the bytes are decoded and re-encoded rather than stored as they arrived, which drops
  whatever metadata the file carried;
* **SVG is kept as SVG, sanitised** (#264). It is the format a logo most often comes in,
  and the one that needs care: it is a document rather than a picture, and a direct visit
  to the stored file is not the ``<img>`` context where a browser refuses to run what it
  carries. `core.pictures.sanitise_svg` is the allowlist, and `jobs:company_logo` is
  hardened as well — the two are halves of one defence and neither is enough alone;
* **a logo is bounded by its file size, not by its dimensions** (#264). It used to be
  flattened onto a 256-pixel square, which was enough for every surface that exists today
  and a ceiling on every surface nobody has built — a company header, a mark on a printed
  report. The original bytes are not kept, so that ceiling would have been found years
  later by a design that could not have what it needed.

A company with no logo shows an initials tile, exactly as a person with no picture does.
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.core import pictures, throttle
from postulo.plugins import fetching, http

logger = logging.getLogger(__name__)

#: Anything larger than this is not a logo, and is refused before it is decoded. Raised
#: from two megabytes with #264: the output is no longer fixed at 256 pixels square, so the
#: input no longer has to be small enough that it would survive being flattened to one.
MAX_BYTES = 5 * 1024 * 1024

#: What is written down, after re-encoding. A flat wordmark at a thousand pixels is well
#: under this, so the reduction in `encode_within` effectively never runs -- the budget is
#: here to stop one file being unreasonable, not to decide how large a logo may be (#264).
MAX_STORED_BYTES = 1024 * 1024

#: Re-exported so the rest of this module and its tests read one name for it. The guard
#: itself is `core.pictures`': it bounds what decoding allocates, not what is stored.
MAX_PIXELS = pictures.MAX_PIXELS

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/svg+xml",
    }
)

#: How long a fetch may take. A logo is not worth waiting on.
TIMEOUT = 8.0

#: How long a whole download may take, redirects included, where `TIMEOUT` is each wait for
#: the network and a server dribbling a byte every few seconds never trips it (#321). Twenty
#: seconds reads the whole of `MAX_BYTES` at 260 KB/s, and a logo is usually tens of
#: kilobytes. Every wait inside it is cut to what is left (`http.until`), so a download is
#: over by then, the name lookups aside.
DOWNLOAD_SECONDS = 20.0


class UnusableLogo(ValueError):
    """The bytes are not an image Postulo will keep, and the message says why."""


def process(data: bytes) -> tuple[ContentFile, str]:
    """The bytes as Postulo will keep them, and the extension to keep them under.

    An SVG is sanitised and stays an SVG — it is a vector, and flattening a vector to
    pixels to store it would throw away the reason it is the better file. Anything else is
    decoded and written out again as PNG, at its own size, within the budget.

    **The square padding is not lost, it moved to CSS.** `{% company_logo %}` renders
    `object-contain` inside a square box at every call site, so a wide wordmark letterboxes
    in the browser exactly as it letterboxed in the file, and nothing in the layout changes
    (#264).
    """
    if pictures.looks_like_svg(data):
        return ContentFile(_svg(data)), "svg"
    try:
        return ContentFile(pictures.as_stored(data, budget=MAX_STORED_BYTES)), "png"
    except pictures.UnusablePicture as error:
        raise UnusableLogo(str(error)) from error


def _svg(data: bytes) -> bytes:
    try:
        return pictures.sanitise_svg(data)
    except pictures.UnusablePicture as error:
        raise UnusableLogo(str(error)) from error


def download(url: str) -> bytes:
    """One guarded request for one image. Raises :class:`UnusableLogo` with the reason.

    The *public-only* client, not the connection one: a company's logo lives on the open web
    by definition, so the operator's decision to let **connections** reach a Paperless on the
    LAN says nothing about this. It is the same argument `resume/links.py` makes about a
    portfolio address, and it is what stops a hostile site redirecting this fetch onto the
    network the server sits in (#215). The client checks every hop and connects to the
    address it checked, so there is no separate check to make first.

    Redirects are followed here rather than by httpx, which reads each redirect's body whole
    before following it; every hop is still a request of its own, so the client's hook still
    checks each one. The image is streamed and read only as far as `MAX_BYTES`, within
    `DOWNLOAD_SECONDS` (#321).
    """
    deadline = http.deadline_in(DOWNLOAD_SECONDS)
    current = url
    try:
        with (
            http.public_only_client(
                timeout=TIMEOUT,
                follow_redirects=False,
                headers={"Accept-Encoding": http.ACCEPT_ENCODING},
            ) as client,
            http.until(deadline),
        ):
            for _hop in range(http.MAX_REDIRECTS + 1):
                if http.past(deadline):
                    raise http.BodyTooSlow("The redirects used up the time for the download.")
                try:
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            # Joined to the address asked for: the request's own was pinned
                            # to a number, and the next hop is asked for by name.
                            current = urljoin(current, response.headers["Location"])
                            continue
                        return _image(response, deadline)
                except httpx.TimeoutException as error:
                    if http.past(deadline):
                        # A wait `http.until` cut short before the body began.
                        raise http.BodyTooSlow("The time ran out before the body.") from error
                    raise
            raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.")
    except UnusableLogo:
        raise
    except http.DestinationRefused as error:
        raise UnusableLogo(str(error)) from error
    except http.BodyTooLarge as error:
        raise UnusableLogo(str(_("That file is larger than a logo should be."))) from error
    except http.BodyTooSlow as error:
        raise UnusableLogo(
            ngettext(
                "That file took longer than %(seconds)s second to arrive.",
                "That file took longer than %(seconds)s seconds to arrive.",
                int(DOWNLOAD_SECONDS),
            )
            % {"seconds": int(DOWNLOAD_SECONDS)}
        ) from error
    except http.BodyInAnotherCoding as error:
        raise UnusableLogo(
            str(
                _(
                    "That file was sent with the content encoding “%(coding)s”, which "
                    "Postulo does not unpack."
                )
            )
            % {"coding": error.coding[:40]}
        ) from error
    except Exception as error:
        raise UnusableLogo(
            str(_("Could not be fetched: %(error)s"))
            % {"error": f"{type(error).__name__}: {error}"}
        ) from error


def _image(response, deadline: float) -> bytes:
    """The body of the answer that is not a redirect, if it is an image worth decoding."""
    if response.status_code != 200:
        raise UnusableLogo(
            str(_("The address answered %(code)s.")) % {"code": response.status_code}
        )
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise UnusableLogo(
            str(_("That address is %(type)s, not an image Postulo keeps.")) % {"type": content_type}
        )
    content = http.read_body(response, limit=MAX_BYTES, deadline=deadline)
    if not content:
        raise UnusableLogo(str(_("The address answered with nothing.")))
    return content


# ------------------------------------------------------------------ storing


#: What a logo sets on the company, whether one was found or the last one was thrown away.
#: Written as one list because the two are the same act in opposite directions, and a field
#: on one and not the other is how a cleared logo keeps saying where it came from.
LOGO_FIELDS = ["logo", "logo_source", "logo_source_url", "logo_fetched_at", "updated_at"]


def store(
    company, content: ContentFile, *, source: str, url: str = "", extension: str = "png"
) -> None:
    """Put the image on the company, replacing whatever was there.

    The most recent action wins — a URL, the website, an upload — so there is no
    precedence rule for anybody to learn.

    The database write is here rather than around the view, because the view fetches: the
    page, then up to six images, none of it quick and none of it the instance's business to
    hold the write lock for (#220). The picture is decoded before this is entered, so what
    the transaction covers is one `UPDATE`.
    """
    old = company.logo.name if company.logo else ""
    held = company.logo
    held.save(f"logo-{company.pk}.{extension}", content, save=False)
    new = held.name
    company.logo_source = source
    company.logo_source_url = url[:500]
    company.logo_fetched_at = timezone.now()
    try:
        with transaction.atomic():
            company.save(update_fields=LOGO_FIELDS)
    except BaseException:
        # The row did not take it: the new file is bytes nothing points at, and the old
        # one is still what the row names, so it stays (#525).
        held.storage.delete(new)
        company.logo.name = old
        raise
    if old != new:
        _discard(held.storage, old)


def clear(company) -> None:
    """Take the logo off the company: the row first, the file once the change commits."""
    old = company.logo.name if company.logo else ""
    storage = company.logo.storage
    with transaction.atomic():
        company.logo = ""
        company.logo_source = ""
        company.logo_source_url = ""
        company.logo_fetched_at = None
        company.save(update_fields=LOGO_FIELDS)
    _discard(storage, old)


def _discard(storage, name: str) -> None:
    """Remove a file the row has stopped pointing at, once the change is committed.

    Only when no company row still uses the name -- the rule #217 set for documents.
    """
    if not name:
        return

    def remove() -> None:
        from .models import Company

        if Company.objects.filter(logo=name).exists():
            return
        try:
            storage.delete(name)
        except OSError:  # pragma: no cover - a file already gone is the outcome we wanted
            logger.warning("Could not remove %s from storage", name, exc_info=True)

    transaction.on_commit(remove)


def spend_fetch(owner) -> None:
    """Use one of the owner's fetches, or raise :class:`UnusableLogo` saying why not (#407)."""
    try:
        throttle.fetch(owner)
    except throttle.TooOften as too_often:
        raise UnusableLogo(str(too_often)) from too_often


def from_url(company, url: str, *, spend: bool = True) -> None:
    """Fetch the address, keep the picture. Raises :class:`UnusableLogo` with the reason.

    ``spend=False`` for a caller that has already spent the allowance for this fetch.
    """
    if spend:
        spend_fetch(company.owner)
    content, extension = process(download(url))
    store(company, content, source="url", url=url, extension=extension)


def from_upload(company, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise UnusableLogo(str(_("That file is larger than a logo should be.")))
    content, extension = process(data)
    store(company, content, source="upload", extension=extension)


# ------------------------------------------------- finding one on their site


class _LogoLinks(HTMLParser):
    """The addresses a page offers for its own icon, best first.

    Only what the site declares about itself: an apple-touch-icon, a declared icon, an
    Open Graph image, or the logo in a schema.org Organization block. Nothing is guessed
    from the markup's shape.
    """

    def __init__(self) -> None:
        super().__init__()
        self.apple: list[tuple[int, str]] = []
        self.icons: list[tuple[int, str]] = []
        self.og: list[str] = []
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
                self.og.append(values["content"])
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_ld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_ld and data.strip():
            self.json_ld.append(data)

    def candidates(self) -> list[str]:
        found = [href for _size, href in sorted(self.apple, reverse=True)]
        found += [href for _size, href in sorted(self.icons, reverse=True)]
        found += _organisation_logos(self.json_ld)
        found += self.og
        seen: list[str] = []
        for href in found:
            if href and href not in seen:
                seen.append(href)
        return seen


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
        except ValueError:
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
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def find_on_website(company) -> str:
    """Look on the company's own site for its icon, and keep the first that works.

    The site is the one public repository every company maintains, and the address is one
    the person typed. One page is fetched, under the same rules and the same courtesy
    towards ``robots.txt`` as capture, and then at most a few images.
    """
    website = (company.website or "").strip()
    if not website:
        raise UnusableLogo(str(_("This company has no website recorded.")))
    try:
        page = fetching.fetch_page(website)
    except Exception as error:
        raise UnusableLogo(
            str(_("Could not read %(site)s: %(error)s")) % {"site": website, "error": error}
        ) from error

    parser = _LogoLinks()
    try:
        parser.feed(page.html)
    except Exception:  # pragma: no cover - a broken page is not an error worth showing
        logger.exception("Could not read the markup of %r", website)
    base = page.url or website
    candidates = [urljoin(base, href) for href in parser.candidates()]
    candidates.append(urljoin(base, "/favicon.ico"))

    problems = []
    for candidate in candidates[:6]:
        try:
            content, extension = process(download(candidate))
            store(company, content, source="website", url=candidate, extension=extension)
        except UnusableLogo as error:
            problems.append(str(error))
            continue
        return candidate
    raise UnusableLogo(
        str(_("Nothing on %(host)s could be used as a logo."))
        % {"host": urlsplit(base).hostname or website}
    )
