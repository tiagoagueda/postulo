"""What a plugin may write to a company, a department, a contact and their rows (#328).

`postulo.plugins.api` hands out these calls, and they are the only way a sync should change
those records: the DAV plugin used to call `Contact.objects.create` and `contact.save()`, and
the API did the same, so a rule one of them kept was a rule the other could forget. Every call
here is the one that keeps it.

**Who is writing.** Every call takes ``actor``, the account the work is done for. It is the
reader the scoping questions are asked of -- a record is writable when ``actor`` owns it, is in
the group that does, or holds a *read/write* share on it (#896) -- and a record that is not is
refused in the same words whether it exists or not. The owner of what is made is taken from the
record handed in, as `record_listing_event` does: a department and a row are the company's or
the contact's. A contact is the exception, and ``actor``'s: the contacts a person adds at a
company shared with them stay theirs (#955). A plugin never says whose a record is. ``via``
names the plugin or the token in the record's trail (`jobs.trail`), which is where a change to
a company or a contact is written; a department and a row have no history of their own yet.

**The person's own details are read only.** Nothing here accepts a `User` or a `Profile`, and
the rows calls refuse a profile as a holder: a card could otherwise change the sign-in address,
a verified number or the recovery number (#142, #144).

**Employer structure.** While the feature is off for ``actor``, departments and parents are
hidden and kept (`structure`). Reads work either way; making a department, putting a contact in
one and setting a parent are refused, so a sync cannot build what the person is not shown.

**Refusals** are a `WriteRefused` carrying the reason in words -- never a half-written record.
A *row* the rules refuse is different: it comes back in `SavedRows.refused` with its reason and
the rest is saved, because one number in a thousand cards that another account holds must not
stop the other nine hundred and ninety-nine.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from postulo.core import (
    addresses,
    card_rows,
    phone_numbers,
    phones,
    postal,
    slugs,
    throttle,
    web_links,
)
from postulo.core.formsets import give_owner
from postulo.core.models import PhoneNumber, PostalAddress, WebLink

from . import company_names, structure, trail
from .models import Company, CompanyKind, Contact, Department

_UNSET = object()


class WriteRefused(ValueError):
    """A write was refused, and the message says why in words a person can read.

    A `ValueError`, so a plugin that catches the broad one catches this. Raised before
    anything is written.
    """


@dataclass(frozen=True)
class Refused:
    """One row that was not written, as it was handed in, and why."""

    row: object
    reason: str


@dataclass(frozen=True)
class SavedRows:
    """What a rows call did: the holder's rows afterwards, as the person is shown them, the
    rows it refused with their reasons, and how many it left unwritten because a switch is
    off (only the primary is written then)."""

    rows: tuple
    refused: tuple[Refused, ...] = ()
    held_back: int = 0


# ------------------------------------------------------------------------ who may write


def _actor(actor):
    if actor is None or getattr(actor, "pk", None) is None:
        raise WriteRefused(_("Say whose work this is: a saved account."))
    return actor


def _writable(record, actor, model=None) -> None:
    """Refuse a record ``actor`` may not change the details of, whoever it belongs to."""
    model = model or type(record)
    if record is None or getattr(record, "pk", None) is None or not isinstance(record, model):
        raise WriteRefused(_("That is not a record that has been saved."))
    if not model.objects.visible_to(actor, write=True).filter(pk=record.pk).exists():
        raise WriteRefused(_("That record is not yours to change."))


def _structure(actor) -> None:
    if not structure.structure_allowed(actor):
        raise WriteRefused(
            _("Employer structure is switched off, so departments and parents are not changed.")
        )


def _refusal_of(error: ValidationError) -> WriteRefused:
    return WriteRefused("; ".join(error.messages))


# ------------------------------------------------------------------------- the company

_COMPANY_FIELDS = ("name", "website", "careers_url", "location", "notes", "kind", "parent")


@transaction.atomic
def update_company(company: Company, *, actor, via: str = "", **changes) -> Company:
    """Change a company's own details: ``name``, ``website``, ``careers_url``, ``location``,
    ``notes``, ``kind`` and ``parent``. Only the ones named are touched.

    ``notes`` is ``actor``'s own note (#896). A name another of the owner's companies already
    has is refused -- never merged (#239). A ``parent`` is refused while employer structure is
    off, and when it would make a loop; it has to be a company ``actor`` can see.
    """
    actor = _actor(actor)
    unknown = set(changes) - set(_COMPANY_FIELDS)
    if unknown:
        raise TypeError(f"update_company() does not change {sorted(unknown)}")
    _writable(company, actor, Company)
    changed: list[str] = []
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())
        if not name:
            raise WriteRefused(_("A company needs a name."))
        if name != company.name:
            try:
                clash = company_names.clashes(actor, company.principal, name, company=company)
            except throttle.TooOften as too_often:
                # The answer is bounded for somebody who does not own the company (#949).
                raise WriteRefused(_("That name cannot be checked just now.")) from too_often
            if clash:
                raise WriteRefused(_("You already have a company with that name."))
            company.name = name
            changed.append("name")
    for column in ("website", "careers_url", "location"):
        if column in changes:
            value = str(changes[column] or "").strip()
            if value != getattr(company, column):
                setattr(company, column, value)
                changed.append(column)
    if "kind" in changes:
        if changes["kind"] not in CompanyKind.values:
            raise WriteRefused(_("That is not a kind of company."))
        if changes["kind"] != company.kind:
            company.kind = changes["kind"]
            changed.append("kind")
    if "parent" in changes:
        parent = changes["parent"]
        if parent != company.parent:
            _structure(actor)
            if parent is not None and not (
                Company.objects.visible_to(actor).filter(pk=getattr(parent, "pk", None)).exists()
            ):
                raise WriteRefused(_("That company is not one you can see."))
            company.parent = parent
            changed.append("parent")
    pending = [name for name in changed if name != "location"]
    try:
        company.full_clean(
            exclude=[f.name for f in company._meta.fields if f.name not in pending],
            validate_unique=False,
            validate_constraints=False,
        )
        if changed:
            company.save()
    except ValidationError as error:
        company.refresh_from_db()
        raise _refusal_of(error) from error
    except IntegrityError as error:
        raise WriteRefused(_("You already have a company with that name.")) from error
    if "notes" in changes:
        company.write_note(actor, str(changes["notes"] or ""))
    if changed:
        trail.record(company, actor, trail.EDITED, fields=changed, via=via)
    return company


def get_or_create_department(company: Company, name: str, *, actor, via: str = "") -> Department:
    """The department of ``company`` called ``name`` -- spelling and case aside -- or a new
    one. A new one is refused while employer structure is off; one that exists is returned."""
    actor = _actor(actor)
    _writable(company, actor, Company)
    name = " ".join(str(name or "").split())
    if not name:
        raise WriteRefused(_("A department needs a name."))
    found = (
        Department.objects.for_user(actor)
        .filter(company=company, name_key=slugs.name_key(name))
        .first()
    )
    if found is not None:
        return found
    _structure(actor)
    try:
        with transaction.atomic():
            department = Department(company=company, name=name[:120])
            department.save()
    except IntegrityError:
        found = (
            Department.objects.for_user(actor)
            .filter(company=company, name_key=slugs.name_key(name))
            .first()
        )
        if found is None:
            raise
        return found
    return department


# -------------------------------------------------------------------------- the contact

_CONTACT_FIELDS = ("name", "role", "email", "company", "department", "notes")


def _check_placement(contact: Contact, actor, *, department_changed: bool) -> None:
    if contact.department_id is not None and department_changed:
        _structure(actor)
        if not Department.objects.for_user(actor).filter(pk=contact.department_id).exists():
            raise WriteRefused(_("That department is not one you can see."))


def create_contact(company: Company | None, *, actor, via: str = "", **values) -> Contact:
    """Add a contact at ``company`` (or at none): ``name`` (required), ``role``, ``email``,
    ``department`` and ``notes`` -- ``actor``'s own note.

    The company has to be one ``actor`` may change. A department of another company is
    refused (`Contact.clean`), and any department is while employer structure is off.
    """
    actor = _actor(actor)
    unknown = set(values) - set(_CONTACT_FIELDS) - {"company"}
    if unknown:
        raise TypeError(f"create_contact() does not take {sorted(unknown)}")
    if company is not None:
        _writable(company, actor, Company)
    department = values.get("department")
    contact = Contact(
        company=company,
        department=department,
        name=" ".join(str(values.get("name") or "").split()),
        role=str(values.get("role") or "").strip(),
        email=str(values.get("email") or "").strip(),
    )
    contact.set_principal(actor)
    if not contact.name:
        raise WriteRefused(_("A contact needs a name."))
    _check_placement(contact, actor, department_changed=True)
    try:
        contact.full_clean(
            exclude=["owner", "owner_group"], validate_unique=False, validate_constraints=False
        )
    except ValidationError as error:
        raise _refusal_of(error) from error
    with transaction.atomic():
        contact.save()
        contact.write_note(actor, str(values.get("notes") or ""))
        trail.record(contact, actor, trail.CREATED, via=via)
    return contact


@transaction.atomic
def update_contact(contact: Contact, *, actor, via: str = "", **changes) -> Contact:
    """Change a contact: ``name``, ``role``, ``email``, ``company``, ``department`` and
    ``notes`` (``actor``'s own). Only the ones named are touched.

    The contact has to be one ``actor`` may change, and so does a company it is moved to. A
    department of another company is refused, and so is putting it in any department while
    employer structure is off.
    """
    actor = _actor(actor)
    unknown = set(changes) - set(_CONTACT_FIELDS)
    if unknown:
        raise TypeError(f"update_contact() does not change {sorted(unknown)}")
    _writable(contact, actor, Contact)
    changed: list[str] = []
    if "company" in changes and changes["company"] != contact.company:
        if changes["company"] is not None:
            _writable(changes["company"], actor, Company)
        contact.company = changes["company"]
        changed.append("company")
        if "department" not in changes and contact.department_id is not None:
            # A department belongs to a company, so moving the contact leaves it.
            contact.department = None
            changed.append("department")
    for column in ("name", "role", "email"):
        if column in changes:
            value = " ".join(str(changes[column] or "").split())
            if column == "name" and not value:
                raise WriteRefused(_("A contact needs a name."))
            if value != getattr(contact, column):
                setattr(contact, column, value)
                changed.append(column)
    department_changed = False
    if "department" in changes and changes["department"] != contact.department:
        contact.department = changes["department"]
        changed.append("department")
        department_changed = True
    _check_placement(contact, actor, department_changed=department_changed)
    try:
        contact.full_clean(
            exclude=["owner", "owner_group"], validate_unique=False, validate_constraints=False
        )
    except ValidationError as error:
        contact.refresh_from_db()
        raise _refusal_of(error) from error
    if changed:
        contact.save()
        trail.record(contact, actor, trail.EDITED, fields=sorted(set(changed)), via=via)
    if "notes" in changes:
        contact.write_note(actor, str(changes["notes"] or ""))
    return contact


# ---------------------------------------------------------------------------- the rows


def _holder(holder, actor):
    """The company or contact whose rows these are, if ``actor`` may change it."""
    if isinstance(holder, Company | Contact):
        _writable(holder, actor)
        return holder
    raise WriteRefused(_("Only a company's or a contact's details can be written."))


def _row(kind, row):
    """A row as the dataclass: a dict is accepted for a caller that holds values."""
    if isinstance(row, kind):
        return row
    if isinstance(row, dict):
        allowed = {f.name for f in fields(kind)}
        return kind(**{key: value for key, value in row.items() if key in allowed})
    raise TypeError(f"Expected a {kind.__name__}, not {type(row).__name__}")


def _primary_index(rows: list) -> int:
    """The row that asks to be the primary -- the first to -- or the first row."""
    return next((i for i, row in enumerate(rows) if row.is_primary), 0)


def _settle(made: list, asked: list, make_primary) -> None:
    """Exactly one primary among ``made``: the row that asked for it, else the one that
    already was, else the first. ``asked`` is parallel to ``made``."""
    if any(row.is_primary for row in asked):
        make_primary(made[_primary_index(asked)])
        return
    make_primary(next((row for row in made if row.is_primary), made[0]))


@transaction.atomic
def save_phone_numbers(holder, rows, *, actor, via: str = "") -> SavedRows:
    """Make ``holder``'s telephone numbers ``rows`` (`PhoneRow`): a number that stays keeps its
    row, one that is not listed goes, one that is new is added, and exactly one is primary.

    A row that cannot be kept -- a number that cannot exist, one listed twice, a kind that is
    not one -- comes back in ``refused`` and the others are saved. A person's own number is
    asked about the instance and the answer spent (#142); a company's and a contact's collide
    with nothing beyond their holder, so for them nothing is asked.

    With *Several telephone numbers* off for ``actor``, the primary alone is written and the
    numbers the person is not shown are left as they were.
    """
    actor = _actor(actor)
    holder = _holder(holder, actor)
    before = _snapshot(holder, "phone_numbers")
    existing = list(holder.phone_numbers.all())
    stale_pks = set()
    accepted: list[card_rows.PhoneRow] = []
    refused: list[Refused] = []
    seen: set[str] = set()
    for raw in rows:
        row = _row(card_rows.PhoneRow, raw)
        typed = (row.number or "").strip()
        reason = ""
        if not typed:
            reason = _("A number is needed.")
        elif row.kind and row.kind not in PhoneNumber.Kind.values:
            reason = _("%(kind)s is not a kind of number.") % {"kind": row.kind}
        elif row.kind == PhoneNumber.Kind.OTHER and not (row.label or "").strip():
            reason = _("Say what this number is.")
        else:
            verdict = phones.check(typed, "")
            if verdict.impossible:
                reason = verdict.sentence(notes=True)
        normalised = phones.normalise(typed) if typed else ""
        if not reason and (normalised or typed) in seen:
            reason = _("This number is already listed.")
        if not reason:
            reason = phone_numbers.refusal(
                actor,
                typed,
                exclude_pks=stale_pks,
                identifies_owner=phone_numbers.identifies(holder),
            )
        if reason:
            refused.append(Refused(raw, str(reason)))
            continue
        seen.add(normalised or typed)
        accepted.append(
            replace(
                row,
                number=phones.combine(typed, ""),
                label=row.label.strip() if row.kind == PhoneNumber.Kind.OTHER else "",
            )
        )
    if not phone_numbers.several_allowed(actor):
        held_back = max(len(accepted) - 1, 0)
        if accepted:
            wanted = accepted[_primary_index(accepted)]
            kept = phones.normalise(wanted.number)
            hidden = (
                holder.phone_numbers.filter(normalised=kept).first() if kept else None
            ) or holder.phone_numbers.filter(number=wanted.number).first()
            if hidden is not None:
                phone_numbers.set_primary(hidden)
            else:
                phone_numbers.save_only_number(holder, holder.owner, wanted.number)
        else:
            phone_numbers.save_only_number(holder, holder.owner, "")
        _note(holder, actor, via, "phone_numbers", before)
        return SavedRows(card_rows.numbers_for(holder, actor), tuple(refused), held_back=held_back)
    by_key = {(row.normalised or row.number): row for row in existing}
    pairs = [(row, by_key.get(phones.normalise(row.number) or row.number)) for row in accepted]
    kept = {match.pk for _row_, match in pairs if match is not None}
    for old in existing:
        if old.pk not in kept:
            old.delete()
    made: list[PhoneNumber] = []
    for row, match in pairs:
        if match is None:
            match = PhoneNumber(holder=holder)
            give_owner(match, holder)
        match.kind, match.label, match.number = row.kind, row.label, row.number
        match.save()
        made.append(match)
    if made:
        _settle(made, accepted, phone_numbers.set_primary)
    _note(holder, actor, via, "phone_numbers", before)
    return SavedRows(card_rows.numbers_for(holder, actor), tuple(refused))


@transaction.atomic
def save_postal_addresses(holder, rows, *, actor, via: str = "") -> SavedRows:
    """Make ``holder``'s postal addresses ``rows`` (`AddressRow`), as `save_phone_numbers` does
    for numbers: an address that stays keeps its row, exactly one is primary, and a row the
    rules refuse -- empty, a kind that is not one, listed twice, or against its country's
    rules (a postcode in a form it never uses) -- comes back in ``refused``.

    There is no switch for several addresses, so all of them are written.
    """
    actor = _actor(actor)
    holder = _holder(holder, actor)
    before = _snapshot(holder, "postal_addresses")
    existing = list(holder.postal_addresses.all())
    accepted: list[tuple[card_rows.AddressRow, PostalAddress]] = []
    refused: list[Refused] = []
    seen: set[str] = set()
    for raw in rows:
        row = _row(card_rows.AddressRow, raw)
        country = (row.country or "").strip().upper()
        candidate = PostalAddress(
            kind=row.kind,
            label=(row.label or "").strip(),
            street=row.street,
            postcode=postal.canonical_postcode(row.postcode, country, person=actor),
            municipality=row.municipality,
            region=row.region,
            country=country,
            on_map=bool(row.on_map),
        )
        reason = ""
        comparable = candidate.comparable_form()
        if not comparable:
            reason = _("An address needs at least one part.")
        elif row.kind and row.kind not in PostalAddress.Kind.values:
            reason = _("%(kind)s is not a kind of address.") % {"kind": row.kind}
        elif row.kind == PostalAddress.Kind.OTHER and not candidate.label:
            reason = _("Say what this address is.")
        elif comparable in seen:
            reason = _("This address is already listed.")
        else:
            wrong = postal.refusals_for(candidate, person=actor)
            if wrong:
                reason = "; ".join(str(sentence) for _part, sentence in wrong)
        if reason:
            refused.append(Refused(raw, str(reason)))
            continue
        seen.add(comparable)
        accepted.append((row, candidate))
    by_form = {old.comparable_form(): old for old in existing}
    staying = {c.comparable_form() for _r, c in accepted} & set(by_form)
    for old in existing:
        if old.comparable_form() not in staying:
            old.delete()
    made: list[PostalAddress] = []
    for _asked, candidate in accepted:
        match = by_form.get(candidate.comparable_form())
        if match is None or match.pk is None:
            match = candidate
            match.holder = holder
            give_owner(match, holder)
        else:
            match.kind, match.label, match.on_map = (
                candidate.kind,
                candidate.label,
                candidate.on_map,
            )
            match.street, match.postcode = candidate.street, candidate.postcode
            match.municipality, match.region = candidate.municipality, candidate.region
            match.country = candidate.country
        match.save()
        made.append(match)
    if made:
        _settle(made, [row for row, _c in accepted], postal.make_primary)
    _note(holder, actor, via, "postal_addresses", before)
    return SavedRows(card_rows.addresses_for(holder, actor), tuple(refused))


@transaction.atomic
def save_web_links(holder, rows, *, actor, via: str = "") -> SavedRows:
    """Make ``holder``'s web links ``rows`` (`LinkRow`), as `save_phone_numbers` does for
    numbers, with one primary **per kind**.

    A link is checked as a row on the page is: an address that is not a complete http(s) one,
    a service the address is not on, a kind that is not one -- and a company holds the social
    kind alone -- come back in ``refused``. For a kind whose feature is off for ``actor``
    only its primary is written, and the links the person is not shown are left as they were.
    """
    actor = _actor(actor)
    holder = _holder(holder, actor)
    before = _snapshot(holder, "web_links")
    allowed_kinds = web_links.COMPANY_KINDS if isinstance(holder, Company) else web_links.KINDS
    accepted: list[dict] = []
    refused: list[Refused] = []
    seen: set[tuple[str, str]] = set()
    serviced: set[tuple[str, str]] = set()
    for raw in rows:
        row = _row(card_rows.LinkRow, raw)
        reason = ""
        checked = None
        if row.kind not in web_links.KINDS:
            reason = _("“%(kind)s” is not one of the types of link.") % {"kind": row.kind[:40]}
        elif row.kind not in allowed_kinds:
            reason = _("A company's links are its social profiles.")
        else:
            try:
                url = addresses.web_address((row.url or "").strip())
                (checked,) = web_links.checked_rows(
                    [
                        {
                            "kind": row.kind,
                            "url": url,
                            "service": row.service,
                            "label": row.label,
                            "is_primary": row.is_primary,
                        }
                    ]
                )
            except (ValueError, ValidationError) as error:
                reason = "; ".join(error.messages) if hasattr(error, "messages") else str(error)
        if not reason:
            key = (checked["kind"], web_links.same_address(checked["url"]))
            if key in seen:
                reason = _("This address is already listed.")
            elif (
                web_links.one_row_per_service(checked["kind"], checked["service"])
                and (
                    checked["kind"],
                    checked["service"],
                )
                in serviced
            ):
                reason = _("%(service)s is already used by another row.") % {
                    "service": checked["service"]
                }
        if reason:
            refused.append(Refused(raw, str(reason)))
            continue
        seen.add(key)
        if web_links.one_row_per_service(checked["kind"], checked["service"]):
            serviced.add((checked["kind"], checked["service"]))
        accepted.append(checked)
    held_back = 0
    for kind in web_links.KINDS:
        if kind not in allowed_kinds:
            continue
        mine = [row for row in accepted if row["kind"] == kind]
        stored = list(holder.web_links.filter(kind=kind))
        if not web_links.several_allowed(actor, kind):
            held_back += max(len(mine) - 1, 0)
            if mine:
                wanted = mine[next((i for i, r in enumerate(mine) if r["is_primary"]), 0)]
                try:
                    web_links.save_only_link(holder, holder.owner, kind, wanted["url"])
                except ValidationError as error:
                    refused.append(Refused(wanted, "; ".join(error.messages)))
            else:
                web_links.save_only_link(holder, holder.owner, kind, "")
            continue
        by_address = {web_links.same_address(link.url): link for link in stored}
        staying = {web_links.same_address(row["url"]) for row in mine} & set(by_address)
        for link in stored:
            if web_links.same_address(link.url) not in staying:
                link.delete()
        made: list[WebLink] = []
        for row in mine:
            link = by_address.get(web_links.same_address(row["url"]))
            if link is None:
                link = WebLink(holder=holder, kind=kind)
                give_owner(link, holder)
            link.service, link.label, link.url = row["service"], row["label"], row["url"]
            link.save()
            made.append(link)
        if made:
            _settle(
                made,
                [SimpleNamespace(is_primary=row["is_primary"]) for row in mine],
                web_links.set_primary,
            )
    _note(holder, actor, via, "web_links", before)
    return SavedRows(card_rows.links_for(holder, actor), tuple(refused), held_back=held_back)


def _snapshot(holder, block: str) -> list:
    """The values of a holder's rows in one block, to tell whether a save changed any."""
    return sorted(
        (tuple(row) for row in getattr(holder, block).values_list(*_COLUMNS[block])), key=repr
    )


_COLUMNS = {
    "phone_numbers": ("number", "kind", "label", "is_primary"),
    "postal_addresses": (
        "street",
        "postcode",
        "municipality",
        "region",
        "country",
        "kind",
        "label",
        "is_primary",
        "on_map",
    ),
    "web_links": ("url", "kind", "service", "label", "is_primary"),
}


def _note(holder, actor, via: str, block: str, before: list) -> None:
    """A line in the holder's trail naming the block of rows, if the save changed any: a sync
    that finds nothing new writes nothing every quarter of an hour."""
    if _snapshot(holder, block) != before:
        trail.record(holder, actor, trail.EDITED, fields=[trail.BLOCKS[block]], via=via)
