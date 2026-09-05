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

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from ninja import NinjaAPI, Schema, Status
from ninja.errors import HttpError
from pydantic import Field

from postulo.jobs.models import Capture, CaptureStatus
from postulo.notifications.base import Notification
from postulo.notifications.service import notify
from postulo.plugins.base import CaptureError
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


class CaptureIn(Schema):
    """A posting somebody wants Postulo to look at."""

    url: str = Field(max_length=500)
    html: str | None = Field(
        default=None,
        description=(
            "The page source, if the caller already has it. Supplying it means Postulo "
            "does not fetch the page itself, which is how a browser extension can "
            "capture a posting that is only visible to a signed-in reader."
        ),
    )


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
    not a good enough reason to write to their records.
    """
    token: ApiToken = request.auth
    owner = token.owner

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
    notify(
        owner,
        Notification(
            event="capture_received",
            title=str(_("Captured: %(title)s") % {"title": data.title}),
            body=" · ".join(part for part in (data.company_name, data.location) if part),
            url=request.build_absolute_uri(reverse("jobs:capture_review", args=[capture.pk])),
        ),
    )
    return Status(201, _as_output(request, capture))


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


api.add_router("/applications", applications.router)
api.add_router("/listings", listings.router)
api.add_router("/companies", companies.router)
api.add_router("/reminders", reminders.router)
api.add_router("/interviews", interviews.router)
api.add_router("", documents.router)
api.add_router("/insights", insights.router)
api.add_router("/search", search.router)
