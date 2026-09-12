"""Has this advert been seen before? Asked before a capture is made, and told rather than refused.

Nothing checked, until this, whether a posting had been captured already: the same advert on
Monday and again on Thursday made two captures and, once both were reviewed, two listings
for one job. A unique constraint would be the wrong answer -- boards reuse an address for a
different advert, somebody may re-capture an edited one on purpose, and a refusal at the
database arrives as an error after the work. So this answers a question, in time for the
person to decide: is there a listing at this address, a capture of it still waiting, or a
listing with this title at this company? The last is asked more softly, because a company
running two openings with the same title is not a duplicate -- and a board that mints a
fresh address on every visit is (#178).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from postulo.core.addresses import same_url

from .models import Capture, CaptureStatus, JobPosting

#: How many rows the address narrows to before the normalised form decides. An account with
#: more listings at one path than this has a larger problem than a duplicate.
LIMIT = 200


@dataclass(frozen=True)
class Known:
    """What exists already for one address: the answer to "have I captured this?"."""

    #: Listings at this very address, however it was spelled.
    listings: list = field(default_factory=list)
    #: Captures of it still waiting for review.
    captures: list = field(default_factory=list)
    #: Listings with this title at this company, at another address.
    similar: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.listings or self.captures or self.similar)


def _needle(url: str) -> str:
    """The part of an address worth asking the database about: its path, or else its host."""
    parsed = urlparse(url)
    return parsed.path.rstrip("/") or parsed.netloc.removeprefix("www.")


def known(owner, url: str, title: str = "", company: str = "", *, except_capture=None) -> Known:
    """What ``owner`` already holds for this address, and for this title at this company.

    The address is narrowed in SQL by its path and decided in Python by ``same_url``, so
    ``https://www.example.org/jobs/42/`` finds ``http://example.org/jobs/42``. The title and
    the company are compared whole, case aside. ``except_capture`` is the capture being
    reviewed, which is at its own address and is not a duplicate of itself.
    """
    key = same_url(url)
    listings: list[JobPosting] = []
    captures: list[Capture] = []
    if key:
        needle = _needle(url)
        candidates = (
            JobPosting.objects.for_user(owner)
            .filter(url__icontains=needle)
            .select_related("company")
            .order_by("-created_at")[:LIMIT]
        )
        listings = [posting for posting in candidates if same_url(posting.url) == key]
        waiting = (
            Capture.objects.for_user(owner)
            .filter(status=CaptureStatus.PENDING, url__icontains=needle)
            .order_by("-created_at")[:LIMIT]
        )
        captures = [
            capture
            for capture in waiting
            if same_url(capture.url) == key and capture.pk != except_capture
        ]
    similar: list[JobPosting] = []
    if title.strip() and company.strip():
        already = {posting.pk for posting in listings}
        found = (
            JobPosting.objects.for_user(owner)
            .filter(title__iexact=title.strip(), company__name__iexact=company.strip())
            .select_related("company")
            .order_by("-created_at")[:20]
        )
        similar = [posting for posting in found if posting.pk not in already]
    return Known(listings, captures, similar)
