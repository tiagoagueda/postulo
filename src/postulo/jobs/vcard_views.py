"""Contacts as vCard files: taking them out, and reading a file's cards in (#660).

Every download is a card file made of the signed-in person's own rows (`for_user()`), and a
record that is somebody else's is a 404. The file that is uploaded is read, held in the
session as what was read of it, shown card by card with nothing ticked, and only the cards
that are then ticked are made.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import TemplateView

from postulo.core import vcard

from . import vcards
from .models import Company, Contact


def _download(text: str, filename: str) -> HttpResponse:
    response = HttpResponse(text, content_type=vcard.CONTENT_TYPE)
    response["Content-Disposition"] = content_disposition_header(True, filename)
    # Somebody's address book: kept by nothing between here and the person.
    response["Cache-Control"] = "private, max-age=0, no-store"
    return response


def _with_notes(request: HttpRequest) -> bool:
    return request.GET.get("notes") == "1"


class ContactVCardView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        contact = get_object_or_404(
            vcards.contacts_of(Contact.objects.for_user(request.user)), pk=pk
        )
        card = vcards.card_for_contact(contact, notes=_with_notes(request))
        return _download(vcard.card_to_text(card), f"postulo-contact-{contact.pk}.vcf")


class CompanyContactsVCardView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        company = get_object_or_404(Company.objects.for_user(request.user), pk=pk)
        contacts = vcards.contacts_of(
            Contact.objects.for_user(request.user).filter(company=company)
        )
        notes = _with_notes(request)
        text = vcard.cards_to_text(vcards.card_for_contact(c, notes=notes) for c in contacts)
        return _download(text, f"postulo-company-{company.pk}-contacts.vcf")


class CompanyVCardView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        company = get_object_or_404(Company.objects.for_user(request.user), pk=pk)
        card = vcards.card_for_company(company, notes=_with_notes(request))
        return _download(vcard.card_to_text(card), f"postulo-company-{company.pk}.vcf")


class AllContactsVCardView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        contacts = vcards.contacts_of(Contact.objects.for_user(request.user))
        notes = _with_notes(request)
        text = vcard.cards_to_text(vcards.card_for_contact(c, notes=notes) for c in contacts)
        return _download(text, "postulo-contacts.vcf")


class OwnCardView(LoginRequiredMixin, View):
    """The signed-in person's own card, and nobody's else: there is no id to change."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return _download(vcard.card_to_text(vcards.card_for_person(request.user)), "postulo-me.vcf")


class ContactVCardsView(LoginRequiredMixin, TemplateView):
    """Offer the file, read one, show what each card would become, and make the chosen.

    Two steps on one address, as the candidate file and the spreadsheet import are: a file
    is read and what was read of it is held, the page says what each card would become and
    what of it is not kept with **none ticked**, and nothing reaches the records until
    somebody has ticked a card and pressed the button.
    """

    template_name = "jobs/contact_vcards.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        held = self.request.session.get(vcards.SESSION_KEY)
        cards = vcards.held_cards(held)
        if held and not cards:
            self.request.session.pop(vcards.SESSION_KEY, None)
            held = None
        context["entries"] = vcards.review(self.request.user, cards) if cards else None
        context["skipped"] = held.get("skipped", 0) if cards else 0
        context["limit"] = vcard.MAX_CARDS
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        action = request.POST.get("action")
        if action == "forget":
            request.session.pop(vcards.SESSION_KEY, None)
            return redirect("jobs:contact_vcards")
        if action == "confirm":
            return self._add(request)
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect("jobs:contact_vcards")
        if upload.size > vcard.MAX_BYTES:
            messages.error(
                request,
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": vcard.MAX_BYTES // (1024 * 1024)},
            )
            return redirect("jobs:contact_vcards")
        try:
            read = vcard.read(upload.read(vcard.MAX_BYTES + 1))
        except vcard.VCardRefused as refused:
            messages.error(request, str(refused))
            return redirect("jobs:contact_vcards")
        if not read.cards:
            messages.warning(
                request, _("That file was read, and there was no card in it to import.")
            )
            return redirect("jobs:contact_vcards")
        request.session[vcards.SESSION_KEY] = vcards.hold(read)
        return redirect("jobs:contact_vcards")

    def _add(self, request: HttpRequest) -> HttpResponse:
        cards = vcards.held_cards(request.session.get(vcards.SESSION_KEY))
        if not cards:
            messages.error(request, _("There is nothing waiting to be imported."))
            return redirect("jobs:contact_vcards")
        chosen = set()
        for value in request.POST.getlist("chosen"):
            if value.isdigit() and len(value) < 6:
                chosen.add(int(value))
        if not chosen:
            messages.info(request, _("No card was ticked, so nothing was added."))
            return redirect("jobs:contact_vcards")
        report = vcards.apply(request.user, cards, chosen)
        request.session.pop(vcards.SESSION_KEY, None)
        total = report.contacts + report.companies
        if total:
            messages.success(
                request,
                ngettext(
                    "%(total)s card was added. Nothing you already had was changed.",
                    "%(total)s cards were added. Nothing you already had was changed.",
                    total,
                )
                % {"total": total},
            )
        else:
            messages.info(request, _("Nothing was added."))
        for problem in report.problems[:20]:
            messages.warning(request, problem)
        if len(report.problems) > 20:
            more = len(report.problems) - 20
            messages.warning(
                request,
                ngettext(
                    "%(more)s more value was left out.",
                    "%(more)s more values were left out.",
                    more,
                )
                % {"more": more},
            )
        return redirect("jobs:company_list")
