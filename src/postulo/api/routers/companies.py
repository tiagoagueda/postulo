"""Companies and the people at them."""

from django.db.models import Q
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.services import get_or_create_company
from postulo.jobs import identifiers
from postulo.jobs.models import Company, Contact, Industry

from ..auth import scope
from ..schemas import (
    CompanyDetailOut,
    CompanyIn,
    CompanyOut,
    CompanyPatch,
    ContactIn,
    ContactOut,
    company_out,
    contact_out,
)
from .common import identifiers_or_422, owned, owned_or_404

router = Router(tags=["companies"], auth=scope("read"))


@router.get("", response=list[CompanyOut], summary="List companies")
@paginate
def list_companies(request, q: str | None = Query(None, description="Name, location or industry")):
    companies = (
        owned(request, Company.objects)
        .prefetch_related("industries", "identifiers")
        .order_by("name")
    )
    if q:
        companies = companies.filter(
            Q(name__icontains=q)
            | Q(location__icontains=q)
            | Q(industries__name__icontains=q)
            | Q(identifiers__value__icontains=q)
        ).distinct()
    return [company_out(c) for c in companies]


@router.post("", response={201: CompanyDetailOut}, auth=scope("write"), summary="Add a company")
def add_company(request, payload: CompanyIn):
    """Matched by name, case-insensitively, as the forms do: no second Acme."""
    wikidata = next((i.value for i in payload.identifiers if i.scheme == identifiers.WIKIDATA), "")
    company = get_or_create_company(request.auth.owner, payload.name, wikidata=wikidata)
    for field in ("website", "careers_url", "location", "notes"):
        value = getattr(payload, field)
        if value:
            setattr(company, field, value)
    company.save()
    if payload.industries:
        company.industries.add(*Industry.named(request.auth.owner, payload.industries))
    if payload.identifiers:
        identifiers_or_422(company, payload.identifiers)
    return Status(201, company_out(_detail(request, company.pk), detail=True))


def _detail(request, pk: int) -> Company:
    return owned_or_404(
        request,
        Company.objects.prefetch_related("contacts", "postings", "industries", "identifiers"),
        pk,
    )


@router.get("/{int:pk}", response=CompanyDetailOut, summary="One company, with its contacts")
def get_company(request, pk: int):
    return company_out(_detail(request, pk), detail=True)


@router.patch(
    "/{int:pk}", response=CompanyDetailOut, auth=scope("write"), summary="Change a company"
)
def patch_company(request, pk: int, payload: CompanyPatch):
    company = _detail(request, pk)
    data = payload.dict(exclude_unset=True)
    industries = data.pop("industries", None)
    data.pop("identifiers", None)
    for field, value in data.items():
        if value is not None:
            setattr(company, field, value)
    company.save()
    if industries is not None:
        company.industries.set(Industry.named(request.auth.owner, industries))
    if payload.identifiers is not None:
        identifiers_or_422(company, payload.identifiers, replace=True)
    return company_out(_detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/contacts",
    response={201: ContactOut},
    auth=scope("write"),
    summary="Add a contact at a company",
)
def add_contact(request, pk: int, payload: ContactIn):
    from postulo.core import phone_numbers

    company = _detail(request, pk)
    fields = payload.dict()
    # One number in the payload, as there has always been, written to the row that holds
    # it. A client sending a number somebody here already has is told so rather than
    # silently given a contact without one.
    number = (fields.pop("phone", "") or "").strip()
    if number and phone_numbers.taken_elsewhere(number):
        # The API is the surface a sweep would actually use, so it is bounded exactly as
        # the form is -- one limit, keyed on the account rather than on the token (#142).
        raise HttpError(422, phone_numbers.collision_message(request.auth.owner))
    contact = Contact.objects.create(owner=request.auth.owner, company=company, **fields)
    if number:
        phone_numbers.save_only_number(contact, request.auth.owner, number)
    return Status(201, contact_out(contact))
