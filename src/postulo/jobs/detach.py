"""Keeping what is yours when a record you used stops being yours to see (#896).

A person loses a company, a contact or a listing when they leave the group that owns it, a
share to them is taken back, a group's record is given to one member, or its owner deletes
it or their account. Their own data may point at it: their application at a listing, their
note on a company, a contact of theirs at it, an entry of their career there. None of that
is lost and none of it is left pointing at a record they cannot open:

* each record their data points at is **copied to them**, as their own, with what hangs off
  it (its names, identifiers, departments, numbers, addresses, links, handles, logo and
  industries -- these in their own vocabulary), and what *it* points at that they lose too
  (a listing's company), so the copy is whole;
* their data is **repointed to the copy**, and the copy's trail says *Copied from … on …*;
* nothing else of the original goes with it, and the original is untouched.

A copy whose name they already use for a company of their own is called "… (copy)", and the
two are a conflict to resolve on the conflicts page.

`DEPENDENTS` is every relation by which a person's own row points at one of the three, and
a test holds it to the models: a new foreign key to a company, a contact or a listing has to
be added here, or the test says so.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from postulo.core import slugs

#: (app label, model, field) of every row of a person's own that points at a company, a
#: contact, a listing or a department. Each such row has an ``owner``.
DEPENDENTS = {
    "company": (
        ("jobs", "Contact", "company"),
        ("jobs", "JobPosting", "company"),
        ("jobs", "Company", "parent"),
        ("jobs", "Company", "acquired_by"),
        ("jobs", "CompanyNote", "company"),
        ("applications", "Application", "through_agency"),
        ("applications", "Appointment", "company"),
        ("resume", "Experience", "company"),
        ("resume", "Education", "company"),
        ("resume", "Certification", "company"),
        ("resume", "Honour", "company"),
        ("resume", "Membership", "company"),
        ("resume", "Course", "company"),
    ),
    "contact": (
        ("jobs", "ListingEvent", "contact"),
        ("jobs", "ContactNote", "contact"),
        ("applications", "Application", "contact"),
        ("applications", "Application", "referred_by"),
        ("applications", "Appointment", "contact"),
        ("resume", "Reference", "contact"),
        ("documents", "ReferenceLetter", "referee"),
    ),
    "posting": (
        ("jobs", "Capture", "posting"),
        ("jobs", "ListingEvent", "posting"),
        ("jobs", "ListingStatus", "posting"),
        ("applications", "Application", "posting"),
    ),
}
#: The many-to-many relations that do the same: an interview's people.
DEPENDENT_LINKS = {"contact": (("applications", "Interview", "contacts"),)}
#: Relations to the three that are not a person's own data pointing at them: the record's
#: own parts, its grants, its trail, and the ones the copy re-makes.
NOT_DEPENDENTS = {
    ("jobs", "CompanyName", "company"),
    ("jobs", "CompanyHiringSignal", "company"),
    ("jobs", "CompanyIdentifier", "company"),
    ("jobs", "CompanyLogo", "company"),
    ("jobs", "Department", "company"),
    ("jobs", "Share", "company"),
    ("jobs", "Share", "contact"),
    ("jobs", "Share", "posting"),
    ("jobs", "Contact", "department"),
    ("applications", "Application", "department"),
}


def _kind(record) -> str:
    return {"company": "company", "contact": "contact", "jobposting": "posting"}[
        record._meta.model_name
    ]


def _model(app_label, name):
    return apps.get_model(app_label, name)


def points_at(record, person) -> bool:
    """Whether any of ``person``'s own rows points at ``record``."""
    kind = _kind(record)
    for app_label, name, field in DEPENDENTS[kind]:
        model = _model(app_label, name)
        rows = model.objects.filter(**{field: record})
        rows = rows.filter(owner=person)
        if model._meta.model_name in ("company", "contact", "jobposting"):
            rows = rows.exclude(pk=record.pk) if model is type(record) else rows
        if rows.exists():
            return True
    for app_label, name, field in DEPENDENT_LINKS.get(kind, ()):
        model = _model(app_label, name)
        if model.objects.filter(**{field: record}, owner=person).exists():
            return True
    return False


def _sees(record, person) -> bool:
    return type(record).objects.visible_to(person).filter(pk=record.pk).exists()


@transaction.atomic
def after_losing(records, people, *, check: bool = True) -> dict:
    """Copy to each of ``people`` the records of ``records`` their own data points at.

    ``check`` asks first whether they still see each one some other way -- through another
    group, another grant -- and leaves it alone if they do. A caller that knows they will
    not (the record is about to be deleted, or given away) passes ``check=False``.

    Returns ``{(person pk, record kind, record pk): copy}``.
    """
    if not isinstance(records, (list, tuple, set)):
        records = [records]
    made = {}
    for person in people:
        if person is None:
            continue
        lost = [r for r in records if not (check and _sees(r, person))]
        if lost:
            made.update(_detach(person, lost))
    return made


MODELS = {"company": "Company", "contact": "Contact", "posting": "JobPosting"}


def _detach(person, lost) -> dict:
    copies: dict[tuple[str, int], object] = {}
    going = {(_kind(record), record.pk) for record in lost}

    def needs(target) -> bool:
        """A record a copy points at gets its own copy when it is going too, or when the
        person cannot see it: a copy never points at what its owner cannot open."""
        if target is None:
            return False
        return (_kind(target), target.pk) in going or not _sees(target, person)

    def copy_of(record):
        key = (_kind(record), record.pk)
        if key not in copies:
            copies[key] = _copy(record, person, copy_of, needs)
        return copies[key]

    for record in lost:
        if points_at(record, person):
            copy_of(record)
    for (kind, pk), copy in list(copies.items()):
        original = _model("jobs", MODELS[kind]).objects.get(pk=pk)
        _repoint(original, copy, person)
    return {(person.pk, kind, pk): copy for (kind, pk), copy in copies.items()}


