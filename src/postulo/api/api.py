"""The capture API.

Small on purpose. It exists so that something outside Postulo can hand over a posting,
and it can do nothing else: there is no way through this API to read an application, a
CV, or anything else the owner has. A token that leaks costs its holder the ability to
add captures somebody will then decline on the review screen.

The browser extension planned for later is a client of this and nothing more. Building
the interface first means the extension is an addition rather than a reason to rewrite
anything.
"""

from __future__ import annotations

import datetime as dt

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from ninja import NinjaAPI, Schema, Status
from ninja.errors import HttpError
from ninja.security import HttpBearer
from pydantic import Field

from postulo.jobs.models import Capture, CaptureStatus
from postulo.plugins.base import CaptureError
from postulo.plugins.fetching import fetch_page
from postulo.plugins.registry import parse_page

from .models import CaptureToken


class CaptureTokenAuth(HttpBearer):
    """Authenticate with a capture token presented as a bearer token."""

    def authenticate(self, request, token: str):
        if not token:
            return None
        record = (
            CaptureToken.objects.active()
            .select_related("owner")
            .filter(token_hash=CaptureToken.hash_token(token))
            .first()
        )
        if record is None or not record.owner.is_active:
            return None
        record.record_use()
        # The rest of the API reads request.auth.owner; nothing here logs the caller in,
        # so a capture token can never be mistaken for a session.
        return record


api = NinjaAPI(
    title="Postulo capture API",
    version="1.0",
    auth=CaptureTokenAuth(),
    urls_namespace="postulo-api",
    docs_url=None,
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


class TokenOut(Schema):
    name: str
    owner: str
    last_used_at: dt.datetime | None


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
    """Confirm a token works, and say who it belongs to.

    A client needs some way to tell a mistyped token from a network problem without
    creating anything.
    """
    token: CaptureToken = request.auth
    return {
        "name": token.name,
        "owner": token.owner.email,
        "last_used_at": token.last_used_at,
    }


@api.post("/captures", response={201: CaptureOut}, summary="Capture a posting")
def create_capture(request, payload: CaptureIn):
    """Read a posting and store it for review.

    Nothing is created beyond the capture itself. The owner still has to look at it and
    accept it before an application exists, because a parser reading somebody else's
    markup is not a good enough reason to write to their records.
    """
    token: CaptureToken = request.auth
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
    return Status(201, _as_output(request, capture))


@api.get("/captures", response=list[CaptureOut], summary="List captures awaiting review")
def list_captures(request):
    token: CaptureToken = request.auth
    captures = Capture.objects.for_user(token.owner).filter(status=CaptureStatus.PENDING)[:50]
    return [_as_output(request, capture) for capture in captures]
