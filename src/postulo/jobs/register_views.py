"""*Look up* beside a company's register number or LEI, what the register said, and keeping it
(#1021, #935).

Modelled on *Fetch from Wikidata* (#673): a POST asks, charged to the person's allowance, and
the answer is held in their session, not stored; the GET shows it beside what the company has
now; a POST of ``keep`` stores **only what was ticked**. Nothing is ticked to begin with, and
nothing is overwritten that the page did not name: the official name replaces the company's
name only where the person ticks it, an address is added beside the ones it has, a state is
written with the register as its source, an industry is added, and a register number is
offered from an LEI record only where the company has none.

The offers are worked out again from the held answer when *Keep* is pressed, never from what
the form sent back, so a hand-written POST can tick only what the page could have offered.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views import View

from postulo.core.mixins import WRITE, shared_scope
from postulo.core.redirects import safe_next

from . import identifiers, industries, register_lookup, trail
from .models import Company, Industry, LegalState

#: What the review page can offer, in the order it draws them.
TAKES = ("name", "address", "state", "industry", "register")


def offered(person) -> bool:
    """Whether *Look up* is offered to ``person``: the feature's answer (#1021)."""
    from postulo.plugins.policy import decide

    return bool(decide(register_lookup.FEATURE, person).on)


def lookups_for(company, person) -> list[dict]:
    """The *Look up* buttons a company's page draws: one per identifier a register answers
    for, and none while the feature is off for ``person``."""
    if not offered(person):
        return []
    found = []
    for row in company.identifiers.all():
        if row.scheme not in register_lookup.SCHEMES:
            continue
        adapter = register_lookup.adapter_for(row.scheme, row.value)
        if adapter is not None:
            found.append({"scheme": row.scheme, "value": row.value, "label": str(adapter.label)})
    return found


def _same(one: str, other: str) -> bool:
    return " ".join((one or "").split()).casefold() == " ".join((other or "").split()).casefold()


def _address_row(company, address):
    """An unsaved row for what the register gave, so it is compared as a stored one is."""
    from postulo.core.models import PostalAddress

    return PostalAddress(
        content_type=ContentType.objects.get_for_model(Company),
        object_id=company.pk,
        kind=PostalAddress.Kind.OTHER,
        label=str(_("Registered office"))[:60],
        street=address.street,
        postcode=address.postcode,
        municipality=address.municipality,
        region=address.region,
        country=address.country,
    )


def offers(company, record, scheme: str) -> dict:
    """What the review page offers to keep from ``record``, and what it only says.

    Each offer is ``None`` where there is nothing to keep: the register gave none, or the
    company already has it.
    """
    found: dict = dict.fromkeys(TAKES)
    found["notes"] = []
    if record.name and not _same(record.name, company.name):
        found["name"] = {"value": record.name}
    if record.address is not None:
        row = _address_row(company, record.address)
        listed = {address.comparable for address in company.postal_addresses.all()}
        if row.comparable_form() in listed:
            found["notes"].append(_("Its address is already listed."))
        else:
            found["address"] = {"value": row.one_line(), "row": row}
    if record.state and record.state != company.legal_state:
        found["state"] = {
            "value": record.state,
            "label": LegalState(record.state).label,
            "since": record.state_since,
        }
    if record.division:
        name = industries.name_for(record.division)
        if company.industries.filter(code=record.division).exists():
            found["notes"].append(_("Its industry is already listed."))
        elif name:
            found["industry"] = {"division": record.division, "value": name}
    if scheme == register_lookup.LEI and record.registered_as:
        stored = next(
            (row.value for row in company.identifiers.all() if row.scheme == identifiers.REGISTER),
            "",
        )
        if stored:
            found["comparison"] = {
                "stored": stored,
                "found": record.registered_as,
                "result": register_lookup.compare(stored, record),
            }
        else:
            value = register_lookup.register_value_from(record)
            if value:
                found["register"] = {"value": value}
            else:
                found["notes"].append(
                    _(
                        "The LEI record gives the register number %(number)s, which is not "
                        "one a register number here can be."
                    )
                    % {"number": record.registered_as}
                )
    return found


