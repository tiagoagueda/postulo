"""*Find details* on a company: the press, what the sources said, and keeping it (#1256).

Three acts at one address. A POST of the ticked sources spends the person's allowance and
sends an errand, because reading a company's site is ten seconds and a ``robots.txt``
besides (#247); the errand asks each source and holds what came back in its own row, which
is the person's. A GET with that errand's number shows it beside what the company has now,
every offer unticked. A POST of ``keep`` stores **only what was ticked**; ``cancel`` keeps
nothing. Either way what was held is taken out of the errand's row.

Modelled on *Look up* in a company register (`register_views`), with one difference: the
answer waits in the errand's row and not in the session, since the work may be done by a
worker, which has none.

The offers are worked out again from the held answer when *Keep* is pressed, never from what
the form sent back, so a hand-written POST can tick only what the page could have offered.
With no source on for the reader there is no button, and this address is a 404.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views import View

from postulo.core import errands, throttle
from postulo.core.mixins import WRITE, shared_scope
from postulo.core.models import Errand, ErrandState
from postulo.core.redirects import safe_next

from . import enrichment, enrichment_offers
from .models import Company

#: The kind of errand a press sends, and what its row holds until the review is answered.
ERRAND = "enrichment"
HELD = enrichment.HELD


#: What a source may need of a company, as the dialog says a company lacks it.
STARTS = {
    "website": gettext_lazy("a website"),
    "name": gettext_lazy("a name"),
    "country": gettext_lazy("an address with a country"),
    "wikidata": gettext_lazy("a Wikidata identifier"),
    "register": gettext_lazy("a register number"),
    "lei": gettext_lazy("an LEI"),
}


def ask_rows(company, person) -> list[dict]:
    """The sources the *Find details* dialog lists for ``company``: every one that is on for
    ``person``, with what a press would send and to whom, and why one cannot be asked.

    Empty where no source is on, or none can start from what the company has: no button is
    drawn then. Reading this asks nothing of anybody.
    """
    rows = []
    for source in enrichment.sources(person).values():
        lacking = enrichment.missing(source, company)
        rows.append(
            {
                "key": source.key,
                "label": str(source.label),
                "sends": str(source.sends),
                "hosts": enrichment.asked_on(source, company),
                "usable": not lacking,
                "needs": [str(STARTS[start]) for start in lacking],
            }
        )
    return rows if any(row["usable"] for row in rows) else []


def forget(errand: Errand) -> None:
    """Take what the sources said out of an errand's row: it is a stranger's text, and the
    row is kept a week (#256). What the errand came to stays."""
    if HELD in (errand.outcome or {}):
        outcome = {name: value for name, value in errand.outcome.items() if name != HELD}
        Errand.objects.filter(pk=errand.pk).update(outcome=outcome)
        errand.outcome = outcome


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CompanyEnrichmentView(LoginRequiredMixin, View):
    """*Find details*, what came back, and keeping what was ticked (#1256).

    Outside the request's transaction (#220): with no worker the press asks the sources
    where it stands, and keeping a logo fetches it. Each write is a transaction of its own
    (`enrichment_offers.keep`).
    """

    template_name = "jobs/company_enrichment.html"

    def _company(self, request: HttpRequest, pk: int) -> Company:
        company = (
            # What it keeps changes the company's details, so a grant to write.
            shared_scope(Company.objects, request.user, WRITE)
            .prefetch_related("identifiers", "names", "postal_addresses", "phone_numbers")
            .filter(pk=pk)
            .first()
        )
        if company is None or not enrichment.sources(request.user):
            raise Http404
        return company

    @staticmethod
    def _errand(request: HttpRequest, pk: int) -> Errand | None:
        """The reader's own errand about this company, or nothing: somebody else's, one of
        another kind and one about another company are all the same nothing."""
        number = request.GET.get("errand") or request.POST.get("errand") or ""
        if not number.isdigit():
            return None
        errand = Errand.objects.for_user(request.user).filter(pk=int(number), kind=ERRAND).first()
        if errand is None or errand.payload.get("company_id") != pk:
            return None
        return errand

    def _held(self, request: HttpRequest, pk: int) -> tuple[Company, Errand | None]:
        """The company and the errand whose answer is being reviewed, the answer forgotten
        first where the company is no longer the reader's to change."""
        errand = self._errand(request, pk)
        try:
            return self._company(request, pk), errand
        except Http404:
            if errand is not None:
                forget(errand)
            raise

    @staticmethod
    def _back(errand: Errand | None, company: Company) -> str:
        return (errand.payload.get("next") if errand else "") or company.get_absolute_url()

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        company, errand = self._held(request, pk)
        if errand is None and request.GET.get("errand"):
            raise Http404
        if errand is None or errand.state != ErrandState.DONE or HELD not in errand.outcome:
            # Nothing waiting: never asked, still being asked, or already answered.
            if errand is not None and not errand.is_finished:
                return redirect("core:errand", pk=errand.pk)
            return redirect(self._back(errand, company))
        held = errand.outcome[HELD]
        results = enrichment.from_held(held, request.user)
        return render(
            request,
            self.template_name,
            {
                "company": company,
                "errand": errand,
                "review": enrichment_offers.review(company, results, request.user),
                "retrieved": parse_datetime(str(held.get("at") or "")),
                "back": self._back(errand, company),
            },
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        if request.POST.get("keep") or request.POST.get("cancel"):
            return self._answer(request, pk)
        return self._ask(request, self._company(request, pk))

    # ---------------------------------------------------------------- the press

    def _ask(self, request: HttpRequest, company: Company) -> HttpResponse:
        back = safe_next(request, company.get_absolute_url())
        on = enrichment.sources(request.user)
        wanted = [
            on[key]
            for key in dict.fromkeys(request.POST.getlist("source"))
            if key in on and not enrichment.missing(on[key], company)
        ]
        if not wanted:
            messages.error(request, _("Choose at least one place to ask."))
            return redirect(back)
        try:
            for source in wanted:
                # Before the errand exists, as every allowance is (#247): a source that reads
                # the company's own site is a fetch of an address somebody recorded, and one
                # with hosts of its own has an allowance of its own.
                (throttle.fetch if source.own_site else throttle.enrichment)(request.user)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return redirect(back)
        errand = errands.send(
            ERRAND,
            request.user,
            subject=company,
            company_id=company.pk,
            sources=[source.key for source in wanted],
            next=back,
        )
        if errand.state == ErrandState.DONE and errand.url:
            # No worker: it was asked where the request stood, and the answer is waiting.
            return redirect(errand.url)
        return redirect("core:errand", pk=errand.pk)

    # ------------------------------------------------------- keeping, or not

    def _answer(self, request: HttpRequest, pk: int) -> HttpResponse:
        company, errand = self._held(request, pk)
        if errand is None:
            raise Http404
        back = self._back(errand, company)
        held = (errand.outcome or {}).get(HELD)
        # Out of the row whichever was pressed: nothing is kept that the person did not
        # tick, and what the sources said is not kept at all.
        forget(errand)
        if held is None or request.POST.get("cancel"):
            return redirect(back)
        results = enrichment.from_held(held, request.user)
        found = enrichment_offers.review(company, results, request.user)
        chosen = enrichment_offers.ticked(found, request.POST)
        if not chosen:
            messages.info(request, _("Nothing was kept."))
            return redirect(back)
        kept, refused = enrichment_offers.keep(company, request.user, chosen)
        for reason in refused:
            messages.error(request, reason)
        if kept:
            messages.success(
                request,
                ngettext("Kept %(count)s detail.", "Kept %(count)s details.", len(kept))
                % {"count": len(kept)},
            )
        return redirect(back)
