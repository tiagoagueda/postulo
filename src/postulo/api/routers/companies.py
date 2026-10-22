"""Companies and the people at them."""

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.applications.services import get_or_create_company
from postulo.jobs import company_names, identifiers
from postulo.jobs.models import (
    Company,
    CompanyHiringSignal,
    CompanyKind,
    Contact,
    HiringSignalKind,
    Industry,
    JobPosting,
    LegalState,
)
from postulo.jobs.surface import WriteRefused, create_contact

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Moment, Page, RowId, changed_since
from ..schemas import (
    CompanyDetailOut,
    CompanyIn,
    CompanyOut,
    CompanyPatch,
    ContactIn,
    ContactOut,
    HiringSignalIn,
    HiringSignalOut,
    company_out,
    contact_out,
    signal_out,
)
from .common import choice_or_422, identifiers_or_422, owned, owned_or_404, writable_or_404

#: The signals are read with their authors, so a list of companies is not a query per entry.
SIGNALS = Prefetch("signals", queryset=CompanyHiringSignal.objects.select_related("author"))

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
        .with_notes(request.auth.owner)
        .prefetch_related(
            "industries",
            "identifiers",
            "phone_numbers",
            "postal_addresses",
            "web_links",
            "names",
            SIGNALS,
        )
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
        changes = any(
            getattr(payload, field)
            for field in (
                "website",
                "careers_url",
                "location",
                "kind",
                "email",
                "identifiers",
                "legal_state",
                "legal_state_since",
                "legal_state_source",
                "acquired_by",
            )
        ) or bool(payload.phone_numbers or payload.postal_addresses or payload.web_links)
        changes = changes or bool(payload.industries)
        if (
            changes
            and not Company.objects.visible_to(request.auth.owner, write=True)
            .filter(pk=company.pk)
            .exists()
        ):
            # Found by name among the companies shared with the caller read-only (#896):
            # said so, rather than changed or silently left as it was.
            raise HttpError(403, _("That company is shared with you read-only."))
        for field in ("website", "careers_url", "location"):
            value = getattr(payload, field)
            if value:
                setattr(company, field, value)
        if payload.notes:
            # The caller's own note (#896), never a column of the company.
            company.write_note(request.auth.owner, payload.notes)
        if payload.kind in CompanyKind.values:
            company.kind = payload.kind
        _set_status(
            request,
            company,
            {
                name: getattr(payload, name)
                for name in (
                    "legal_state",
                    "legal_state_since",
                    "legal_state_source",
                    "acquired_by",
                )
                if getattr(payload, name)
            },
        )
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
            links=[row.dict() for row in payload.web_links],
        )
    return Status(201, company_out(_detail(request, company.pk), detail=True))


def _number_rows(numbers) -> list[dict]:
    """The numbers of a payload as rows to write, each checked against its country's
    numbering plan as the page checks it; an impossible one is a 422. The same for a company
    and a contact (#934)."""
    from postulo.core import phones
    from postulo.core.models import PhoneNumber

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
    return rows


def _checked_addresses(addresses) -> list[dict]:
    """The addresses of a payload as rows to write: the kind checked, the country in capitals.
    The same for a company and a contact (#934)."""
    from postulo.core.models import PostalAddress

    rows = []
    for row in addresses:
        if row.kind and row.kind not in PostalAddress.Kind.values:
            raise HttpError(422, _("%(kind)s is not a kind of address.") % {"kind": row.kind})
        rows.append({**row.dict(), "country": (row.country or "").upper()})
    return rows


def _set_status(request, company: Company, data: dict) -> None:
    """The company's legal state from a payload (#941), checked as the form checks it.

    The state is one of the four, the acquirer is a company the caller can see and that
    everybody who can see this one can see too, and the model's own `clean` refuses the
    rest (itself, a loop, an acquirer on a company that was not acquired). A refusal is a
    422 naming the reason, and the caller's transaction undoes what came before it.
    """
    from django.core.exceptions import ValidationError

    from postulo.jobs import sharing

    if not data:
        return
    owner = request.auth.owner
    if "legal_state" in data:
        company.legal_state = choice_or_422(data["legal_state"], LegalState, field="legal_state")
    if "legal_state_since" in data:
        company.legal_state_since = data["legal_state_since"]
    if "legal_state_source" in data:
        company.legal_state_source = data["legal_state_source"] or ""
    if "acquired_by" in data:
        target_id = data["acquired_by"]
        target = (
            None
            if target_id is None
            else Company.objects.visible_to(owner).filter(pk=target_id).first()
        )
        if target_id is not None and target is None:
            raise HttpError(422, _("acquired_by must be a company you can see."))
        refusal = sharing.check_link(company, target, owner) if company.pk else ""
        if refusal:
            raise HttpError(422, str(refusal))
        company.acquired_by = target
    try:
        company.clean()
    except ValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc


