"""The API: one machine-readable surface, scoped by token.

It began as the capture API — a way for something outside Postulo to hand over a posting,
and nothing else — and that part is unchanged: a token holding only the ``captures``
scope still cannot read an application, a CV, or anything else. The rest arrived for the
tools that need more: an agent acting for a person needs to read their search and,
if they say so, to write to it; a browser extension wants to know whether a posting is
already tracked.

Every read is owner-scoped exactly as the views are. Every write goes through the same
services as the forms, so the event log stays the single truth, and each entry written
this way names the token that wrote it.

The OpenAPI description is served at ``openapi.json`` under the API root. There is no
documentation page rendered here: its assets would have to come from a CDN the content
security policy forbids, and the schema is what a client consumes anyway.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from urllib.parse import urlsplit

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from ninja import NinjaAPI, Schema, Status
from ninja.errors import HttpError, ValidationError
from pydantic import ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from postulo.jobs.known import known
from postulo.jobs.models import Capture, CaptureStatus
from postulo.notifications.base import Notification
from postulo.notifications.service import notify
from postulo.plugins.base import CaptureError, JobPostingData
from postulo.plugins.fetching import fetch_page
from postulo.plugins.registry import parse_page

from .auth import TokenAuth, scope
from .models import ApiToken
from .routers import (
    applications,
    companies,
    documents,
    insights,
    interviews,
    listings,
    reminders,
    search,
)
from .schemas import TokenOut

api = NinjaAPI(
    title="Postulo API",
    version="1",
    auth=TokenAuth(),
    urls_namespace="postulo-api",
    docs_url=None,
    description=(
        "Scoped bearer tokens, made under Settings → API tokens. `captures` hands over a "
        "posting; `read` reads everything the owner has; `write` records and changes "
        "through the same services as the forms; `documents:read` downloads files."
    ),
)


class CorrectionsIn(Schema):
    """The fields a person changed after seeing what was read. Every one is optional."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company_name: str | None = None
    location: str | None = None
    remote_type: str | None = None
    employment_type: str | None = None
    description: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    posted_at: dt.date | None = None
    closes_at: dt.date | None = None
    url: str | None = None
    source: str | None = None


class PageIn(Schema):
    """A page somebody wants Postulo to read."""

    url: str = Field(max_length=500)
    html: str | None = Field(
        default=None,
        description=(
            "The page source, if the caller already has it. Supplying it means Postulo "
            "does not fetch the page itself, which is how a browser extension can "
            "capture a posting that is only visible to a signed-in reader."
        ),
    )


class BatchIn(Schema):
    """Where this capture stands among several sent together, from one page.

    A results page holds forty postings and a browser extension sends each as its own
    capture -- the same page, a different ``url`` -- so that a failure loses one and not
    forty (#177). What the server needs to know is only that they belong together: the
    first of the batch is announced, with how many are coming, and the rest arrive quietly.
    Forty notifications for one deliberate gesture would be worse than none.
    """

    size: int = Field(ge=2, le=500, description="How many captures the gesture sends.")
    position: int = Field(ge=1, description="This capture's place in it, from 1.")


class CaptureIn(PageIn):
    """A posting somebody wants Postulo to look at."""

    batch: BatchIn | None = Field(
        default=None,
        description=(
            "Set when several captures are sent together from one page: the first is "
            "announced with the count, the rest are not announced at all."
        ),
    )
    data: CorrectionsIn | None = Field(
        default=None,
        description=(
            "Corrections to what the page is read as, typically made after a preview. "
            "The page is still read; each field given replaces what was read, and the "
            "result is checked exactly as a source's own output is."
        ),
    )


class PreviewOut(Schema):
    url: str
    source: str
    data: JobPostingData


class KnownQueryIn(Schema):
    """One posting to ask about: its address, and its title at its company."""

    url: str = Field(max_length=500)
    title: str = Field(default="", max_length=250)
    company: str = Field(default="", max_length=250)


