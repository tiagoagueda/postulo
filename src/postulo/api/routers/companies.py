"""Companies and the people at them."""

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.services import get_or_create_company
from postulo.core import slugs
from postulo.jobs import identifiers
from postulo.jobs.models import Company, CompanyKind, Contact, Industry

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Moment, Page, RowId, changed_since
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
    updated_since: Moment | None = Query(None, description=UPDATED_SINCE),
    after_id: RowId | None = Query(None, description=AFTER_ID),
):
    companies = (
        owned(request, Company.objects)
        .prefetch_related("industries", "identifiers", "phone_numbers", "postal_addresses")
        .order_by("name", "pk")
    )
    if q:
        companies = companies.filter(
            Q(name__icontains=q)
            | Q(postal_addresses__municipality__icontains=q)
            | Q(industries__name__icontains=q)
            | Q(identifiers__value__icontains=q)
        ).distinct()
    return changed_since(companies, updated_since, after_id)


@router.post("", response={201: CompanyDetailOut}, auth=scope("write"), summary="Add a company")
def add_company(request, payload: CompanyIn):
    """Matched by name, case-insensitively, as the forms do: no second Acme."""
    wikidata = next((i.value for i in payload.identifiers if i.scheme == identifiers.WIKIDATA), "")
    # One transaction for the whole write: a refusal is an `HttpError` the handler turns
    # into a response, so the request still commits, and what was saved before the refusal
    # would stay (#435).
    with transaction.atomic():
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
        _write_reached(
            company,
            request.auth.owner,
            email=payload.email or None,
            numbers=payload.phone_numbers,
            addresses=payload.postal_addresses,
        )
    return Status(201, company_out(_detail(request, company.pk), detail=True))


def _write_reached(company, owner, *, email=None, numbers=None, addresses=None, replace=False):
    """Its mailbox, numbers and addresses (#329), checked as the page checks them.

    A number is checked against its country's numbering plan; an impossible one is a 422
    and nothing is written. A company's number is one the person recorded, so it collides
    with nothing on the instance; one the company already lists is not listed twice.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from postulo.core import importer, phones
    from postulo.core.models import PhoneNumber, PostalAddress

    if email is not None:
        email = email.strip()
        if email:
            try:
                validate_email(email)
            except ValidationError as exc:
                raise HttpError(422, "; ".join(exc.messages)) from exc
        company.email = email
        company.save(update_fields=["email", "updated_at"])
    if numbers is not None:
        rows = []
        for row in numbers:
            if row.kind and row.kind not in PhoneNumber.Kind.values:
                raise HttpError(422, _("%(kind)s is not a kind of number.") % {"kind": row.kind})
            verdict = phones.check(row.number, row.country)
            if verdict.impossible:
                raise HttpError(422, verdict.sentence(notes=True))
            rows.append(
                {
                    "number": phones.combine(row.number, row.country),
                    "kind": row.kind,
                    "label": row.label if row.kind == PhoneNumber.Kind.OTHER else "",
                    "is_primary": row.is_primary,
                }
            )
        if replace:
            company.phone_numbers.all().delete()
        importer._restore_phone_numbers(company, owner, rows)
    if addresses is not None:
        rows = []
        for row in addresses:
            if row.kind and row.kind not in PostalAddress.Kind.values:
                raise HttpError(422, _("%(kind)s is not a kind of address.") % {"kind": row.kind})
            rows.append({**row.dict(), "country": (row.country or "").upper()})
        if replace:
            company.postal_addresses.all().delete()
        importer._restore_postal_addresses(
            company, owner, importer._address_rows({"postal_addresses": rows})
        )


def _detail(request, pk: int) -> Company:
    return owned_or_404(
        request,
        Company.objects.prefetch_related(
            "contacts",
            "postings",
            "industries",
            "identifiers",
            "phone_numbers",
            "postal_addresses",
        ),
        pk,
    )


@router.get("/{int:pk}", response=CompanyDetailOut, summary="One company, with its contacts")
def get_company(request, pk: int):
    return company_out(_detail(request, pk), detail=True)


def _name_or_refusal(request, company: Company, name: str) -> str:
    """The new name, or the form's refusals: an empty one is a 422, and so is another company's."""
    name = name.strip()
    if not name:
        raise HttpError(422, _("This field is required."))
    clash = Company.objects.for_user(request.auth.owner).filter(name_key=slugs.name_key(name))
    if clash.exclude(pk=company.pk).exists():
        raise HttpError(422, _("You already have a company with that name."))
    return name


@router.patch(
    "/{int:pk}", response=CompanyDetailOut, auth=scope("write"), summary="Change a company"
)
def patch_company(request, pk: int, payload: CompanyPatch):
    company = _detail(request, pk)
    data = payload.dict(exclude_unset=True)
    industries = data.pop("industries", None)
    data.pop("identifiers", None)
    email = data.pop("email", None)
    data.pop("phone_numbers", None)
    data.pop("postal_addresses", None)
    if data.get("name") is not None:
        data["name"] = _name_or_refusal(request, company, data["name"])
    with transaction.atomic():  # a refusal below undoes the saves above it (#435)
        for field, value in data.items():
            if value is not None:
                setattr(company, field, value)
        company.save()
        if industries is not None:
            company.industries.set(Industry.named(request.auth.owner, industries))
        if payload.identifiers is not None:
            identifiers_or_422(company, payload.identifiers, replace=True)
        _write_reached(
            company,
            request.auth.owner,
            email=email,
            numbers=payload.phone_numbers,
            addresses=payload.postal_addresses,
            replace=True,
        )
    return company_out(_detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/contacts",
    response={201: ContactOut},
    auth=scope("write"),
    summary="Add a contact at a company",
)
def add_contact(request, pk: int, payload: ContactIn):
    from django.core.exceptions import ValidationError

    from postulo.core import messaging_handles, phone_numbers, phones, web_links

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
    handles = list(fields.pop("messaging_handles", None) or [])
    try:
        handles = messaging_handles.checked_rows(handles)
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
    # A contact's number is one this account recorded, never one it is, so it collides with
    # nothing on the instance and nothing is asked or spent (#329; the bound of #142 and
    # #374 is for a person's own numbers).
    number = phones.combine(number, country)
    contact = Contact.objects.create(owner=request.auth.owner, company=company, **fields)
    if number:
        phone_numbers.save_only_number(contact, request.auth.owner, number)
    web_links.add_links(contact, request.auth.owner, links)
    messaging_handles.add_handles(contact, request.auth.owner, handles)
    return Status(201, contact_out(contact))
