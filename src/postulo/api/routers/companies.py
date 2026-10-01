"""Companies and the people at them."""

import datetime as dt

from django.db.models import Q
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.services import get_or_create_company
from postulo.jobs import identifiers
from postulo.jobs.models import Company, CompanyKind, Contact, Industry

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
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
@paginate(Page, row=lambda request, company: company_out(company))
def list_companies(
    request,
    q: str | None = Query(None, description="Name, location or industry"),
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
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
    return changed_since(companies, updated_since, after_id)


@router.post("", response={201: CompanyDetailOut}, auth=scope("write"), summary="Add a company")
def add_company(request, payload: CompanyIn):
    """Matched by name, case-insensitively, as the forms do: no second Acme."""
    wikidata = next((i.value for i in payload.identifiers if i.scheme == identifiers.WIKIDATA), "")
    company = get_or_create_company(request.auth.owner, payload.name, wikidata=wikidata)
    for field in ("website", "careers_url", "location", "notes"):
        value = getattr(payload, field)
        if value:
            setattr(company, field, value)
    if payload.kind in CompanyKind.values:
        company.kind = payload.kind
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
    from django.core.exceptions import ValidationError

    from postulo.core import phone_numbers, phones, web_links

    company = _detail(request, pk)
    fields = payload.dict()
    # One LinkedIn address in the payload, as there has always been, written to the row
    # that is the contact's primary social profile now (#189). It is read as an address
    # with no service chosen, so its host says which it is and nothing is refused: the
    # field has always taken whatever profile a client had (#305).
    linkedin = (fields.pop("linkedin_url", "") or "").strip()
    links = list(fields.pop("web_links", None) or [])
    social = [row for row in links if row["kind"] == web_links.Kind.SOCIAL]
    if linkedin and not any(row["url"].strip() == linkedin for row in social):
        leads = not any(row["is_primary"] for row in social)
        links.insert(0, {"kind": web_links.Kind.SOCIAL, "url": linkedin, "is_primary": leads})
    # Checked before anything is written: a link filed under a service has to be one of
    # that service's addresses, and a refusal leaves no contact behind it.
    try:
        links = web_links.checked_rows(links)
    except ValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    # One number in the payload, as there has always been, written to the row that holds
    # it. A client sending a number somebody here already has is told so rather than
    # silently given a contact without one.
    number = (fields.pop("phone", "") or "").strip()
    # Checked against its country's numbering plan as the field on the page checks it
    # (#304), and for the same reason: a number that cannot exist is refused with why,
    # and one the plan cannot place is kept as it came. The country is the number's own,
    # where it starts with `+`, and `phone_country` otherwise: the chooser beside the box,
    # for a client that holds a national number and knows where it is. With neither, a
    # national number is refused in the words the page uses. A contact has notes, so a
    # refusal that leaves something out says where it can go.
    country = fields.pop("phone_country", "")
    verdict = phones.check(number, country)
    if verdict.impossible:
        raise HttpError(422, verdict.sentence(notes=True))
    number = phones.combine(number, country)
    if number and phone_numbers.taken_elsewhere(number):
        # The API is the surface a sweep would actually use, so it is bounded exactly as
        # the form is -- one limit, keyed on the account rather than on the token (#142).
        raise HttpError(422, phone_numbers.collision_message(request.auth.owner))
    contact = Contact.objects.create(owner=request.auth.owner, company=company, **fields)
    if number:
        phone_numbers.save_only_number(contact, request.auth.owner, number)
    web_links.add_links(contact, request.auth.owner, links)
    return Status(201, contact_out(contact))
