"""The sources Postulo ships with.

Two, tried in order.

``schema.org``
    Reads the ``JobPosting`` object that most large boards already embed as JSON-LD, for
    the benefit of search engines. It is a published standard, the sites maintain it
    themselves, and reading it breaks far less often than guessing at their markup would.
    No CSS selectors, no per-site rules, nothing to repair when a board redesigns.

``page-metadata``
    When there is no structured data, take the title the page declares and the readable
    text, and let the person capturing fix the rest. Deliberately unambitious: it saves
    typing, and it never pretends to know more than it does.

Neither invents a value. A field that cannot be determined is left empty for somebody to
fill in on the review screen.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .base import JobPostingData
from .htmlutil import extract_jsonld, extract_meta, html_to_text, strip_tags

#: schema.org employmentType values mapped onto Postulo's own.
EMPLOYMENT_TYPES = {
    "FULL_TIME": "full_time",
    "PART_TIME": "part_time",
    "CONTRACTOR": "contract",
    "CONTRACT": "contract",
    "TEMPORARY": "contract",
    "INTERN": "internship",
    "INTERNSHIP": "internship",
    "APPRENTICESHIP": "apprenticeship",
}

#: schema.org QuantitativeValue unitText values Postulo has a period for.
SALARY_PERIODS = {"YEAR": "year", "MONTH": "month", "DAY": "day", "HOUR": "hour"}


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


def _location(job_location) -> str:
    """Assemble a readable place from a schema.org Place."""
    place = _first(job_location)
    if not isinstance(place, dict):
        return _text(job_location)

    address = place.get("address")
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return _text(place)

    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    readable = [
        str(part).strip() for part in parts if isinstance(part, str | int) and str(part).strip()
    ]
    if isinstance(address.get("addressCountry"), dict):
        country = _text(address["addressCountry"])
        if country:
            readable.append(country)
    return ", ".join(dict.fromkeys(readable))


def _salary(base_salary) -> tuple[Decimal | None, Decimal | None, str, str]:
    salary = _first(base_salary)
    if not isinstance(salary, dict):
        return None, None, "", ""

    currency = str(salary.get("currency") or salary.get("salaryCurrency") or "").strip()[:3]
    value = salary.get("value")

    if isinstance(value, dict):
        low = _decimal(value.get("minValue"))
        high = _decimal(value.get("maxValue"))
        if low is None and high is None:
            low = _decimal(value.get("value"))
        period = SALARY_PERIODS.get(str(value.get("unitText") or "").upper(), "")
    else:
        low, high, period = _decimal(value), None, ""

    return low, high, currency.upper(), period


class SchemaOrgSource:
    """Read the schema.org JobPosting that a site publishes about itself."""

    name = "schema.org"
    version = "1.0"

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    def parse(self, url: str, html: str) -> JobPostingData | None:
        posting = self._find_posting(extract_jsonld(html))
        if posting is None:
            return None

        title = _text(posting.get("title"))
        if not title:
            return None

        low, high, currency, period = _salary(posting.get("baseSalary"))
        employment = str(_first(posting.get("employmentType")) or "").upper().replace("-", "_")
        remote = (
            "remote" if "TELECOMMUTE" in str(posting.get("jobLocationType") or "").upper() else ""
        )

        return JobPostingData(
            title=title,
            company_name=_text(posting.get("hiringOrganization")),
            location=_location(posting.get("jobLocation")),
            remote_type=remote,
            employment_type=EMPLOYMENT_TYPES.get(employment, ""),
            description=strip_tags(posting.get("description") or ""),
            salary_min=low,
            salary_max=high,
            salary_currency=currency,
            salary_period=period,
            posted_at=_date(posting.get("datePosted")),
            closes_at=_date(posting.get("validThrough")),
            url=url,
            source=urlparse(url).netloc,
        )

    @staticmethod
    def _find_posting(objects: list[dict]) -> dict | None:
        for obj in objects:
            types = obj.get("@type")
            names = {str(t).lower() for t in (types if isinstance(types, list) else [types])}
            if "jobposting" in names:
                return obj
        return None


class PageMetadataSource:
    """The fallback: a title, whatever the page says about itself, and its text."""

    name = "page-metadata"
    version = "1.0"

    def can_handle(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    def parse(self, url: str, html: str) -> JobPostingData | None:
        meta = extract_meta(html)
        title = meta.get("og:title") or meta.get("twitter:title") or meta.get("title") or ""
        if not title.strip():
            return None

        return JobPostingData(
            title=title[:500],
            company_name=(meta.get("og:site_name") or "")[:500],
            description=html_to_text(html),
            url=url,
            source=urlparse(url).netloc,
        )


#: Tried in order, after any plugin a third party has registered.
BUILTIN_SOURCES = (SchemaOrgSource, PageMetadataSource)
