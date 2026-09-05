"""Small helpers the routers share."""

from django.shortcuts import get_object_or_404
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
        raise HttpError(422, f"{field!r} must be one of {sorted(choices.values)}; got {value!r}.")
    return value


def identifiers_or_422(company, items, *, replace: bool = False) -> None:
    """Attach identifiers to a company, or explain in one 422 what was wrong with them."""
    from django.core.exceptions import ValidationError

    from postulo.jobs.services import set_identifiers

    try:
        set_identifiers(company, [(i.scheme, i.value, i.label) for i in items], replace=replace)
    except ValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc


def priority_or_422(value: int) -> int:
    from postulo.applications.models import Priority

    if value not in Priority.values:
        raise HttpError(422, f"'priority' must be one of {sorted(Priority.values)}; got {value!r}.")
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
