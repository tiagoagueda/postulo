"""Operations on companies that more than one door uses: the API, the importers, plugins."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from . import identifiers
from .models import Company, CompanyIdentifier


@transaction.atomic
def set_identifiers(
    company: Company, items: list[tuple[str, str, str]], *, replace: bool = False
) -> list[CompanyIdentifier]:
    """Give ``company`` these identifiers — (scheme, value, label) — validated as a set.

    With ``replace`` the company ends up with exactly these; otherwise they are added to
    what it has, and a value it already carries is left alone. A value already on another
    of the owner's companies is refused with its name, because that is the one thing an
    identifier must never do: point at two records.
    """
    cleaned: list[tuple[str, str, str]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for scheme, raw, label in items:
        try:
            value = identifiers.clean(scheme, raw)
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue
        label = (label or "").strip() if scheme == identifiers.OTHER else ""
        if scheme == identifiers.OTHER and not label:
            errors.append(_("An 'other' identifier needs a name."))
            continue
        # Case-blind, as the constraint and the form are (#211).
        if (scheme, value.casefold()) in seen:
            continue
        seen.add((scheme, value.casefold()))
        cleaned.append((scheme, value, label))

    schemes = [scheme for scheme, _v, _l in cleaned if scheme != identifiers.OTHER]
    if len(schemes) != len(set(schemes)):
        errors.append(_("A company carries one identifier per scheme."))

    for scheme, value, _label in cleaned:
        if scheme == identifiers.OTHER:
            continue
        clash = (
            CompanyIdentifier.objects.for_user(company.owner)
            .filter(scheme=scheme, value__iexact=value)
            .exclude(company=company)
            .select_related("company")
            .first()
        )
        if clash is not None:
            errors.append(
                _("%(company)s already carries %(scheme)s %(value)s.")
                % {"company": clash.company.name, "scheme": clash.scheme_label, "value": value}
            )
        if not replace:
            held = company.identifiers.filter(scheme=scheme).exclude(value__iexact=value).first()
            if held is not None:
                errors.append(
                    _("%(company)s already has a %(scheme)s identifier: %(value)s.")
                    % {"company": company.name, "scheme": held.scheme_label, "value": held.value}
                )
    if errors:
        raise ValidationError(errors)

    if replace:
        keep = {(scheme, value.casefold()) for scheme, value, _label in cleaned}
        for existing in company.identifiers.all():
            if (existing.scheme, existing.value.casefold()) not in keep:
                existing.delete()
    result = []
    for scheme, value, label in cleaned:
        # Asked for the way the constraint underneath asks (#211): a company carrying `q95`
        # and told to carry `Q95` already carries it, and matching on the exact characters
        # would have gone on to create a second row and meet the database instead of a
        # person. The row keeps the spelling it has — the migration folds the old ones, and
        # `other` is not ours to rewrite.
        held = company.identifiers.filter(scheme=scheme, value__iexact=value)
        if scheme == identifiers.OTHER:
            held = held.filter(label__iexact=label)
        identifier = held.first()
        if identifier is None:
            identifier = CompanyIdentifier.objects.create(
                owner=company.owner, company=company, scheme=scheme, value=value, label=label
            )
        if identifier.label != label:
            identifier.label = label
            identifier.save(update_fields=["label", "updated_at"])
        result.append(identifier)
    return result
