"""Taking one identifier off a company at once, after asking (#857).

*Your details* has done this for its rows since #303: the bin opens a dialog, and confirming it
removes the row there and then, without saving everything else on the page. The company's
form kept the "Remove" tick box and the Save that applied it. This gives the company's
identifiers the same contract, with the same dialog and the same script
(`dialog[data-remove-row]`), so its terms are the profile's: each row has its own address,
which answers 404 for anybody else's row (a foreign id and a made-up one look alike, #759), and
what the page draws -- the button's name, the dialog's id, the sentences -- comes from here so
the button in the row and the dialog after the form cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from django.urls import reverse
from django.utils.translation import gettext as _

from postulo.accounts.removals import quoted

from .models import CompanyIdentifier

#: What the shared dialog markup asks of a "kind": identifiers have no primary.
KIND = SimpleNamespace(name="company-identifier", has_primary=False)


def words(row: CompanyIdentifier) -> str:
    """The row as somebody would recognise it: its kind and its value."""
    return f"{row.scheme_label} {row.value}"


def said(value: str) -> str:
    return _("%(what)s removed.") % {"what": quoted(value)}


@dataclass(frozen=True)
class Removal:
    """One saved identifier as the company's form offers to take it off."""

    pk: int
    company_pk: int
    value: str
    hands_on: bool = False
    kind = KIND

    @property
    def already_gone(self) -> str:
        return _("%(what)s had already been removed.") % {"what": quoted(self.value)}

    @property
    def dialog(self) -> str:
        return f"remove-company-identifier-{self.pk}"

    @property
    def action(self) -> str:
        return reverse("jobs:company_identifier_remove", args=[self.pk])


def offer(formset) -> list[Removal]:
    """Mark each saved row with what taking it off would be, read from the table.

    After a refused save a form's instance carries what was typed, and the dialog is about the
    row as it is kept; a row already taken off has no instance any more and is offered nothing.
    """
    saved = [form for form in formset.initial_forms if form.instance.pk]
    kept = CompanyIdentifier.objects.in_bulk([form.instance.pk for form in saved])
    found: list[Removal] = []
    for form in saved:
        row = kept.get(form.instance.pk)
        if row is None:
            continue
        removal = Removal(pk=row.pk, company_pk=row.company_id, value=words(row))
        form.removal = removal
        found.append(removal)
    return found