class KnownIn(Schema):
    """Postings to ask about together -- one for a popup, forty for a results page."""

    postings: list[KnownQueryIn] = Field(max_length=100)


class KnownListingOut(Schema):
    id: int
    title: str
    company_name: str
    url: str
    state: str
    created_at: dt.datetime
    listing_url: str


class KnownCaptureOut(Schema):
    id: int
    title: str
    created_at: dt.datetime
    review_url: str


class KnownOut(Schema):
    """What the owner already holds for one address: told, so they can decide (#178)."""

    url: str
    listings: list[KnownListingOut]
    captures: list[KnownCaptureOut]
    similar: list[KnownListingOut]


class CaptureOut(Schema):
    id: int
    url: str
    title: str
    company_name: str
    location: str
    source: str
    status: str
    created_at: dt.datetime
    review_url: str


def _as_output(request, capture: Capture) -> dict:
    data = capture.data
    return {
        "id": capture.pk,
        "url": capture.url,
        "title": data.get("title", ""),
        "company_name": data.get("company_name", ""),
        "location": data.get("location", ""),
        "source": capture.source_name,
        "status": capture.status,
        "created_at": capture.created_at,
        "review_url": request.build_absolute_uri(reverse("jobs:capture_review", args=[capture.pk])),
    }


@api.get("/me", response=TokenOut, summary="Check a token")
def whoami(request):
    """Confirm a token works, and say who it belongs to and what it may do.

    A client needs some way to tell a mistyped token from a network problem without
    creating anything.
    """
    token: ApiToken = request.auth
    return {
        "name": token.name,
        "owner": token.owner.email,
        "scopes": token.scopes,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
    }


@api.post(
    "/captures",
    response={201: CaptureOut},
    auth=scope("captures"),
    tags=["captures"],
    summary="Capture a posting",
)
def create_capture(request, payload: CaptureIn):
    """Read a posting and store it for review.

    Nothing is created beyond the capture itself. The owner still has to look at it and
    save it before a listing exists, because a parser reading somebody else's markup is
    not a good enough reason to write to their records. Corrections sent with it change
    what the review screen opens with, not that it has to be reviewed.
    """
    token: ApiToken = request.auth
    owner = token.owner

    url, data, source = _read(payload)
    if payload.data is not None:
        corrections = payload.data.model_dump(exclude_unset=True)
        try:
            data = JobPostingData.model_validate({**data.model_dump(), **corrections})
        except PydanticValidationError as exc:
            # Located where the request put it, as the payload's own refusals are.
            raise ValidationError(
                [
                    {**error, "loc": ("body", "payload", "data", *error["loc"])}
                    for error in exc.errors(include_url=False, include_context=False)
                ]
            ) from exc

    batch = payload.batch
    if batch is not None and batch.position > batch.size:
        raise ValidationError(
            [
                {
                    "type": "less_than_equal",
                    "loc": ("body", "payload", "batch", "position"),
                    "msg": str(_("A capture cannot come after the last of its batch.")),
                }
            ]
        )

    capture = Capture.objects.create(
        owner=owner,
        url=url[:500],
        source_name=source.name,
        source_version=getattr(source, "version", ""),
        origin="api",
        data=data.model_dump(mode="json"),
        status=CaptureStatus.PENDING,
    )
    # The one event a person cannot see coming: something arrived from outside. Their
    # notifiers, if any, hear about it; the capture is saved whether or not they do.
    #
    # Unless it came with thirty-nine others because somebody pressed one button: then the
    # first says how many are on their way and the rest say nothing (#177). It is announced
    # on the first rather than the last because the last may never come -- a refused one
    # is retried later, on its own -- and a promise of forty is nearer the truth than
    # silence about all of them.
    review_url = request.build_absolute_uri(reverse("jobs:capture_review", args=[capture.pk]))
    where = " · ".join(part for part in (data.company_name, data.location) if part)
    if batch is None:
        notify(
            owner,
            Notification(
                event="capture_received",
                title=str(_("Captured: %(title)s") % {"title": data.title}),
                body=where,
                url=review_url,
            ),
        )
    elif batch.position == 1:
        notify(
            owner,
            Notification(
                event="capture_received",
                title=str(
                    _("Captured %(count)s postings from %(host)s")
                    % {"count": batch.size, "host": urlsplit(url).hostname or url}
                ),
                body=str(_("The first: %(title)s") % {"title": data.title}),
                url=request.build_absolute_uri(reverse("jobs:capture_list")),
            ),
        )
    return Status(201, _as_output(request, capture))


