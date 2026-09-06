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
  whatever metadata the file carried and caps its size;
* **raster only** for now: PNG, JPEG, GIF and WebP. SVG is the format logos most often
  come in and the one that needs care — it can carry scripts and references to other
  files, and a direct visit to the file is not the ``<img>`` context where a browser
  refuses to run them. Accepting SVG means a sanitiser, and that is its own step.

A company with no logo shows an initials tile, exactly as a person with no picture does.
"""

from __future__ import annotations

import io
import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from postulo.plugins import fetching, http

logger = logging.getLogger(__name__)

#: The square a logo is brought to. Big enough for the company page, small enough to keep.
LOGO_SIZE = 256

#: Anything larger than this is not a logo, and is refused before it is decoded.
MAX_BYTES = 2 * 1024 * 1024

#: Decoded images above this many pixels are refused: a decompression bomb, or a mistake.
MAX_PIXELS = 40_000_000

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)

#: How long a fetch may take. A logo is not worth waiting on.
TIMEOUT = 8.0


class UnusableLogo(ValueError):
    """The bytes are not an image Postulo will keep, and the message says why."""


def process(data: bytes) -> ContentFile:
    """Decode, fit onto a transparent square, and re-encode as PNG.

    ``contain`` rather than ``fit``: a wordmark is usually wide, and cropping it to a
    square would cut the name in half. The padding is transparent, so the tile still
    lines up with everything else.
    """
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise UnusableLogo(str(_("That image is far larger than a logo needs to be.")))
            image = image.convert("RGBA")
            fitted = ImageOps.contain(image, (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
            square = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 0))
            square.paste(
                fitted,
                ((LOGO_SIZE - fitted.width) // 2, (LOGO_SIZE - fitted.height) // 2),
            )
    except UnusableLogo:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise UnusableLogo(str(_("That file could not be read as an image."))) from error

    out = io.BytesIO()
    square.save(out, format="PNG", optimize=True)
    return ContentFile(out.getvalue())


def download(url: str) -> bytes:
    """One guarded request for one image. Raises :class:`UnusableLogo` with the reason."""
    try:
        fetching.validate_public_url(url)
    except fetching.UnsafeURL as error:
        raise UnusableLogo(str(error)) from error
    try:
        with http.client(timeout=TIMEOUT) as client:
            response = client.get(url)
    except Exception as error:
        raise UnusableLogo(
            str(_("Could not be fetched: %(error)s"))
            % {"error": f"{type(error).__name__}: {error}"}
        ) from error

    if response.status_code != 200:
        raise UnusableLogo(
            str(_("The address answered %(code)s.")) % {"code": response.status_code}
        )
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        if content_type == "image/svg+xml":
            raise UnusableLogo(
                str(_("That is an SVG, which Postulo does not keep yet. A PNG or JPEG works."))
            )
        raise UnusableLogo(
            str(_("That address is %(type)s, not an image Postulo keeps.")) % {"type": content_type}
        )
    if len(response.content) > MAX_BYTES:
        raise UnusableLogo(str(_("That file is larger than a logo should be.")))
    if not response.content:
        raise UnusableLogo(str(_("The address answered with nothing.")))
    return response.content


# ------------------------------------------------------------------ storing


def store(company, content: ContentFile, *, source: str, url: str = "") -> None:
    """Put the image on the company, replacing whatever was there.

    The most recent action wins — a URL, the website, an upload — so there is no
    precedence rule for anybody to learn.
    """
    if company.logo:
        company.logo.delete(save=False)
    company.logo.save(f"logo-{company.pk}.png", content, save=False)
    company.logo_source = source
    company.logo_source_url = url[:500]
    company.logo_fetched_at = timezone.now()
    company.save(
        update_fields=["logo", "logo_source", "logo_source_url", "logo_fetched_at", "updated_at"]
    )


def clear(company) -> None:
    if company.logo:
        company.logo.delete(save=False)
    company.logo_source = ""
    company.logo_source_url = ""
    company.logo_fetched_at = None
    company.save(
        update_fields=["logo", "logo_source", "logo_source_url", "logo_fetched_at", "updated_at"]
    )


def from_url(company, url: str) -> None:
    """Fetch the address, keep the picture. Raises :class:`UnusableLogo` with the reason."""
    store(company, process(download(url)), source="url", url=url)


def from_upload(company, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise UnusableLogo(str(_("That file is larger than a logo should be.")))
    store(company, process(data), source="upload")


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
    numbers = [int(match) for match in re.findall(r"(\d+)x\d+", sizes or "", re.IGNORECASE)]
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
        logger.exception("Could not read the markup of %s", website)
    base = page.url or website
    candidates = [urljoin(base, href) for href in parser.candidates()]
    candidates.append(urljoin(base, "/favicon.ico"))

    problems = []
    for candidate in candidates[:6]:
        try:
            store(company, process(download(candidate)), source="website", url=candidate)
        except UnusableLogo as error:
            problems.append(str(error))
            continue
        return candidate
    raise UnusableLogo(
        str(_("Nothing on %(host)s could be used as a logo."))
        % {"host": urlsplit(base).hostname or website}
    )
