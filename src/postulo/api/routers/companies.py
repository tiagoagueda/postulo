"""Companies and the people at them."""

from django.db.models import Q
from ninja import Query, Router, Status
from ninja.pagination import paginate

from postulo.applications.services import get_or_create_company
from postulo.jobs.models import Company, Contact

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
from .common import owned, owned_or_404

router = Router(tags=["companies"], auth=scope("read"))


@router.get("", response=list[CompanyOut], summary="List companies")
@paginate
def list_companies(request, q: str | None = Query(None, description="Name, location or industry")):
    companies = owned(request, Company.objects).order_by("name")
    if q:
        companies = companies.filter(
            Q(name__icontains=q) | Q(location__icontains=q) | Q(industry__icontains=q)
        )
    return [company_out(c) for c in companies]


@router.post("", response={201: CompanyDetailOut}, auth=scope("write"), summary="Add a company")
def add_company(request, payload: CompanyIn):
    """Matched by name, case-insensitively, as the forms do: no second Acme."""
    company = get_or_create_company(request.auth.owner, payload.name)
    for field in ("website", "careers_url", "location", "industry", "notes"):
        value = getattr(payload, field)
        if value:
            setattr(company, field, value)
    company.save()
    return Status(201, company_out(_detail(request, company.pk), detail=True))


def _detail(request, pk: int) -> Company:
    return owned_or_404(request, Company.objects.prefetch_related("contacts", "postings"), pk)


@router.get("/{int:pk}", response=CompanyDetailOut, summary="One company, with its contacts")
def get_company(request, pk: int):
    return company_out(_detail(request, pk), detail=True)


@router.patch(
    "/{int:pk}", response=CompanyDetailOut, auth=scope("write"), summary="Change a company"
)
def patch_company(request, pk: int, payload: CompanyPatch):
    company = _detail(request, pk)
    for field, value in payload.dict(exclude_unset=True).items():
        if value is not None:
            setattr(company, field, value)
    company.save()
    return company_out(_detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/contacts",
    response={201: ContactOut},
    auth=scope("write"),
    summary="Add a contact at a company",
)
def add_contact(request, pk: int, payload: ContactIn):
    company = _detail(request, pk)
    contact = Contact.objects.create(owner=request.auth.owner, company=company, **payload.dict())
    return Status(201, contact_out(contact))
