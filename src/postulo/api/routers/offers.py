"""Offers: what was offered, readable. Recording one is the interface's, for now (#237)."""

import datetime as dt

from ninja import Query, Router
from ninja.pagination import paginate

from postulo.applications.models import Offer

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
from ..schemas import OfferOut, offer_out
from .common import owned, owned_or_404

router = Router(tags=["offers"], auth=scope("read"))


def _queryset(request):
    return owned(request, Offer.objects).select_related(
        "application", "application__posting", "application__posting__company"
    )


@router.get("", response=list[OfferOut], summary="List offers")
@paginate(Page, row=offer_out)
def list_offers(
    request,
    application: int | None = Query(None, description="Only this application's"),
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    offers = _queryset(request).order_by("-created_at", "-pk")
    if application:
        offers = offers.filter(application_id=application)
    return changed_since(offers, updated_since, after_id)


@router.get("/{int:pk}", response=OfferOut, summary="One offer")
def get_offer(request, pk: int):
    return offer_out(request, owned_or_404(request, _queryset(request), pk))
