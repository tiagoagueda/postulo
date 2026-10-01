"""Small helpers the routers share."""

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from ninja.errors import HttpError

from postulo.core.models import Tag


def owned(request, queryset):
    """The caller's rows and nothing else: the API's version of ``for_user``."""
    return queryset.for_user(request.auth.owner)


def owned_or_404(request, queryset, pk: int):
    return get_object_or_404(owned(request, queryset), pk=pk)


def choice_or_422(value: str, choices, *, field: str, allow_blank: bool = False) -> str:
    if allow_blank and value == "":
        return value
    if value not in choices.values:
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s; got %(value)s.")
            % {"field": repr(field), "choices": sorted(choices.values), "value": repr(value)},
        )
    return value


def identifiers_or_422(company, items, *, replace: bool = False) -> None:
    """Attach identifiers to a company, or explain in one 422 what was wrong with them."""
    from django.core.exceptions import ValidationError

    from postulo.jobs.services import set_identifiers

    try:
        set_identifiers(company, [(i.scheme, i.value, i.label) for i in items], replace=replace)
    except ValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc


def referrer_and_agency_or_422(request, payload) -> dict:
    """Who referred the caller and through which agency, as rows of the caller's own (#239).

    The payload names them by id, and an id is only a number until somebody has checked
    whose it is: the contact and the company are looked up among the caller's own, exactly
    as the form offers only the owner's. **One answer for somebody else's record and for
    one that does not exist**, so the refusal confirms nothing about what another account
    holds.
    """
    from postulo.jobs.models import Company, Contact

    found = {}
    if payload.referred_by_id is not None:
        contact = owned(request, Contact.objects).filter(pk=payload.referred_by_id).first()
        if contact is None:
            raise HttpError(
                422, _("%(field)s is not one of your contacts.") % {"field": "'referred_by_id'"}
            )
        found["referred_by"] = contact
    if payload.through_agency_id is not None:
        agency = owned(request, Company.objects).filter(pk=payload.through_agency_id).first()
        if agency is None:
            raise HttpError(
                422,
                _("%(field)s is not one of your companies.") % {"field": "'through_agency_id'"},
            )
        found["through_agency"] = agency
    return found


def priority_or_422(value: int) -> int:
    from postulo.applications.models import Priority

    if value not in Priority.values:
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s; got %(value)s.")
            % {"field": "'priority'", "choices": sorted(Priority.values), "value": repr(value)},
        )
    return value


def tags_named(owner, names: list[str]) -> list[Tag]:
    """The owner's tags with these names, made if missing."""
    tags = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        tag = Tag.objects.for_user(owner).filter(name__iexact=name).first()
        if tag is None:
            tag = Tag.objects.create(owner=owner, name=name)
        tags.append(tag)
    return tags