class CompanyRegisterView(LoginRequiredMixin, View):
    """*Look up*, what came back, and keeping what was ticked (#1021)."""

    template_name = "jobs/company_register.html"

    def _company(self, request, pk: int) -> Company:
        if not offered(request.user):
            raise Http404
        return get_object_or_404(
            # What it keeps changes the company's details, so a grant to write.
            shared_scope(Company.objects, request.user, WRITE).prefetch_related(
                "identifiers", "industries", "postal_addresses"
            ),
            pk=pk,
        )

    @staticmethod
    def _held_key(company) -> str:
        return f"register-{company.pk}"

    @staticmethod
    def _back_key(company) -> str:
        return f"register-{company.pk}-next"

    def _back(self, request, company) -> str:
        return safe_next(request, reverse("jobs:company_detail", args=[company.pk]))

    def _held(self, request, company):
        """The held answer and the adapter it came from, or ``(None, None)``."""
        held = request.session.get(self._held_key(company))
        if not isinstance(held, dict):
            return None, None
        adapter = register_lookup.find(held.get("adapter", ""))
        if adapter is None:
            return None, None
        return held, adapter

    def get(self, request, pk: int):
        company = self._company(request, pk)
        held, adapter = self._held(request, company)
        back = request.session.get(self._back_key(company)) or reverse(
            "jobs:company_detail", args=[company.pk]
        )
        if held is None:
            return redirect(back)
        record = register_lookup.from_held(held.get("record"))
        number = register_lookup.number_of(adapter.scheme, held.get("value", ""))
        return render(
            request,
            self.template_name,
            {
                "company": company,
                "adapter": adapter,
                "held": held,
                "record": record,
                "offers": offers(company, record, adapter.scheme),
                "page": adapter.page(number) if adapter.page else "",
                "retrieved": parse_datetime(held.get("at") or ""),
                "back": back,
            },
        )

    def post(self, request, pk: int):
        from postulo.core import throttle

        company = self._company(request, pk)
        if request.POST.get("keep"):
            return self._keep(request, company)
        scheme = request.POST.get("scheme", "")
        row = next(
            (row for row in company.identifiers.all() if row.scheme == scheme),
            None,
        )
        adapter = register_lookup.adapter_for(scheme, row.value) if row is not None else None
        if adapter is None:
            messages.error(request, _("No register this instance asks answers for that number."))
            return redirect(self._back(request, company))
        try:
            throttle.register_lookup(request.user)
            record = register_lookup.lookup(adapter, row.value)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return redirect(self._back(request, company))
        except register_lookup.LookupRefused as refused:
            messages.error(request, str(refused))
            return redirect(self._back(request, company))
        request.session[self._back_key(company)] = self._back(request, company)
        request.session[self._held_key(company)] = {
            "adapter": adapter.key,
            "scheme": scheme,
            "value": row.value,
            "record": register_lookup.held(record),
            "at": timezone.now().isoformat(),
        }
        return redirect("jobs:company_register", pk=company.pk)

    def _keep(self, request, company):
        held, adapter = self._held(request, company)
        request.session.pop(self._held_key(company), None)
        back = request.session.pop(self._back_key(company), None) or self._back(request, company)
        if held is None:
            return redirect(back)
        record = register_lookup.from_held(held.get("record"))
        found = offers(company, record, adapter.scheme)
        ticked = [take for take in TAKES if take in request.POST.getlist("take") and found[take]]
        if not ticked:
            messages.info(request, _("Nothing was kept."))
            return redirect(back)
        number = register_lookup.number_of(adapter.scheme, held.get("value", ""))
        source = (adapter.page(number) if adapter.page else "")[:500]
        via = str(adapter.label)
        kept, refused = self._apply(request.user, company, found, ticked, source, via)
        for reason in refused:
            messages.error(request, reason)
        if kept:
            messages.success(
                request, _("Kept what %(register)s said.") % {"register": adapter.label}
            )
        return redirect(back)

    @staticmethod
    def _apply(user, company, found, ticked, source, via) -> tuple[list[str], list[str]]:
        """Write each ticked offer; what could not be written is said, and the rest stays."""
        from .surface import WriteRefused, update_company

        kept: list[str] = []
        refused: list[str] = []
        if "name" in ticked:
            try:
                update_company(company, actor=user, via=via, name=found["name"]["value"])
                kept.append("name")
            except WriteRefused as refusal:
                refused.append(str(refusal))
        fields: list[str] = []
        with transaction.atomic():
            if "address" in ticked:
                row = found["address"]["row"]
                row.is_primary = not company.postal_addresses.exists()
                try:
                    with transaction.atomic():
                        row.save()
                    fields.append(trail.BLOCKS["postal_addresses"])
                except IntegrityError:
                    refused.append(_("That address could not be added."))
            if "state" in ticked:
                state = found["state"]
                company.legal_state = state["value"]
                company.legal_state_since = state["since"]
                company.legal_state_source = source
                company.acquired_by = None
                company.save(
                    update_fields=[
                        "legal_state",
                        "legal_state_since",
                        "legal_state_source",
                        "acquired_by",
                        "updated_at",
                    ]
                )
                fields += ["legal_state", "legal_state_since"]
            if "industry" in ticked:
                company.industries.add(*Industry.named(user, [found["industry"]["value"]]))
                fields.append("industries")
            if "register" in ticked:
                try:
                    with transaction.atomic():
                        # Through the company, which the caller has already scoped to a
                        # grant to write; the row is owned as the company is.
                        company.identifiers.create(
                            scheme=identifiers.REGISTER,
                            value=found["register"]["value"],
                        )
                    fields.append(trail.BLOCKS["identifiers"])
                except IntegrityError:
                    refused.append(
                        _("Another of your companies already has the register number %(number)s.")
                        % {"number": found["register"]["value"]}
                    )
            if fields:
                trail.record(company, user, trail.EDITED, fields=fields, via=via)
        return kept + fields, refused
