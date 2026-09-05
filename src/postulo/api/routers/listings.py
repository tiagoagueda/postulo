"""Listings: the stage before applications, readable and decidable through the API."""

from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.models import Channel
from postulo.applications.models import Status as ApplicationStatus
from postulo.applications.services import apply_to_listing, create_listing, get_or_create_company
from postulo.jobs.models import LISTING_FILTERS, DiscardReason, JobPosting

from ..auth import actor_of, scope
from ..schemas import (
    ApplicationDetailOut,
    ApplicationDetailsIn,
    DiscardIn,
    ListingDetailOut,
    ListingIn,
    ListingOut,
    application_out,
    listing_out,
)
from .common import choice_or_422, owned, owned_or_404, priority_or_422, tags_named

router = Router(tags=["listings"], auth=scope("read"))


def _queryset(request):
    return (
        owned(request, JobPosting.objects)
        .select_related("company")
        .with_application_count()
        .prefetch_related("applications")
    )


@router.get("", response=list[ListingOut], summary="List listings")
@paginate
def list_listings(
    request,
    state: str = Query(
        "undecided",
        description="undecided (default), new, shortlisted, discarded, applied, closed or all",
    ),
    company: int | None = Query(None),
):
    listings = _queryset(request)
    if state == "undecided":
        listings = listings.undecided()
    elif state in LISTING_FILTERS:
        listings = listings.in_state(state)
    elif state != "all":
        raise HttpError(422, f"'state' must be undecided, all or one of {list(LISTING_FILTERS)}.")
    if company:
        listings = listings.filter(company_id=company)
    return [listing_out(request, p) for p in listings.order_by("-noted_at", "-pk")]


@router.post("", response={201: ListingDetailOut}, auth=scope("write"), summary="Add a listing")
def add_listing(request, payload: ListingIn):
    owner = request.auth.owner
    company = get_or_create_company(owner, payload.company_name)
    listing = create_listing(owner, company=company, posting_data=payload.posting_data())
    return Status(
        201,
        listing_out(request, owned_or_404(request, _queryset(request), listing.pk), detail=True),
    )


@router.get("/{int:pk}", response=ListingDetailOut, summary="One listing")
def get_listing(request, pk: int):
    return listing_out(request, owned_or_404(request, _queryset(request), pk), detail=True)


@router.post(
    "/{int:pk}/apply",
    response={201: ApplicationDetailOut},
    auth=scope("write"),
    summary="Apply: turn a listing into an application",
)
def apply(request, pk: int, payload: ApplicationDetailsIn):
    listing = owned_or_404(request, _queryset(request), pk)
    choice_or_422(payload.status, ApplicationStatus, field="status")
    choice_or_422(payload.channel, Channel, field="channel", allow_blank=True)
    priority_or_422(payload.priority)
    application = apply_to_listing(listing, payload.application_data(), actor=actor_of(request))
    application.tags.set(tags_named(request.auth.owner, payload.tags))
    from postulo.applications.models import Application

    fresh = owned_or_404(
        request,
        Application.objects.select_related("posting", "posting__company")
        .prefetch_related(
            "tags", "events", "reminders", "interviews__contacts", "rendered_documents"
        )
        .with_next_interview(),
        application.pk,
    )
    return Status(201, application_out(request, fresh, detail=True))


@router.post(
    "/{int:pk}/shortlist", response=ListingDetailOut, auth=scope("write"), summary="Shortlist"
)
def shortlist(request, pk: int):
    listing = owned_or_404(request, _queryset(request), pk)
    listing.shortlist()
    return listing_out(request, owned_or_404(request, _queryset(request), pk), detail=True)


@router.post("/{int:pk}/discard", response=ListingDetailOut, auth=scope("write"), summary="Discard")
def discard(request, pk: int, payload: DiscardIn):
    listing = owned_or_404(request, _queryset(request), pk)
    reason = choice_or_422(payload.reason, DiscardReason, field="reason")
    listing.discard(reason)
    return listing_out(request, owned_or_404(request, _queryset(request), pk), detail=True)


@router.post("/{int:pk}/restore", response=ListingDetailOut, auth=scope("write"), summary="Restore")
def restore(request, pk: int):
    listing = owned_or_404(request, _queryset(request), pk)
    listing.restore()
    return listing_out(request, owned_or_404(request, _queryset(request), pk), detail=True)
