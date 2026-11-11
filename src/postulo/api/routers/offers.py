"""Offers: what was offered, read, recorded, changed and deleted (#237, #1101).

A write goes through the interface's offer form and the services the pages use, so an offer
recorded here is refused, written on the timeline, reminded about and moves the status
exactly as one recorded on the page does.
"""

from django.db import transaction
from django.forms.models import model_to_dict
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.forms import OfferForm
from postulo.applications.models import Application, Offer
from postulo.applications.services import record_offer, revise_offer, withdraw_offer

from ..auth import actor_of, scope
from ..paging import AFTER_ID, UPDATED_SINCE, Moment, Page, RowId, changed_since
from ..problems import form_refused, refuses
from ..schemas import OfferIn, OfferOut, OfferPatch, offer_out
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
    application: RowId | None = Query(None, description="Only this application's"),
    updated_since: Moment | None = Query(None, description=UPDATED_SINCE),
    after_id: RowId | None = Query(None, description=AFTER_ID),
):
    offers = _queryset(request).order_by("-created_at", "-pk")
    if application:
        offers = offers.filter(application_id=application)
    return changed_since(offers, updated_since, after_id)


@router.get("/{int:pk}", response=OfferOut, summary="One offer")
def get_offer(request, pk: int):
    return offer_out(request, owned_or_404(request, _queryset(request), pk))


def _checked(request, data: dict, instance: Offer | None = None) -> OfferForm:
    """The page's own form, given the body: its rules are the rules, here as there."""
    form = OfferForm(data, instance=instance, user=request.auth.owner)
    if not form.is_valid():
        raise form_refused(form)
    return form


@router.post("", response={201: OfferOut}, auth=scope("write"), summary="Record an offer")
@refuses(404)  # an `application_id` that is not the caller's
def add_offer(request, payload: OfferIn):
    """The row, the timeline entry, the answer-by reminder and the move to *Offer*, through
    the service the page records one with."""
    application = owned(request, Application.objects).filter(pk=payload.application_id).first()
    if application is None:
        raise HttpError(404, _("No such application."))
    form = _checked(request, payload.dict(exclude={"application_id"}))
    offer = record_offer(application, actor=actor_of(request), **form.cleaned_data)
    return Status(201, offer_out(request, owned_or_404(request, _queryset(request), offer.pk)))


@router.patch("/{int:pk}", response=OfferOut, auth=scope("write"), summary="Change an offer")
def change_offer(request, pk: int, payload: OfferPatch):
    """Written on the timeline as a revision when a term changed, and not when only the notes
    did; the reminder follows a moved answer-by date. The page's rule, through its service."""
    offer = owned_or_404(request, _queryset(request), pk)
    data = model_to_dict(offer, fields=OfferForm._meta.fields)
    data.update(payload.dict(exclude_unset=True))
    form = _checked(request, data, instance=offer)
    with transaction.atomic():
        offer = form.save()
        revise_offer(offer, changed=form.changed_data, actor=actor_of(request))
    return offer_out(request, owned_or_404(request, _queryset(request), pk))


@router.delete("/{int:pk}", response={204: None}, auth=scope("write"), summary="Delete an offer")
def delete_offer(request, pk: int):
    """As the page deletes one: the row goes, and its answer-by reminder is ticked off."""
    withdraw_offer(owned_or_404(request, _queryset(request), pk), actor=actor_of(request))
    return Status(204, None)
