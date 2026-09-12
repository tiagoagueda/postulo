"""The sources Postulo ships with.

Three, tried in order.

``board``
    A recipe per board, for the boards that publish nothing a standard can read -- LinkedIn
    publishes no JSON-LD, no microdata, and marks the company and the place with class names
    only. Runs first and only on a host some recipe claims; whatever the recipe leaves empty
    is filled from the standards below it, so a board that starts publishing them improves
    without its recipe being touched. See `boards/` for what a recipe may and may not state.

``schema.org``
    Reads the ``JobPosting`` object that most large boards already embed for the benefit of
    search engines. It is a published standard, the sites maintain it themselves, and
    reading it breaks far less often than guessing at their markup would. No CSS selectors,
    no per-site rules, nothing to repair when a board redesigns.

    The standard has three spellings and this reads all of them: JSON-LD in a script, which
    is what most boards publish, and ``itemprop`` microdata or RDFa written into the markup,
    which is all that older recruitment systems and a good many public-sector boards
    publish. One source rather than three, because it is one vocabulary -- a posting read
    out of ``itemprop`` is not a second guess at a board's markup, it is the same standard
    the same way round.

``page-metadata``
    When there is no structured data, take the title the page declares and the readable
    text, and let the person capturing fix the rest. Deliberately unambitious: it saves
    typing, and it never pretends to know more than it does.

Neither invents a value. A field that cannot be determined is left empty for somebody to
fill in on the review screen.

The browser extension reads the same two, over a real DOM
(``postulo-chromium/src/lib/parse.js``), so that a page read in somebody's browser is the
page read here when it is sent. The two are meant to be walked side by side; changing one
without the other is how they drift.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import JobPostingData, declares, shipped

from .boards import recipe_for
from .htmlutil import (
    body_of,
    extract_jsonld,
    extract_meta,
    extract_microdata,
    extract_rdfa,
    heading_title,
    main_text,
    parse_html,
    strip_tags,
)
from .vocabulary import EMPLOYMENT_TYPES, SALARY_PERIODS, employment_type  # noqa: F401

logger = logging.getLogger(__name__)


def _first(value):
    """schema.org lets almost anything be a single value or a list of them."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text(value) -> str:
    """Coerce a schema.org value to a string, following ``name`` where present."""
    value = _first(value)
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "").strip()
    return str(value).strip()


def _date(value) -> dt.date | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _decimal(value) -> Decimal | None:
    if value in (None, "", []):
        return None
    try:
        amount = Decimal(str(_first(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _one_place(place) -> str:
    """One schema.org Place as a readable line, without the repetition sites put in."""
    if not isinstance(place, dict):
        return place.strip() if isinstance(place, str) else ""

    address = place.get("address")
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return _text(place)

    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        _text(address["addressCountry"])
        if isinstance(address.get("addressCountry"), dict)
        else address.get("addressCountry"),
    ]

    # Sites repeat themselves. MathWorks, for one, writes "Issy-les-Moulineaux, FR" as
    # the locality and "FR" again as the country, which joined naively reads
    # "Issy-les-Moulineaux, FR, FR". Comparing comma-separated pieces rather than whole
    # strings drops the repetition without discarding a genuine "York" after "New York".
    readable: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part).strip() if isinstance(part, str | int) else ""
        if not text:
            continue
        pieces = {piece.strip().lower() for piece in text.split(",") if piece.strip()}
        if pieces and pieces <= seen:
            continue
        seen |= pieces
        readable.append(text)
    return ", ".join(readable)


def _location(job_location) -> str:
    """A readable place from a jobLocation, which may name more than one office."""
    many = job_location if isinstance(job_location, list) else [job_location]
    lines: list[str] = []
    for one in many:
        line = _one_place(one)
        if line and line not in lines:
            lines.append(line)
    # Three offices read as a place; a board listing forty is listing its coverage, not one.
    return " · ".join(lines[:3])


def _one_salary(value) -> tuple[Decimal | None, Decimal | None, str, str]:
    """``(low, high, currency, period)`` from one MonetaryAmount."""
    salary = _first(value)
    if not isinstance(salary, dict):
        return None, None, "", ""

    currency = str(salary.get("currency") or salary.get("salaryCurrency") or "").strip()[:3]
    inner = salary.get("value")

    if isinstance(inner, dict):
        low = _decimal(inner.get("minValue"))
        high = _decimal(inner.get("maxValue"))
        if low is None and high is None:
            low = _decimal(inner.get("value"))
        period = SALARY_PERIODS.get(str(inner.get("unitText") or "").upper(), "")
    else:
        low, high, period = _decimal(inner), None, ""

    return low, high, currency.upper(), period


def _salary(posting: dict) -> tuple[Decimal | None, Decimal | None, str, str]:
    """What a posting pays: its baseSalary, or the estimate where that is all it states.

    A currency and a period with no amount behind them say nothing -- boards routinely write
    a "USD 0-0 YEAR" placeholder into every advert -- so they are kept only alongside one.
    """
    for candidate in (posting.get("baseSalary"), posting.get("estimatedSalary")):
        low, high, currency, period = _one_salary(candidate)
        if low is not None or high is not None:
            return low, high, currency, period
    return None, None, "", ""


def _same_url(raw: str) -> str:
    """A URL reduced to what identifies the posting: no scheme, no "www.", no trailing slash."""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}".lower()


