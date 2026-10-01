"""Listings: the stage before applications, readable and decidable through the API.

And a listing's history (#270): what arrived about it, recorded by a client that holds
`listings:bind` -- a mail client filing the message somebody is reading -- or `write`, and
the brief list of listings such a client needs to choose one. That scope reaches those two
calls and nothing else.
"""

import datetime as dt

from django.db.models import Q
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.models import Channel
from postulo.applications.models import Status as ApplicationStatus
from postulo.applications.services import apply_to_listing, create_listing, get_or_create_company
from postulo.documents.models import UploadedDocument
from postulo.jobs.history import record_listing_event
from postulo.jobs.models import (
    LISTING_FILTERS,
    SYSTEM_LISTING_EVENT_KINDS,
    Contact,
    DiscardReason,
    JobPosting,
    ListingEventKind,
)

from ..auth import actor_of, scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
from ..schemas import (
    ApplicationDetailOut,
    ApplicationDetailsIn,
    DiscardIn,
    ListingChoiceOut,
    ListingDetailOut,
    ListingEventIn,
    ListingEventOut,
    ListingIn,
    ListingOut,
    application_out,
    listing_choice_out,
    listing_event_out,
    listing_out,
)
from .common import (
    choice_or_422,
    owned,
    owned_or_404,
    priority_or_422,
    referrer_and_agency_or_422,
    tags_named,
)

router = Router(tags=["listings"], auth=scope("read"))


def _queryset(request):
    return (
        owned(request, JobPosting.objects)
        .select_related("company")
        .with_application_count()
        .prefetch_related("applications")
    )


@router.get("", response=list[ListingOut], summary="List listings")
@paginate(Page, row=listing_out)
def list_listings(
    request,
    state: str = Query(
        "undecided",
        description="undecided (default), new, shortlisted, discarded, applied, closed or all",
    ),
    company: int | None = Query(None),
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    listings = _queryset(request)
    if state == "undecided":
        listings = listings.undecided()
    elif state in LISTING_FILTERS:
        listings = listings.in_state(state)
    elif state != "all":
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s.")
            % {"field": "'state'", "choices": ["undecided", "all", *LISTING_FILTERS]},
        )
    if company:
        listings = listings.filter(company_id=company)
    return changed_since(listings.order_by("-noted_at", "-pk"), updated_since, after_id)


@router.get(
    "/choices",
    response=list[ListingChoiceOut],
    auth=scope("listings:bind", "read"),
    summary="List listings briefly, to choose one",
)
@paginate(Page, row=listing_choice_out)
def list_listing_choices(
    request,
    q: str = Query("", max_length=200, description="Words in the title or the company's name"),
    state: str = Query(
        "all",
        description="all (default), undecided, new, shortlisted, discarded, applied or closed",
    ),
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    """The person's listings, as little of each as recognising it takes (#270).

    For a client filing something into a listing's history, which has to offer a choice of
    listing and must not read the search to do it: a title, a company, a place and a state.
    Newest noted first, and ``q`` narrows to the ones whose title or company says it.
    """
    listings = owned(request, JobPosting.objects).select_related("company")
    if state == "all":
        listings = listings.with_application_count()
    elif state == "undecided":
        listings = listings.undecided()
    elif state in LISTING_FILTERS:
        listings = listings.in_state(state)
    else:
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s.")
            % {"field": "'state'", "choices": ["all", "undecided", *LISTING_FILTERS]},
        )
    words = q.strip()
    if words:
        listings = listings.filter(Q(title__icontains=words) | Q(company__name__icontains=words))
    return changed_since(listings.order_by("-noted_at", "-pk"), updated_since, after_id)


@router.post(
    "/{int:pk}/events",
    response={200: ListingEventOut, 201: ListingEventOut},
    auth=scope("listings:bind", "write"),
    summary="Add an entry to a listing's history",
)
def add_listing_event(request, pk: int, payload: ListingEventIn):
    """Record what arrived about a listing: a message, an email, a call, a note, a file (#270).

    Written through the same function as the listing's own page, signed with the token's
    name. The contact and the file are ids of the caller's own, looked up before anything is
    written, and one that is somebody else's gets the same answer as one that does not
    exist. A capture entry is not offered: the review screen writes those, pointing at the
    capture. Sent again with the same ``external_id``, the first entry comes back with 200.
    """
    listing = owned_or_404(request, owned(request, JobPosting.objects), pk)
    allowed = [
        value for value in ListingEventKind.values if value not in SYSTEM_LISTING_EVENT_KINDS
    ]
    if payload.kind not in allowed:
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s; got %(value)s.")
            % {"field": "'kind'", "choices": sorted(allowed), "value": repr(payload.kind)},
        )
    contact = None
    if payload.contact_id is not None:
        contact = owned(request, Contact.objects).filter(pk=payload.contact_id).first()
        if contact is None:
            raise HttpError(
                422, _("%(field)s is not one of your contacts.") % {"field": "'contact_id'"}
            )
    document = None
    if payload.document_id is not None:
        document = owned(request, UploadedDocument.objects).filter(pk=payload.document_id).first()
        if document is None:
            raise HttpError(
                422, _("%(field)s is not one of your files.") % {"field": "'document_id'"}
            )
    if payload.kind == ListingEventKind.DOCUMENT and document is None:
        raise HttpError(
            422,
            _("A %(kind)s entry names the file: send %(field)s.")
            % {"kind": "'document'", "field": "'document_id'"},
        )
    event, created = record_listing_event(
        listing,
        kind=payload.kind,
        summary=payload.summary,
        body=payload.body,
        occurred_at=payload.occurred_at,
        actor=actor_of(request),
        contact=contact,
        artefact=document,
        external_id=payload.external_id,
    )
    return Status(201 if created else 200, listing_event_out(event))


@router.post("", response={201: ListingDetailOut}, auth=scope("write"), summary="Add a listing")
def add_listing(request, payload: ListingIn):
    owner = request.auth.owner
    company = get_or_create_company(owner, payload.company_name, wikidata=payload.company_wikidata)
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
    named = referrer_and_agency_or_422(request, payload)
    application = apply_to_listing(
        listing, {**payload.application_data(), **named}, actor=actor_of(request)
    )
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