@api.post(
    "/captures/preview",
    response=PreviewOut,
    auth=scope("captures"),
    tags=["captures"],
    summary="Read a posting without capturing it",
)
def preview_capture(request, payload: PageIn):
    """Say what a page would be captured as, and store nothing.

    For a client that shows the person what was read before sending it, so they can
    correct it first: a browser extension's popup. Nothing is created and nobody is
    notified; the same page sent to ``POST /captures`` afterwards is read again.
    """
    url, data, source = _read(payload)
    return {"url": url, "source": source.name, "data": data}


def _read(payload: PageIn):
    """The page's address, what it was read as, and the source that read it; or a 422."""
    try:
        if payload.html:
            url, html = payload.url, payload.html
        else:
            fetched = fetch_page(payload.url)
            url, html = fetched.url, fetched.html
    except CaptureError as exc:
        raise HttpError(422, str(exc)) from exc

    result = parse_page(url, html)
    if result is None:
        raise HttpError(422, str(_("Nothing resembling a job posting was found there.")))
    data, source = result
    return url, data, source


@api.get(
    "/captures",
    response=list[CaptureOut],
    auth=scope("captures"),
    tags=["captures"],
    summary="List captures awaiting review",
)
def list_captures(request):
    token: ApiToken = request.auth
    captures = Capture.objects.for_user(token.owner).filter(status=CaptureStatus.PENDING)[:50]
    return [_as_output(request, capture) for capture in captures]


@api.post(
    "/captures/known",
    response=list[KnownOut],
    auth=scope("captures"),
    tags=["captures"],
    summary="Ask whether postings have been captured before",
)
def known_captures(request, payload: KnownIn):
    """Say what the owner already holds for each posting, and store nothing.

    For a client that would rather tell the person before sending than show them a
    duplicate afterwards: a listing at that address however it was spelled, a capture of it
    still waiting for review, and -- more softly -- a listing with that title at that
    company, which is how a board that mints a fresh address per visit hides a duplicate.
    Nothing is refused on the strength of it; the answer is theirs (#178).
    """
    owner = request.auth.owner

    def listing(posting) -> dict:
        return {
            "id": posting.pk,
            "title": posting.title,
            "company_name": posting.company.name,
            "url": posting.url,
            "state": posting.state,
            "created_at": posting.created_at,
            "listing_url": request.build_absolute_uri(posting.get_absolute_url()),
        }

    def waiting(capture) -> dict:
        return {
            "id": capture.pk,
            "title": capture.data.get("title", ""),
            "created_at": capture.created_at,
            "review_url": request.build_absolute_uri(
                reverse("jobs:capture_review", args=[capture.pk])
            ),
        }

    answers = []
    for asked in payload.postings:
        seen = known(owner, asked.url, asked.title, asked.company)
        answers.append(
            {
                "url": asked.url,
                "listings": [listing(posting) for posting in seen.listings],
                "captures": [waiting(capture) for capture in seen.captures],
                "similar": [listing(posting) for posting in seen.similar],
            }
        )
    return answers


api.add_router("/applications", applications.router)
api.add_router("/listings", listings.router)
api.add_router("/companies", companies.router)
api.add_router("/reminders", reminders.router)
api.add_router("/interviews", interviews.router)
api.add_router("", documents.router)
api.add_router("/insights", insights.router)
api.add_router("/search", search.router)