def _matches_url(posting: dict, url: str) -> bool:
    """Is this posting the one the page is showing?

    A search page carries a JobPosting for every hit, and the first is almost never the one
    being looked at. Each states which advert it is, in ``url``, in ``@id``, or as the
    ``identifier`` a board puts in its own links, so the one that names this page wins.
    """
    here = _same_url(url)
    if not here:
        return False
    for key in ("url", "@id", "sameAs"):
        stated = _same_url(_text(posting.get(key)))
        if stated and stated == here:
            return True
    identifier = _text(posting.get("identifier"))
    # Anything this short matches by accident; a board's own id never is.
    return len(identifier) >= 4 and identifier.lower() in here


def _postings(objects: list[dict]) -> list[dict]:
    """The JobPosting objects among a page's structured data, in the order they appear."""
    found = []
    for obj in objects:
        types = obj.get("@type")
        names = {str(t).lower() for t in (types if isinstance(types, list) else [types])}
        if "jobposting" in names and _text(obj.get("title")):
            found.append(obj)
    return found


def _best_posting(objects: list[dict], url: str) -> dict | None:
    """The posting a page is showing: the one naming this URL, or the only one there is."""
    found = _postings(objects)
    if len(found) < 2:
        return found[0] if found else None
    for posting in found:
        if _matches_url(posting, url):
            return posting
    return found[0]


def _from_posting(posting: dict, url: str, *, description_is_html: bool) -> JobPostingData | None:
    """Every field Postulo has, read off one schema.org JobPosting however it was written.

    ``description_is_html`` because the two spellings differ in exactly one place: a JSON
    string carries the description as escaped markup and has to be unescaped and flattened,
    while a description read out of the markup is already the text a reader sees. Running
    the unescaping over that text would mangle a description that legitimately contains an
    ampersand or an angle bracket.
    """
    title = _text(posting.get("title"))
    if not title:
        return None

    low, high, currency, period = _salary(posting)

    # schema.org defines applicantLocationRequirements as where an applicant may be for a
    # job done remotely, so a posting carrying one is stating remote as plainly as the type.
    remote = (
        "remote"
        if "TELECOMMUTE" in str(posting.get("jobLocationType") or "").upper()
        or posting.get("applicantLocationRequirements") is not None
        else ""
    )

    # Most boards put the office in jobLocation. Those that do not still name it on the
    # organisation doing the hiring, which is the same answer written a field across.
    organisation = _first(posting.get("hiringOrganization"))
    location = _location(posting.get("jobLocation"))
    if not location and isinstance(organisation, dict):
        location = _one_place({"address": organisation.get("address")})

    described = posting.get("description")
    described = described if isinstance(described, str) else ""
    description = strip_tags(described) if description_is_html else described

    return JobPostingData(
        title=title,
        company_name=_text(posting.get("hiringOrganization")),
        location=location,
        remote_type=remote,
        employment_type=employment_type(_first(posting.get("employmentType")) or ""),
        description=description,
        salary_min=low,
        salary_max=high,
        salary_currency=currency,
        salary_period=period,
        posted_at=_date(posting.get("datePosted")),
        closes_at=_date(posting.get("validThrough")),
        url=url,
        source=urlparse(url).netloc,
    )


