"""The contract every capture source obeys.

There is exactly one shape of parsed posting in Postulo, and everything produces it: the
built-in parser, a third-party plugin, and one day a browser extension posting to the
API. Validating them all through the same schema means a plugin cannot invent a field,
misspell one, or smuggle a value past the review screen.

A source is deliberately given very little to do. It receives a URL and the HTML that was
fetched from it, and returns data. It does not touch the database, decide whether the
result is good enough, or create anything — the person capturing does that, on the review
screen. A parser that guesses wrong should waste a few seconds of somebody's attention,
not put a fabricated job title into their records.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

#: Nothing longer than this is kept from a page. Job adverts are not novels, and an
#: unbounded field is an invitation to store somebody's entire single-page application.
MAX_DESCRIPTION_CHARS = 40_000
MAX_FIELD_CHARS = 500


class JobPostingData(BaseModel):
    """A posting as some source understood it.

    Every field is optional except the title, because a source that could only find the
    job title has still saved somebody most of the typing. Fields it could not determine
    are left empty rather than guessed.
    """

    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    title: str = Field(max_length=MAX_FIELD_CHARS)
    company_name: str = Field(default="", max_length=MAX_FIELD_CHARS)
    location: str = Field(default="", max_length=MAX_FIELD_CHARS)
    remote_type: str = Field(default="", max_length=20)
    employment_type: str = Field(default="", max_length=20)
    # No max_length here on purpose: a length constraint is checked before any
    # validator runs, so declaring one would reject an over-long advert instead of
    # letting truncate_description shorten it. The cap is enforced there.
    description: str = ""

    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = Field(default="", max_length=3)
    salary_period: str = Field(default="", max_length=10)

    posted_at: dt.date | None = None
    closes_at: dt.date | None = None

    url: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_say_something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A posting needs a title.")
        return value

    @field_validator("description")
    @classmethod
    def truncate_description(cls, value: str) -> str:
        """Cut an over-long description rather than rejecting the whole capture.

        Losing the last few thousand characters of an advert is a much smaller problem
        than throwing away a capture that was otherwise perfectly good.
        """
        if len(value) > MAX_DESCRIPTION_CHARS:
            return value[:MAX_DESCRIPTION_CHARS].rstrip() + "\n\n[…truncated]"
        return value


@runtime_checkable
class SourcePlugin(Protocol):
    """What a capture source must provide.

    Implementations need no base class. A plugin is anything with these four names, which
    keeps third-party packages from having to import Postulo internals just to be
    recognised.
    """

    #: A short identifier, recorded against every capture this source produced.
    name: str
    #: The plugin's own version, so a capture can be traced to the code that made it.
    version: str

    def can_handle(self, url: str) -> bool:
        """Whether this source wants to parse ``url``."""
        ...

    def parse(self, url: str, html: str) -> JobPostingData | None:
        """Extract a posting, or return ``None`` if this page yielded nothing useful."""
        ...


class CaptureError(Exception):
    """Raised when a page cannot be fetched or cannot be understood."""
