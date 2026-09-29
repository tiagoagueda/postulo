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

A capture may keep the page it was read from (#256): the source is whatever was parsed,
and a rendering is sent afterwards, as a file, to an address of its own. Both go in under
``captures`` and neither comes back out under any scope -- a token that can hand a page
over cannot fetch one.

A listing has a history (#270), and ``listings:bind`` is the scope for a client that only
files things into one -- a mail client attaching the message somebody is reading. It
records an entry and reads the brief list of listings needed to choose where, and reaches
nothing else: ``write`` covers far more than a mail client should hold.

The OpenAPI description is served at ``openapi.json`` under the API root, to a live token
or a signed-in person and to nobody else (#230). There is no documentation page rendered
here: its assets would have to come from a CDN the content security policy forbids, and the
schema is what a client consumes anyway.
"""

# No ``from __future__ import annotations`` here, as in every router: django-ninja builds
# the model for a call's query parameters in its own module, where a postponed annotation
# naming anything but a builtin cannot be resolved.
import datetime as dt
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from ninja import Header, NinjaAPI, Query, Schema, Status
from ninja.errors import HttpError, ValidationError
from ninja.pagination import paginate
from ninja.renderers import JSONRenderer
from ninja.responses import NinjaJSONEncoder
from pydantic import AfterValidator, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from postulo.core import errands, throttle
from postulo.core.addresses import page_address
from postulo.jobs import pages, remembered
from postulo.jobs.known import known
from postulo.jobs.models import Capture, CaptureStatus
from postulo.plugins.base import CaptureError, JobPostingData
from postulo.plugins.fetching import fetch_page

from . import idempotency, problems
from .auth import ScopedAuth, TokenAuth, for_readers_of_the_api, scope
from .models import ApiToken
from .paging import UPDATED_SINCE, Page, changed_since
from .routers import (
    applications,
    companies,
    documents,
    insights,
    interviews,
    listings,
    offers,
    reminders,
    search,
)
from .schemas import TokenOut


class WholeMoments(NinjaJSONEncoder):
    """Writes a moment out whole, to the microsecond.

    Django's encoder — which ninja's extends — rounds a datetime to the millisecond. For
    most fields that is nobody's problem, and for `updated_at` it is fatal: the cursor a
    caller catches up with is a moment this API hands out and then compares against the
    stored value, so handing out `.122` for a row stored at `.122160` names an instant
    *before* the row it came from. Asked again from there, the row comes back, and so does
    every row tied with it — the walk never advances (#245).

    A value this API gives out has to be a value it will take back. `AnswerEncoder` in
    `models.py` already says the same thing about replayed answers; this says it about
    every moment on every row.
    """

    def default(self, o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        return super().default(o)


class Renderer(JSONRenderer):
    encoder_class = WholeMoments


class Described(NinjaAPI):
    """The API, plus a description of what it refuses with (#296).

    django-ninja describes what every call answers with and nothing about what it refuses
    with, so a client generated from the schema had a type for an application and none for
    the 401 it meets first. `problems.describe` fills that in for every call at once.
    """

    def get_openapi_schema(self, *args, **kwargs):
        return problems.describe(
            self, super().get_openapi_schema(*args, **kwargs), scoped=ScopedAuth
        )


api = Described(
    renderer=Renderer(),
    title="Postulo API",
    version="1",
    auth=TokenAuth(),
    urls_namespace="postulo-api",
    docs_url=None,
    docs_decorator=for_readers_of_the_api,
    description=(
        "Scoped bearer tokens, made under Settings → API tokens. `captures` hands over a "
        "posting; `listings:bind` adds to a listing's history and lists the listings to "
        "choose one from, and nothing else; `read` reads everything the owner has; `write` "
        "records and changes through the same services as the forms; `documents:read` "
        "downloads files."
    ),
)


problems.install(api)


@api.exception_handler(throttle.TooOften)
def _too_often(request, exc: throttle.TooOften):
    """A spent allowance, said the way a client can act on: 429, `detail`, `Retry-After`.

    A header rather than only a sentence, because the caller refused here is a program --
    a browser extension sending a results page, a script importing a hundred postings --
    and the useful answer to "not yet" is *when*. `TooOften` already knows how long is
    left of the window.

    The wait is in the body as well as the header since #296: RFC 9457 lets a problem type
    carry its own members, and a client reading the document should not have to reach back
    out to the headers for the one number that tells it what to do next.
    """
    response = problems.refuse(
        request, api, 429, str(exc), kind="rate-limited", retry_after=exc.retry_after
    )
    response["Retry-After"] = str(exc.retry_after)
    return response


#: The address of a page that was read. Only the scheme is held to, for the reason
#: `postulo.core.addresses.page_address` gives.
PageAddress = Annotated[str, AfterValidator(page_address)]


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

    #: Kept as ``Capture.url`` and drawn as a link on the review screen. A caller that
    #: sends its own ``html`` is never fetched, so this is the only thing standing between
    #: a `javascript:` address and that link (#218).
    url: PageAddress = Field(max_length=500)
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


class KeepIn(Schema):
    """What this one capture may keep of its page. It can narrow, and never widen (#256).

    Whether pages are kept at all is decided on the instance and then by the person, and
    nothing a request says can add to that. What a request can do is keep *less*, for one
    capture: a page that addressed somebody by name, captured by somebody who would rather
    it were not kept this time.

    **The source only.** The source arrives in this request, as `html`, because it is what
    is read; so this request is where not keeping it is said. A rendering arrives in a
    request of its own afterwards, and not keeping one is not sending it -- a field here
    saying so would be a promise the server has nothing to keep with.
    """

    model_config = ConfigDict(extra="forbid")

    source: bool = Field(
        default=True,
        description="False: do not keep the source of this page, whatever the account keeps.",
    )


class CaptureIn(PageIn):
    """A posting somebody wants Postulo to look at."""

    keep: KeepIn | None = Field(
        default=None,
        description=(
            "Whether this capture may keep the source of its page. Left out, the account's "
            "own choice applies. It can only narrow that choice: asking for what the "
            "instance or the account has switched off keeps nothing."
        ),
    )
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
    hinted: list[str] = Field(
        default_factory=list,
        description=(
            "The fields of `data` that were read where the owner's own corrections showed "
            "them to be on this site before, rather than from what the page states about "
            "itself. Worth showing as such: a remembered place is a better guess, not a fact."
        ),
    )


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


class KeptOut(Schema):
    """What a capture kept of the page it was read from, and what it would still take.

    Read this before drawing a page: `accepts_rendering` says whether a rendering sent to
    `rendering_url` would be taken, so a client does not go to the trouble of making one
    that would be refused.
    """

    source: bool = Field(description="Whether the source, as it was parsed, is kept.")
    rendering: bool = Field(description="Whether a rendering of the page is kept.")
    rendering_type: str = Field(description="The media type of the kept rendering, or empty.")
    accepts_rendering: bool = Field(
        description="Whether a rendering sent to `rendering_url` now would be taken."
    )
    rendering_url: str = Field(description="Where a rendering is sent, with PUT.")
    rendering_types: list[str] = Field(description="The media types a rendering may be.")
    rendering_max_bytes: int = Field(description="The most a rendering may weigh.")
    note: str = Field(
        default="",
        description=(
            "Why something the account asked to have kept was not, in words for a "
            "person. Empty when everything asked for was kept, or nothing was asked for."
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
    updated_at: dt.datetime
    review_url: str
    page: KeptOut


def _keeping(request, owner):
    """What the owner keeps and how much room is left, counted once for the request.

    A list of fifty captures asks this fifty times, and the answer is about the account
    rather than about any one of them.
    """
    held = getattr(request, "_postulo_keeping", None)
    if held is None or held[0] != owner.pk:
        held = (owner.pk, pages.keeping_for(owner), pages.room_left(owner))
        request._postulo_keeping = held
    return held[1], held[2]


def _kept(request, capture: Capture, *, keeping=None, note: str = "") -> dict:
    """The `page` member: what was kept, what would still be taken, and where to send it."""
    counted, room = _keeping(request, capture.owner)
    return {
        **pages.describe(capture, keeping or counted, room=room),
        "rendering_url": request.build_absolute_uri(
            reverse("postulo-api:attach_rendering", kwargs={"pk": capture.pk})
        ),
        "note": note,
    }


def _as_output(request, capture: Capture, *, keeping=None, note: str = "") -> dict:
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
        "updated_at": capture.updated_at,
        "review_url": request.build_absolute_uri(reverse("jobs:capture_review", args=[capture.pk])),
        "page": _kept(request, capture, keeping=keeping, note=note),
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
def create_capture(
    request,
    payload: CaptureIn,
    idempotency_key: str | None = Header(
        None,
        alias="Idempotency-Key",
        description=(
            "Any string of your own, one per posting. Send the same request again under the "
            "same key — after a lost reply, say — and you get the first answer back rather "
            "than a second capture. Honoured for 24 hours."
        ),
    ),
):
    """Read a posting and store it for review.

    Nothing is created beyond the capture itself. The owner still has to look at it and
    save it before a listing exists, because a parser reading somebody else's markup is
    not a good enough reason to write to their records. Corrections sent with it change
    what the review screen opens with, not that it has to be reviewed.
    """
    token: ApiToken = request.auth
    owner = token.owner

    with idempotency.once(owner, idempotency_key, payload) as answer:
        if answer.held is not None:
            # The same answer the first attempt got: nothing fetched, nothing made, nobody
            # told again. A retry after a lost reply is not a second posting (#230).
            return Status(answer.held_status, answer.held)
        return Status(201, _capture(request, owner, payload, answer))


def _capture(request, owner, payload: CaptureIn, answer) -> dict:
    """Read the page, keep the capture, tell the owner — the body of the call above."""
    url, data, source, html, handed = _read(payload, owner)
    read = data
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
    #
    # **Sent off rather than delivered here (#247).** `notify` walks every notifier the
    # account has and each one is a network timeout; a batch of forty from the extension
    # waited on all of them, forty times, before the fortieth was acknowledged. The words
    # are still written at delivery, in the owner's language, which is why the errand
    # carries the pieces and not a sentence: pre-wording it here would use whatever
    # `Accept-Language` the extension sent -- the language of the browser that found the
    # posting, not a choice anybody made about Postulo (#223).
    if batch is None:
        errands.send(
            "notify",
            owner,
            subject=capture,
            event="capture_received",
            capture_id=capture.pk,
            title=data.title,
            where=" · ".join(part for part in (data.company_name, data.location) if part),
            # Where this instance is reached from, as this request knows it. The worker has
            # no request to ask, and a link nobody can follow is not a link (#247).
            base=request.build_absolute_uri("/"),
            # When the posting arrived, which is not when the errand runs: a notifier that
            # files the message wants the arrival (#229).
            at=capture.created_at.isoformat(),
        )
    elif batch.position == 1:
        errands.send(
            "notify",
            owner,
            subject=capture,
            event="capture_batch",
            count=batch.size,
            host=urlsplit(url).hostname or url,
            title=data.title,
            capture_id=capture.pk,
            base=request.build_absolute_uri("/"),
            at=capture.created_at.isoformat(),
        )

    # What was parsed, kept beside what it was read as, where the instance and the owner
    # have both said so and the request has not asked for less (#256). After the capture
    # and never instead of it: a source that is too large to keep, or an account with no
    # room left, is a sentence in the answer beside a capture that was made. And last of
    # the work, because a file is the one thing here a failed request cannot take back.
    keeping = pages.keeping_for(owner)
    if payload.keep is not None:
        keeping = keeping.narrowed(source=payload.keep.source)
    _page, note = pages.keep_source_quietly(capture, html, keeping)
    # What its review will learn from, and what the corrections sent with it teach now:
    # the same act as correcting a field on the review screen, only earlier (#267).
    if payload.data is not None:
        remembered.after_capture(capture, handed, html, read=read, corrected=data)
    else:
        remembered.after_capture(capture, handed, html)

    body = _as_output(request, capture, keeping=keeping, note=note)
    # Kept before the answer goes out, so that a client which retries because it never saw
    # the answer is retrying against something already written down.
    answer.keep(201, body)
    return body


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
    url, data, source, _html, handed = _read(payload, request.auth.owner)
    # Read with the owner's remembered places, as the capture will be, and scored by
    # nobody: a preview is not a review, and nothing was decided (#267).
    return {
        "url": url,
        "source": source.name,
        "data": data,
        "hinted": remembered.filled_by(handed, data.model_dump()),
    }


def _read(payload: PageIn, owner):
    """The page's address, what it was read as, the source that read it, the text that was
    read, and the owner's remembered places it was read with; or a 422.

    The text is handed back because it is what a capture may keep (#256): exactly what the
    parser was given, whichever way it arrived. The places, because the capture keeps which
    of them filled what, for its review to mark and to score (#267).

    The capture limit is spent here, on the branch that fetches, so that it bounds the *act*
    of making this server dial an address somebody else chose rather than the door that act
    came through. It was applied only on the web form, leaving the same outbound fetch
    available through this API at the API rate -- twenty times as fast, and to a token rather
    than to the person holding it (#194). It is the account's allowance, shared with the form:
    an account is the thing being limited, and a second token is not a second allowance.

    A capture that brings its own ``html`` fetches nothing and is not counted, which is the
    difference from the form. The form counts those because the parse is still work somebody
    asked for, and a form submits one at a time; here they are how the browser extension sends
    a results page -- forty in one gesture (#177) -- and the API rate is what bounds them.
    """
    if not payload.html:
        # Raised, not caught: `_too_often` above turns it into the 429 with its `Retry-After`.
        throttle.capture(owner)

    try:
        if payload.html:
            url, html = payload.url, payload.html
        else:
            fetched = fetch_page(payload.url)
            url, html = fetched.url, fetched.html
    except CaptureError as exc:
        raise HttpError(422, str(exc)) from exc

    result = remembered.read_page(owner, url, html)
    if result is None:
        raise HttpError(422, str(_("Nothing resembling a job posting was found there.")))
    data, source, handed = result
    return url, data, source, html, handed


@api.get(
    "/captures",
    response=list[CaptureOut],
    auth=scope("captures"),
    tags=["captures"],
    summary="List captures awaiting review",
)
@paginate(Page, row=_as_output)
def list_captures(
    request,
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
):
    """What is still waiting to be reviewed, newest first.

    It used to be the first fifty rows in whatever order the database felt like handing
    them over, while the wiki promised every list took ``limit`` and ``offset`` and came
    back as ``{"items", "count"}``. A queue nobody has kept up with runs past fifty, and
    nothing said so (#230).
    """
    token: ApiToken = request.auth
    captures = (
        Capture.objects.for_user(token.owner)
        .filter(status=CaptureStatus.PENDING)
        # Each row says what it kept of its page (#256), which is a row of its own.
        .select_related("page")
        # And not what its review will learn from (#267), which nothing here answers with.
        .defer("learning")
        .order_by("-created_at", "-pk")
    )
    return changed_since(captures, updated_since)


#: How the description of the call below says what it takes: the file itself as the body,
#: under the media type it is. django-ninja describes a body it parsed, and this one is
#: deliberately not parsed -- it is measured and then read a piece at a time.
_A_RENDERING = {
    "requestBody": {
        "required": True,
        "description": (
            "The rendering itself, as the body of the request: not JSON, not a form. "
            "`Content-Type` says which of the four kinds it is and `Content-Length` how "
            "large, and both are checked before the body is read."
        ),
        "content": {
            kind: {"schema": {"type": "string", "format": "binary"}}
            for kind in pages.RenderingKind.values
        },
    },
    "responses": {
        status: {
            "description": problems.PHRASES[status],
            "content": {
                problems.CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}
            },
        }
        for status in (409, 411, 413, 415)
    },
}

#: What each reason a rendering was not kept is answered with. The status is the one an
#: HTTP client already understands; the problem type, where there is one, is what a client
#: written for Postulo branches on.
_REFUSALS: dict[str, tuple[int, str | None]] = {
    "switched-off": (409, "not-kept"),
    "decided": (409, "not-kept"),
    "already-kept": (409, "not-kept"),
    "no-room": (409, "not-kept"),
    "too-large": (413, "too-large"),
    "unsupported": (415, "unsupported-media-type"),
    "no-length": (411, None),
    "empty": (422, None),
    "not-what-it-says": (422, None),
}


def _refused(refusal: pages.NotKept) -> HttpError:
    """A rendering that was not kept, as the refusal a client can act on."""
    status, kind = _REFUSALS.get(refusal.reason, (422, None))
    if kind is None:
        return HttpError(status, str(refusal))
    extensions: dict = {"reason": refusal.reason}
    if kind == "too-large":
        extensions = {"max_bytes": refusal.limit}
    elif kind == "unsupported-media-type":
        extensions = {"accepted": list(pages.RenderingKind.values)}
    return problems.Refused(status, str(refusal), kind=kind, **extensions)


@api.put(
    "/captures/{int:pk}/rendering",
    response={200: CaptureOut, 201: CaptureOut},
    auth=scope("captures"),
    tags=["captures"],
    url_name="attach_rendering",
    summary="Send a rendering of the page a capture was read from",
    openapi_extra=_A_RENDERING,
)
def attach_rendering(request, pk: int):
    """Keep a picture of the whole page beside a capture that is waiting for review.

    For the browser that was looking at the page, which is the only thing that can draw
    it as it looked: a posting behind a sign-in, or behind bot protection, is a page this
    server cannot see at all. The body is the file -- a PNG, JPEG or WebP image, or a PDF
    -- and nothing else.

    **It is measured before it is read.** `Content-Length` is compared with
    `rendering_max_bytes` before a byte is taken, and the bytes are counted again as they
    arrive. A rendering is taken once: the same bytes sent again get the same answer, and
    different ones are refused, because what a capture kept is not replaced from outside.
    Nothing here reads a rendering back; a token that can send one cannot fetch one.
    """
    capture = get_object_or_404(
        Capture.objects.for_user(request.auth.owner).select_related("page"), pk=pk
    )
    held = capture.kept_page
    already = held is not None and bool(held.rendering)
    declared = request.META.get("CONTENT_LENGTH")
    try:
        pages.attach_rendering(
            capture,
            request,
            content_type=request.content_type,
            length=int(declared) if str(declared or "").isdigit() else None,
        )
    except pages.NotKept as refusal:
        raise _refused(refusal) from refusal
    return Status(200 if already else 201, _as_output(request, capture))


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
api.add_router("/offers", offers.router)
api.add_router("", documents.router)
api.add_router("/insights", insights.router)
api.add_router("/search", search.router)


#: The calls whose request is not one transaction, by the name of their address.
#:
#: Every request is a transaction, and on SQLite a transaction takes the write lock when
#: it begins (#206), so a request that waits on something slow holds that lock for every
#: second of the wait while the other workers queue behind it. #220 took the slow pages out
#: of theirs. Sending a rendering is the slow call here: megabytes read off a connection
#: that may be a telephone's, a piece at a time, and none of that reading is a write. What
#: it writes is one row, when the file is whole, in a statement of its own.
OUTSIDE_A_TRANSACTION = frozenset({"attach_rendering"})


def urls():
    """The API's addresses, with the slow calls taken out of the request's transaction.

    Django asks the *address's* own function whether its request is atomic. django-ninja
    makes one such function for each address and none for an operation, so there is
    nothing to decorate where the call is written; they are marked here instead, on the
    very patterns that are handed to the resolver. `api.urls` builds its patterns afresh
    each time it is read, which is why this returns the ones it marked.
    """
    from django.db import transaction

    patterns, application, namespace = api.urls
    for pattern in patterns:
        if getattr(pattern, "name", None) in OUTSIDE_A_TRANSACTION:
            transaction.non_atomic_requests(pattern.callback)
    return patterns, application, namespace
