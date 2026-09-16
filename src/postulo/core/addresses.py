"""What identifies a page, across the ways one address can be written -- and what is one.

Everything Postulo stores as an address is rendered as an ``href`` somewhere: a company's
website and careers page, a listing's address, a contact's profile. A browser follows
``javascript:`` in one of those by running it, as the person who opened the page, so an
address that arrives through the API has to be told apart from a script before it is kept.
The forms have always done this, because ``URLField`` validates; the API set the field
with ``setattr`` and never called ``full_clean``, so it did not (#218).
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

#: The only two schemes worth following from a page. Django's `URLValidator` allows `ftp`
#: and `ftps` as well by default, which nothing here has a use for.
WEB_SCHEMES = ("http", "https")

#: The same grammar `URLField` applies, so the API refuses exactly what the page refuses
#: rather than keeping something the form would have sent back.
_complete = URLValidator(schemes=list(WEB_SCHEMES))


def web_address(raw: str) -> str:
    """``raw`` if it is a complete http or https address, empty if it is empty.

    Raises `ValueError` otherwise, which is what pydantic turns into a 422 naming the
    field -- a refusal the client can read, rather than a 500 or a stored script.
    """
    value = (raw or "").strip()
    if not value:
        return value
    try:
        _complete(value)
    except ValidationError as exc:
        raise ValueError(str(_("That does not look like a complete web address."))) from exc
    return value


def page_address(raw: str) -> str:
    """``raw`` if it names a page over http or https, empty if it is empty.

    The scheme is checked and the rest is not, deliberately. This is the address a page
    was *read at* -- an internal board on a host with no dot in its name is a real one,
    and losing a whole capture over the shape of a hostname would cost more than it saves.
    The scheme is the whole of the danger anyway: it is what decides whether a browser
    following the link fetches a document or runs a script.
    """
    value = (raw or "").strip()
    if not value:
        return value
    refusal = _("Only http and https addresses can be captured.")
    try:
        scheme = urlparse(value).scheme.lower()
    except ValueError as exc:
        raise ValueError(str(refusal)) from exc
    if scheme not in WEB_SCHEMES:
        raise ValueError(str(refusal))
    return value


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