@declares(
    shipped(
        name="schema.org",
        label="schema.org",
        kind="source",
        description=_(
            "Reads the JobPosting most large boards already publish about themselves, "
            "for search engines. A published standard the sites maintain, so it breaks "
            "far less often than guessing at their markup would."
        ),
    )
)
class SchemaOrgSource:
    """Read the schema.org JobPosting that a site publishes about itself.

    JSON-LD first, because that is what most boards publish and it is the least ambiguous;
    then the same vocabulary written into the markup, for the boards that publish it no
    other way. A page carrying both is read from the script, which is the one a board
    maintains deliberately.
    """

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    def parse(self, url: str, html: str) -> JobPostingData | None:
        posting = _best_posting(extract_jsonld(html), url)
        if posting is not None:
            return _from_posting(posting, url, description_is_html=True)

        posting = _best_posting([*extract_microdata(html), *extract_rdfa(html)], url)
        if posting is not None:
            return _from_posting(posting, url, description_is_html=False)
        return None


#: The fields a recipe may state. Anything else it returns is ignored rather than trusted.
RECIPE_FIELDS = frozenset(
    {
        "title",
        "company_name",
        "location",
        "remote_type",
        "employment_type",
        "description",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "posted_at",
        "closes_at",
    }
)


@declares(
    shipped(
        name="board",
        label=_("Board recipes"),
        kind="source",
        description=_(
            "For the boards that publish nothing a standard can read. A recipe records "
            "where one board puts each field; whatever it leaves empty is filled from the "
            "published standards."
        ),
    )
)
class BoardSource:
    """A board's own recipe, with the standards filling whatever it left empty.

    Only for a page whose host a recipe claims: everywhere else this answers nothing and
    the standard sources run exactly as they did.
    """

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"} and recipe_for(url) is not None

    def parse(self, url: str, html: str) -> JobPostingData | None:
        board = recipe_for(url)
        if board is None:
            return None

        root = parse_html(html)
        stated = board.read(body_of(root) or root, url)
        stated = {
            field: value
            for field, value in stated.items()
            if field in RECIPE_FIELDS and value not in (None, "", [])
        }
        if not stated.get("title"):
            # A recipe that cannot name the job has not recognised the page -- an advert
            # that has moved, or a board that redesigned. Let the standards have it.
            return None

        # Whatever the recipe did not state, taken from the page's own standards.
        for source_class in (SchemaOrgSource, PageMetadataSource):
            if len(stated) >= len(RECIPE_FIELDS):
                break
            try:
                fallback = source_class().parse(url, html)
            except Exception:
                # The recipe already has a title, so the capture survives this. Logged
                # rather than silenced: a standard source throwing is worth looking at.
                logger.warning("Filling gaps with %s failed on %s", source_class.__name__, url)
                continue
            if fallback is None:
                continue
            for field in RECIPE_FIELDS - set(stated):
                value = getattr(fallback, field)
                if value not in (None, "", []):
                    stated[field] = value

        return JobPostingData(**stated, url=url, source=urlparse(url).netloc)


@declares(
    shipped(
        name="page-metadata",
        label=_("Page metadata"),
        kind="source",
        description=_(
            "The fallback, for a page with no structured data: the title it declares and "
            "its readable text. Deliberately unambitious — it saves typing and never "
            "pretends to know more than it does."
        ),
    )
)
class PageMetadataSource:
    """The fallback: a title, whatever the page says about itself, and its text."""

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    def parse(self, url: str, html: str) -> JobPostingData | None:
        meta = extract_meta(html)
        # The page's own heading first, then what it declares for a link to read. Both are
        # the page talking about itself; the heading is the one written for a person.
        title = (
            heading_title(html)
            or meta.get("og:title")
            or meta.get("twitter:title")
            or meta.get("title")
            or ""
        )
        if not title.strip():
            return None

        return JobPostingData(
            title=title[:500],
            company_name=(meta.get("og:site_name") or "")[:500],
            description=main_text(html),
            url=url,
            source=urlparse(url).netloc,
        )


#: Tried in order, after any plugin a third party has registered.
BUILTIN_SOURCES = (BoardSource, SchemaOrgSource, PageMetadataSource)
