"""A company's logo: fetched once from an address, found on their site, or uploaded.

The production policy is ``img-src 'self'``, and that is not an obstacle to work around —
it is the reason this module exists. An ``<img>`` pointing at somebody else's server would
tell them, on every page view, which companies this person is applying to and when they
looked. So "from a URL" cannot mean "show the URL": Postulo fetches the image **once**,
from the server, re-encodes it, keeps it in the database beside the company and serves it
itself (#662): a logo is a row that the database deletes with its company, not a file.

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

import logging
from urllib.parse import urljoin, urlsplit

import httpx
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.core import pictures, throttle
from postulo.plugins import http

from .models import CompanyLogo

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
KINDS = pictures.LOGO_KINDS

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


def process(data: bytes) -> ContentFile:
    """The bytes as Postulo will keep them: a sanitised SVG or a PNG.

    An SVG is sanitised and stays an SVG — it is a vector, and flattening a vector to
    pixels to store it would throw away the reason it is the better file. Anything else is
    decoded and written out again as PNG, at its own size, within the budget.

    **The square padding is not lost, it moved to CSS.** `{% company_logo %}` renders
    `object-contain` inside a square box at every call site, so a wide wordmark letterboxes
    in the browser exactly as it letterboxed in the file, and nothing in the layout changes
    (#264).
    """
    if pictures.looks_like_svg(data):
        return ContentFile(_svg(data))
    try:
        return ContentFile(pictures.as_stored(data, budget=MAX_STORED_BYTES, kinds=KINDS))
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
LOGO_FIELDS = ["has_logo", "logo_source", "logo_source_url", "logo_fetched_at", "updated_at"]


def store(company, content: ContentFile, *, source: str, url: str = "") -> None:
    """Put the image on the company, replacing whatever was there.

    The most recent action wins — a URL, the website, an upload — so there is no
    precedence rule for anybody to learn.

    The database write is here rather than around the view, because the view fetches: the
    page, then up to six images, none of it quick and none of it the instance's business to
    hold the write lock for (#220). The picture is decoded before this is entered, so what
    the transaction covers is the picture's row and the company's.

    The picture is a row beside the company (#662), so there is no file to write first, to
    remove afterwards or to leave behind when the save fails: the two writes are one
    transaction and either both happen or neither does.
    """
    before = {name: getattr(company, name) for name in LOGO_FIELDS}
    company.has_logo = True
    company.logo_source = source
    company.logo_source_url = url[:500]
    company.logo_fetched_at = timezone.now()
    try:
        with transaction.atomic():
            pictures.keep(CompanyLogo, content, company=company)
            legacy = pictures.let_go_of_the_file(company, "logo")
            company.save(update_fields=[*LOGO_FIELDS, *legacy])
    except BaseException:
        # Nothing was written, so the company in memory says what the database does.
        for name, value in before.items():
            setattr(company, name, value)
        raise


def clear(company) -> None:
    """Take the logo off the company: the row, and the flag that says there is one."""
    with transaction.atomic():
        CompanyLogo.objects.filter(company=company).delete()
        company.has_logo = False
        company.logo_source = ""
        company.logo_source_url = ""
        company.logo_fetched_at = None
        legacy = pictures.let_go_of_the_file(company, "logo")
        company.save(update_fields=[*LOGO_FIELDS, *legacy])


def spend_fetch(who) -> None:
    """Use one of the person's fetches, or raise :class:`UnusableLogo` saying why not (#407)."""
    try:
        throttle.fetch(who)
    except throttle.TooOften as too_often:
        raise UnusableLogo(str(too_often)) from too_often


def from_url(company, url: str, *, by=None, spend: bool = True) -> None:
    """Fetch the address, keep the picture. Raises :class:`UnusableLogo` with the reason.

    The allowance spent is that of ``by``, the person who pressed the button: never the
    company's owner, who may be somebody else or a group, which is nobody (#960).
    ``spend=False`` for a caller that has already spent it for this fetch.
    """
    if spend:
        if by is None:
            raise TypeError("from_url spends the allowance of `by`, the person acting")
        spend_fetch(by)
    store(company, process(download(url)), source="url", url=url)


def from_upload(company, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise UnusableLogo(str(_("That file is larger than a logo should be.")))
    store(company, process(data), source="upload")


# ------------------------------------------------- finding one on their site


def site_sources(company, person=None) -> list[str]:
    """The sources on for ``person`` that read ``company``'s own website for a logo.

    Only those: *Find logo* spends the fetch allowance, says it reads the website and records
    what it keeps as found there, so a source with hosts of its own (an open data set) is
    asked through *Find details* and its own allowance, never through this button. Asking
    which they are asks nobody anything.
    """
    from . import enrichment

    return [
        source.key
        for source in enrichment.usable_for(company, person, fields=("logo",))
        if source.own_site
    ]


def find_on_website(company, *, by=None) -> str:
    """Look on the company's own site for its icon, and keep the first that works.

    The site is the one public repository every company maintains, and the address is one
    the person typed. Reading it is a source's (#1256): the `company-enrichment` sources
    that offer a logo -- Postulo's own, the `company-site` plugin, reads the page as capture
    does, ``robots.txt`` honoured -- name the images the site declares, best first, and the
    images are fetched, decoded and kept here, as one from an address is. ``by`` is the
    person who pressed, whose switches decide which sources are asked.
    """
    from . import enrichment

    website = (company.website or "").strip()
    if not website:
        raise UnusableLogo(str(_("This company has no website recorded.")))
    results = enrichment.ask(company, site_sources(company, by), person=by, fields=("logo",))
    if not results:
        raise UnusableLogo(str(_("Nothing is switched on that finds a logo on a website.")))
    errors = [result.error for result in results if result.error]
    candidates = [value for result in results for value in result.values]
    if not candidates and errors:
        raise UnusableLogo(
            str(_("Could not read %(site)s: %(error)s")) % {"site": website, "error": errors[0]}
        )

    problems = []
    for candidate in dict.fromkeys(value.value for value in candidates):
        try:
            store(company, process(download(candidate)), source="website", url=candidate)
        except UnusableLogo as error:
            problems.append(str(error))
            continue
        return candidate
    base = next((value.url for value in candidates if value.url), website)
    host = urlsplit(base).hostname or website
    # The company by number, not its host (#797): the log is staff's and a host is what a
    # person is looking at.
    logger.info("Logo for company %s: %d candidate(s) refused", company.pk, len(problems))
    raise UnusableLogo(
        str(_("Nothing on %(host)s could be used as a logo: %(reason)s"))
        % {"host": host, "reason": problems[0] if problems else ""}
    )
