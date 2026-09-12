"""What identifies a page, across the ways one address can be written."""

from __future__ import annotations

from urllib.parse import urlparse


def same_url(raw: str) -> str:
    """A URL reduced to what identifies the posting: no scheme, no "www.", no trailing slash.

    Written for #176, where a results page states a JobPosting for every hit and the one
    naming this page is the one wanted; reused by #178 to ask whether an address has been
    captured before. Two spellings of one address -- http and https, with and without
    "www.", with and without the trailing slash, in either case -- reduce to one string.
    The query is kept, because a board that puts the job id there means it.
    """
    try:
        parsed = urlparse(raw or "")
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}".lower()