def _write_reached(
    company, owner, *, email=None, numbers=None, addresses=None, links=None, replace=False
):
    """Its mailbox, numbers and addresses (#329), checked as the page checks them.

    A number is checked against its country's numbering plan; an impossible one is a 422
    and nothing is written. A company's number is one the person recorded, so it collides
    with nothing on the instance; one the company already lists is not listed twice.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from postulo.core import importer

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
        rows = _number_rows(numbers)
        if replace:
            company.phone_numbers.all().delete()
        importer._restore_phone_numbers(company, owner, rows)
    if addresses is not None:
        rows = _checked_addresses(addresses)
        if replace:
            company.postal_addresses.all().delete()
        importer._restore_postal_addresses(
            company, owner, importer._address_rows({"postal_addresses": rows})
        )
    if links is not None:
        # Its social profiles (#906), checked as a contact's are: a link filed under a
        # service has to be one of that service's addresses. A company holds the social
        # kind alone, so another kind is refused rather than kept where nothing shows it.
        from postulo.core import web_links

        if any(row.get("kind") not in web_links.COMPANY_KINDS for row in links):
            raise HttpError(422, _("A company's links are its social profiles."))
        try:
            links = web_links.checked_rows(links)
        except ValidationError as exc:
            raise HttpError(422, "; ".join(exc.messages)) from exc
        if replace:
            company.web_links.all().delete()
        web_links.add_links(company, owner, links)


def _detail(request, pk: int, *, write: bool = False) -> Company:
    owner = request.auth.owner
    companies = (
        # The caller's own notes on the company and on its people (#896), and only the
        # people and listings at it the caller can see.
        Company.objects.with_notes(owner).prefetch_related(
            SIGNALS,
            Prefetch("contacts", queryset=Contact.objects.visible_to(owner).with_notes(owner)),
            Prefetch("postings", queryset=JobPosting.objects.visible_to(owner)),
            "industries",
            "identifiers",
            "phone_numbers",
            "postal_addresses",
            "web_links",
            "names",
        )
    )
    if write:
        return writable_or_404(request, companies, pk)
    return owned_or_404(request, companies, pk)


@router.get("/{int:pk}", response=CompanyDetailOut, summary="One company, with its contacts")
def get_company(request, pk: int):
    return company_out(_detail(request, pk), detail=True)


def _name_or_refusal(request, company: Company, name: str) -> str:
    """The new name, or the form's refusals: an empty one is a 422, and so is another company's."""
    name = name.strip()
    if not name:
        raise HttpError(422, _("This field is required."))
    # Among the company's own owner's (#896): a person's or a group's.
    # A grantee asking is bounded and, once spent, answered alike (#949): `TooOften` is a 429.
    if company_names.clashes(request.auth.owner, company.principal, name, company=company):
        raise HttpError(422, _("You already have a company with that name."))
    return name


@router.patch(
    "/{int:pk}", response=CompanyDetailOut, auth=scope("write"), summary="Change a company"
)
def patch_company(request, pk: int, payload: CompanyPatch):
    company = _detail(request, pk, write=True)
    data = payload.dict(exclude_unset=True)
    industries = data.pop("industries", None)
    data.pop("identifiers", None)
    email = data.pop("email", None)
    notes = data.pop("notes", None)
    data.pop("phone_numbers", None)
    data.pop("postal_addresses", None)
    data.pop("web_links", None)
    # Its legal state (#941): handled together, so the state and its acquirer are checked
    # as one thing. Null clears a date, an address or an acquirer; the state itself cannot
    # be null.
    status = {
        name: data.pop(name)
        for name in ("legal_state", "legal_state_since", "legal_state_source", "acquired_by")
        if name in data
    }
    if status.get("legal_state", "") is None:
        status.pop("legal_state")
    if data.get("name") is not None:
        data["name"] = _name_or_refusal(request, company, data["name"])
    with transaction.atomic():  # a refusal below undoes the saves above it (#435)
        for field, value in data.items():
            if value is not None:
                setattr(company, field, value)
        _set_status(request, company, status)
        company.save()
        if data or status:
            from postulo.jobs import trail

            trail.record(
                company,
                request.auth.owner,
                trail.EDITED,
                fields=[name for name, value in data.items() if value is not None] + list(status),
                via=request.auth.name,
            )
        if notes is not None:
            company.write_note(request.auth.owner, notes)
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
            links=(
                [row.dict() for row in payload.web_links] if payload.web_links is not None else None
            ),
            replace=True,
        )
    return company_out(_detail(request, pk), detail=True)


@router.post(
    "/{int:pk}/signals",
    response={201: HiringSignalOut},
    auth=scope("write"),
    summary="Add a hiring signal to a company",
)
def add_signal(request, pk: int, payload: HiringSignalIn):
    """A dated entry about what the company is doing about hiring. Shared with the company,
    and recorded with the caller as its author."""
    from django.utils import timezone

    from postulo.jobs import status, trail

    company = _detail(request, pk, write=True)
    kind = choice_or_422(payload.kind, HiringSignalKind, field="kind")
    day = payload.noted_on or timezone.localdate()
    if day > timezone.localdate():
        raise HttpError(422, _("The date cannot be in the future."))
    with transaction.atomic():
        row = CompanyHiringSignal.objects.create(
            company=company,
            kind=kind,
            noted_on=day,
            source=payload.source,
            author=request.auth.owner,
        )
        trail.record(
            company,
            request.auth.owner,
            trail.EDITED,
            fields=["hiring_signals"],
            via=request.auth.name,
        )
        status.touch(company)
    return Status(201, signal_out(row))


@router.delete(
    "/{int:pk}/signals/{int:signal_id}",
    response={204: None},
    auth=scope("write"),
    summary="Remove a hiring signal",
)
def remove_signal(request, pk: int, signal_id: int):
    from django.shortcuts import get_object_or_404

    from postulo.jobs import status, trail

    company = _detail(request, pk, write=True)
    row = get_object_or_404(CompanyHiringSignal, pk=signal_id, company=company)
    with transaction.atomic():
        row.delete()
        trail.record(
            company,
            request.auth.owner,
            trail.EDITED,
            fields=["hiring_signals"],
            via=request.auth.name,
        )
        status.touch(company)
    return Status(204, None)


@router.post(
    "/{int:pk}/contacts",
    response={201: ContactOut},
    auth=scope("write"),
    summary="Add a contact at a company",
)
def add_contact(request, pk: int, payload: ContactIn):
    from django.core.exceptions import ValidationError

    from postulo.core import importer, messaging_handles, phones, web_links

    # Write access, not read (#328): a company shared read-only is a 404 here, as it is for
    # a change to the company itself.
    company = _detail(request, pk, write=True)
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
    # The lists, checked as a company's are; the single number is the earlier shape of the
    # same thing (deprecated, #934) and joins them as the primary when none of them is.
    number_rows = _number_rows(payload.phone_numbers)
    address_rows = _checked_addresses(payload.postal_addresses)
    fields.pop("phone_numbers", None)
    fields.pop("postal_addresses", None)
    single = (fields.pop("phone", "") or "").strip()
    country = fields.pop("phone_country", "")
    if single:
        verdict = phones.check(single, country)
        if verdict.impossible:
            raise HttpError(422, verdict.sentence(notes=True))
        single = phones.combine(single, country)
        if phones.normalise(single) not in {phones.normalise(r["number"]) for r in number_rows}:
            leads = not any(r["is_primary"] for r in number_rows)
            number_rows.insert(0, {"number": single, "kind": "", "label": "", "is_primary": leads})
    notes = fields.pop("notes", "") or ""
    # The one service that makes a contact (#328): the same checks as every writer, and the
    # change in the contact's trail under this token's name.
    try:
        contact = create_contact(
            company, actor=request.auth.owner, via=request.auth.name, notes=notes, **fields
        )
    except WriteRefused as exc:
        raise HttpError(422, str(exc)) from exc
    # A contact's number is one this account recorded, never one it is, so it collides with
    # nothing on the instance and nothing is asked or spent (#329; the bound of #142 and
    # #374 is for a person's own numbers).
    importer._restore_phone_numbers(contact, request.auth.owner, number_rows)
    importer._restore_postal_addresses(
        contact, request.auth.owner, importer._address_rows({"postal_addresses": address_rows})
    )
    web_links.add_links(contact, request.auth.owner, links)
    messaging_handles.add_handles(contact, request.auth.owner, handles)
    return Status(201, contact_out(contact))