def _copy(record, person, copy_of, needs):
    from . import trail
    from .models import Company, Contact, JobPosting

    model = type(record)
    clone = model.objects.get(pk=record.pk)
    original_pk = clone.pk
    clone.pk = clone.id = None
    clone._state.adding = True
    clone.owner_id, clone.owner_group_id = person.pk, None
    if isinstance(clone, Company):
        if clone.parent_id and needs(clone.parent):
            clone.parent = copy_of(Company.objects.get(pk=clone.parent_id))
        # Its acquirer (#941), as its parent: a copy never names what its owner cannot open.
        if clone.acquired_by_id and needs(clone.acquired_by):
            clone.acquired_by = copy_of(Company.objects.get(pk=clone.acquired_by_id))
        clone.name = _free_name(person, clone.name)
        clone.has_logo = False
    elif isinstance(clone, Contact):
        if clone.company_id and needs(clone.company):
            clone.company = copy_of(Company.objects.get(pk=clone.company_id))
        clone.department = None
    elif isinstance(clone, JobPosting):
        if needs(clone.company):
            clone.company = copy_of(Company.objects.get(pk=clone.company_id))
    for name in getattr(clone, "OWN_VALUES", ()):
        clone.__dict__.pop(name, None)
    clone._owners_values = {}
    clone.save()
    original = model.objects.get(pk=original_pk)
    _copy_parts(original, clone, person)
    trail.record(
        clone,
        person,
        trail.COPIED,
        detail={
            "from": _principal_label(original),
            "on": timezone.localdate().isoformat(),
            "original": original_pk,
        },
    )
    return clone


def _principal_label(record) -> str:
    from postulo.core.sharing import principal_label

    return principal_label(record)


def _free_name(person, name: str) -> str:
    """``name``, or ``name (copy)``, or ``name (copy 2)``: one ``person`` does not use yet."""
    from .models import Company

    taken = set(Company.objects.filter(owner=person).values_list("name_key", flat=True))
    if slugs.name_key(name) not in taken:
        return name
    for number in range(1, 100):
        candidate = f"{name} (copy)" if number == 1 else f"{name} (copy {number})"
        if slugs.name_key(candidate) not in taken:
            return candidate[:200]
    return f"{name} ({timezone.now():%Y%m%d%H%M%S})"[:200]


def _copy_parts(original, clone, person) -> None:
    """What hangs off a record goes with its copy: owned as the copy is, which is ``person``."""
    from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink

    from .models import (
        Company,
        CompanyHiringSignal,
        CompanyIdentifier,
        CompanyLogo,
        CompanyName,
        Department,
        Industry,
    )

    held_from = ContentType.objects.get_for_model(type(original))
    for row_model in (PhoneNumber, PostalAddress, WebLink, MessagingHandle):
        for row in row_model.objects.filter(content_type=held_from, object_id=original.pk):
            row.pk = row.id = None
            row._state.adding = True
            row.object_id = clone.pk
            row.owner_id, row.owner_group_id = person.pk, None
            row._state.fields_cache.pop("holder", None)
            try:
                with transaction.atomic():
                    row.save()
            except IntegrityError:
                # A number the person already holds elsewhere; the original keeps it.
                continue
    if not isinstance(original, Company):
        return
    for part_model in (CompanyName, CompanyHiringSignal, CompanyIdentifier, Department):
        for part in part_model.objects.filter(company=original):
            part.pk = part.id = None
            part._state.adding = True
            part.company = clone
            try:
                with transaction.atomic():
                    part.save()
            except IntegrityError:
                # An identifier one of the person's own companies carries already.
                continue
    logo = CompanyLogo.objects.filter(company=original).first()
    if logo is not None:
        logo.pk = None
        logo._state.adding = True
        logo.company = clone
        logo.save()
        Company.objects.filter(pk=clone.pk).update(has_logo=True)
    names = [industry.name for industry in original.industries.all()]
    if names:
        clone.industries.add(*Industry.named(person, names))


def _repoint(original, copy, person) -> None:
    """``person``'s own rows that pointed at ``original`` point at ``copy`` now."""
    from .models import Department

    kind = _kind(original)
    for app_label, name, field in DEPENDENTS[kind]:
        model = _model(app_label, name)
        rows = model.objects.filter(**{field: original}, owner=person)
        if model is type(original):
            rows = rows.exclude(pk=copy.pk)
        rows.update(**{field: copy})
    for app_label, name, field in DEPENDENT_LINKS.get(kind, ()):
        model = _model(app_label, name)
        for row in model.objects.filter(**{field: original}, owner=person):
            getattr(row, field).remove(original)
            getattr(row, field).add(copy)
    if kind == "company":
        # Their own contacts and applications at a team of the original: the same team at
        # the copy, matched by name.
        teams = {team.name_key: team for team in Department.objects.filter(company=copy)}
        for model_label, field in (
            ("jobs.Contact", "department"),
            ("applications.Application", "department"),
        ):
            model = apps.get_model(model_label)
            for row in model.objects.filter(owner=person, **{f"{field}__company": original}):
                setattr(row, field, teams.get(getattr(row, field).name_key))
                row.save(update_fields=[field])
