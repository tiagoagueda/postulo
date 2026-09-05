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
        if (scheme, value) in seen:
            continue
        seen.add((scheme, value))
        cleaned.append((scheme, value, label))

    schemes = [scheme for scheme, _v, _l in cleaned if scheme != identifiers.OTHER]
    if len(schemes) != len(set(schemes)):
        errors.append(_("A company carries one identifier per scheme."))

    for scheme, value, _label in cleaned:
        if scheme == identifiers.OTHER:
            continue
        clash = (
            CompanyIdentifier.objects.for_user(company.owner)
            .filter(scheme=scheme, value=value)
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
            held = company.identifiers.filter(scheme=scheme).exclude(value=value).first()
            if held is not None:
                errors.append(
                    _("%(company)s already has a %(scheme)s identifier: %(value)s.")
                    % {"company": company.name, "scheme": held.scheme_label, "value": held.value}
                )
    if errors:
        raise ValidationError(errors)

    if replace:
        keep = {(scheme, value) for scheme, value, _label in cleaned}
        for existing in company.identifiers.all():
            if (existing.scheme, existing.value) not in keep:
                existing.delete()
    result = []
    for scheme, value, label in cleaned:
        identifier, _created = CompanyIdentifier.objects.get_or_create(
            owner=company.owner,
            company=company,
            scheme=scheme,
            value=value,
            defaults={"label": label},
        )
        if identifier.label != label:
            identifier.label = label
            identifier.save(update_fields=["label", "updated_at"])
        result.append(identifier)
    return result
