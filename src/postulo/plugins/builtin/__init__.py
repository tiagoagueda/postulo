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
    When there is no structured data, take what the page declares about itself and the
    readable text, and let the person capturing fix the rest. Deliberately unambitious: it
    saves typing, and it never pretends to know more than it does. It reads the OpenGraph
    and Twitter card properties as well as ``<title>``, splits a declared title into the
    job, the employer and the place only where something on the page vouches for each part
    (`titles`), and finds a salary and a closing date written after the words the page's own
    language uses for them (`patterns`).

Between the standards and the page's metadata sit **the places a person's own corrections
showed a field to be** on that site (`hints`, #267): below the recipes and schema.org, which
are the site's own statements, and above the metadata, which is the floor. Postulo hands
them to these three sources and to nobody else's (`registry.reads_hints`); each field is
filled from the first tier that states it, and Postulo keeps the score of which places
helped once the person has reviewed the capture (`jobs.remembered`).

None of them invents a value. A field that cannot be determined is left empty for somebody
to fill in on the review screen.

The browser extension reads the same pages over a real DOM
(``postulo-chromium/src/lib/parse.js``), so that a page read in somebody's browser is the
page read here when it is sent. The two are meant to be walked side by side; changing one
without the other is how they drift -- and `titles` and `patterns` are not in the extension
yet, which is an issue in its own repository.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, unquote, urlparse

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import JobPostingData, declares, shipped

from . import hints as remembered
from . import patterns, titles
from .boards import recipe_for
from .htmlutil import (
    body_of,
    extract_jsonld,
    extract_meta,
    extract_microdata,
    extract_rdfa,
    flatten,
    heading_title,
    main_text,
    page_language,
    parse_html,
    strip_tags,
)
from .vocabulary import EMPLOYMENT_TYPES, SALARY_PERIODS, employment_type  # noqa: F401

logger = logging.getLogger(__name__)


def _stated(data: JobPostingData) -> dict:
    """The fields a tier actually stated, without the empties and the address."""
    return {
        field: value
        for field, value in data.model_dump(exclude={"url", "source"}).items()
        if value not in (None, "", [])
    }


def _with_hints(data: JobPostingData | None, url: str, html: str, hints) -> JobPostingData | None:
    """What a person's remembered places add to what a tier above them stated (#267).

    Only a field the tier left empty is asked about, so a site's own statement is never
    overruled by a place that happened to hold something else.
    """
    if data is None or not hints:
        return data
    stated = remembered.fill(url, html, hints, _stated(data))
    return JobPostingData(**stated, url=data.url, source=data.source)


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
        # A board's template can render "NaN" or "Infinity", and the review form holds
        # twelve digits: an amount outside what the text reader accepts is no amount.
        return amount if amount.is_finite() and 0 < amount < patterns.LARGEST else None
    except (InvalidOperation, TypeError, ValueError):
        return None


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

    # Kept only where it is an ISO code or a symbol that names one currency, as the text
    # reader does; "US Dollar" or a bare "$" is left for the person to state.
    written = str(salary.get("currency") or salary.get("salaryCurrency") or "").strip()
    currency = patterns.currency_code(written.upper() if written.isalpha() else written, "")
    inner = salary.get("value")

    if isinstance(inner, dict):
        low = _decimal(inner.get("minValue"))
        high = _decimal(inner.get("maxValue"))
        if low is None and high is None:
            low = _decimal(inner.get("value"))
        period = SALARY_PERIODS.get(str(inner.get("unitText") or "").upper(), "")
    else:
        low, high, period = _decimal(inner), None, ""

    return low, high, currency, period


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


def _names_page(posting: dict, url: str) -> bool:
    """Does the posting say, in ``url``, ``@id`` or ``sameAs``, that it is this very page?"""
    here = _same_url(url)
    if not here:
        return False
    for key in ("url", "@id", "sameAs"):
        stated = _same_url(_text(posting.get(key)))
        if stated and stated == here:
            return True
    return False


def _identifier(value) -> str:
    """A posting's identifier: the ``value`` of a ``PropertyValue``, or the plain string.

    Never its ``name``, which schema.org and Google's example make the employer's (#590).
    """
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip()


def _in_address(identifier: str, url: str) -> bool:
    """Is the identifier a whole path segment or a whole query value of the address?

    Not a substring: ``1000`` is not in ``/jobs/10001``. Anything this short matches by
    accident; a board's own id never is.
    """
    if len(identifier) < 4:
        return False
    parsed = urlparse(url)
    wanted = identifier.lower()
    parts = [unquote(piece).lower() for piece in parsed.path.split("/") if piece]
    parts += [value.lower() for _name, value in parse_qsl(parsed.query)]
    return wanted in parts


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
    # In two passes: a posting that names this page outright beats one whose identifier
    # happens to appear in its address, whichever comes first on the page (#590).
    for posting in found:
        if _names_page(posting, url):
            return posting
    for posting in found:
        if _in_address(_identifier(posting.get("identifier")), url):
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
        title=title[:MAX_FIELD_CHARS],
        company_name=_text(posting.get("hiringOrganization"))[:MAX_FIELD_CHARS],
        location=location[:MAX_FIELD_CHARS],
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

    def parse(self, url: str, html: str, hints=None) -> JobPostingData | None:
        """``hints``, a person's remembered places, fill only what the posting left empty."""
        posting = _best_posting(extract_jsonld(html), url)
        if posting is not None:
            read = _from_posting(posting, url, description_is_html=True)
            return _with_hints(read, url, html, hints)

        # A posting held inside another item is found as it is in JSON-LD (#589).
        posting = _best_posting(flatten([*extract_microdata(html), *extract_rdfa(html)]), url)
        if posting is not None:
            read = _from_posting(posting, url, description_is_html=False)
            return _with_hints(read, url, html, hints)
        return None


#: The fields a recipe may state. Anything else it returns is ignored rather than trusted.
#: What `JobPostingData` holds of a title, a company and a place; it raises rather than cuts.
MAX_FIELD_CHARS = 500
CUT_FIELDS = frozenset({"title", "company_name", "location"})
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

    def parse(self, url: str, html: str, hints=None) -> JobPostingData | None:
        """``hints``, a person's remembered places, fill what the recipe and the page's
        standards left empty, before what the page says about itself does."""
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
        # A recipe's field may run past what a posting holds; cut it, as the page's own
        # metadata is, rather than lose the board's whole answer to a validation error.
        stated = {
            field: value[:MAX_FIELD_CHARS] if field in CUT_FIELDS else value
            for field, value in stated.items()
        }
        if not stated.get("title"):
            # A recipe that cannot name the job has not recognised the page -- an advert
            # that has moved, or a board that redesigned. Let the standards have it.
            return None

        # Whatever the recipe did not state: the page's own standards first, then the
        # person's remembered places, then what the page says about itself.
        stated = self._fill_from(SchemaOrgSource, url, html, stated)
        stated = remembered.fill(url, html, hints, stated)
        stated = self._fill_from(PageMetadataSource, url, html, stated)
        return JobPostingData(**stated, url=url, source=urlparse(url).netloc)

    @staticmethod
    def _fill_from(source_class, url: str, html: str, stated: dict) -> dict:
        """The fields nothing above has stated, from one of the standard sources."""
        if len(stated) >= len(RECIPE_FIELDS):
            return stated
        try:
            fallback = source_class().parse(url, html)
        except Exception:
            # The recipe already has a title, so the capture survives this. Logged rather
            # than silenced: a standard source throwing is worth looking at.
            logger.warning("Filling gaps with %s failed on %r", source_class.__name__, url)
            return stated
        if fallback is None:
            return stated
        for field in RECIPE_FIELDS - set(stated):
            value = getattr(fallback, field)
            if value not in (None, "", []):
                stated[field] = value
        return stated


def _is_place(text: str) -> bool:
    """Whether Postulo's table of cities knows ``text`` as a place (`jobs/places.py`)."""
    from postulo.plugins.api import place_of

    return place_of(text) is not None


def _card_lines(meta: dict[str, str]) -> list[str]:
    """A Twitter card's label-and-value pairs, as the lines a reader would have seen.

    ``twitter:label1`` "Salary" and ``twitter:data1`` "£45,000" say what a line of the page
    would, and are read by the same words in the same language, or not at all.
    """
    lines = []
    for number in range(1, 5):
        label = meta.get(f"twitter:label{number}", "").strip()
        value = meta.get(f"twitter:data{number}", "").strip()
        if label and value:
            lines.append(f"{label}: {value}")
    return lines


def _declared_place(meta: dict[str, str]) -> str:
    """The place OpenGraph's own location properties state, on a page that uses them."""
    parts = [meta.get(key, "").strip() for key in ("og:locality", "og:region", "og:country-name")]
    return ", ".join(part for part in parts if part)


def _description(text: str, meta: dict[str, str]) -> str:
    """The page's readable text, or what it declares about itself where that says more.

    A page drawn by a script arrives as a shell -- "Loading…", "Please enable JavaScript"
    -- with its advert only in the description it declares for a link to show.
    """
    declared = max(
        (meta.get(key, "").strip() for key in ("og:description", "twitter:description")),
        key=len,
    )
    return declared if len(declared) > len(text) else text


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
    """The fallback: whatever the page says about itself, and its text.

    Below a person's remembered places, which are asked first; a place that finds nothing
    leaves its field to what the page says about itself.
    """

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    def parse(self, url: str, html: str, hints=None) -> JobPostingData | None:
        from_hints = remembered.fill(url, html, hints, {})
        read = self._read(url, html)
        # Each group a remembered place filled is the place's, whole; the rest is the page's.
        stated = {**read, **from_hints}
        if not str(stated.get("title") or "").strip():
            return None
        return JobPostingData(**stated, url=url, source=urlparse(url).netloc)

    def _read(self, url: str, html: str) -> dict:
        """What the page says about itself, field by field, before any remembered place."""
        meta = extract_meta(html)
        site_name = (meta.get("og:site_name") or "").strip()
        # The page's own heading first, then what it declares for a link to read, in the
        # order they are trusted. All of it is the page talking about itself; the heading is
        # the one written for a person, and a declared line is split only where something
        # else on the page vouches for a part.
        read = titles.read(
            declared=[
                meta.get("og:title", ""),
                meta.get("twitter:title", ""),
                meta.get("title", ""),
            ],
            heading=heading_title(html),
            site_name=site_name,
            host=urlparse(url).hostname or "",
            is_place=_is_place,
        )

        text = main_text(html)
        # The pay and the closing date, in the page's own language, from its text and from
        # the pairs a Twitter card may carry. The advert's text rather than the whole page:
        # a sidebar of other adverts carries other adverts' salaries.
        language = patterns.tag_of(page_language(html))
        stated = "\n".join([text, *_card_lines(meta)])
        low, high, currency, period = patterns.salary(stated, language)

        return {
            "title": read.title[:500],
            # Who is hiring, where the title says so; else what the site calls itself, which
            # is the employer on its own careers site and the board on a board.
            "company_name": (read.company or site_name)[:500],
            "location": (read.place or _declared_place(meta))[:500],
            "description": _description(text, meta),
            "salary_min": low,
            "salary_max": high,
            "salary_currency": currency,
            "salary_period": period,
            "closes_at": patterns.closing_date(stated, language),
        }


#: Tried in order, after any plugin a third party has registered.
BUILTIN_SOURCES = (BoardSource, SchemaOrgSource, PageMetadataSource)
