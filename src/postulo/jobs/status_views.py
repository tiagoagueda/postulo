"""Adding, changing and removing the hiring signals of a company (#941).

A signal is shared with its company: anyone who can see the company sees it, and anyone with
a write share may add, change or take one off (a write share covers a company's own details,
#896). The page that draws them is the company's own; these only act and send the person
back, as `CompanyNamesView` does.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View

from postulo.core.mixins import WRITE, shared_scope
from postulo.core.redirects import safe_next

from . import status, trail
from .forms import HiringSignalForm
from .models import Company, CompanyHiringSignal


def _back(request, company) -> str:
    return safe_next(request, reverse("jobs:company_update", args=[company.pk]))


def _first_error(form) -> str:
    for errors in form.errors.values():
        return errors[0]
    return str(_("Choose what was reported and the date."))


class SignalAddView(LoginRequiredMixin, View):
    def post(self, request, pk: int):
        company = get_object_or_404(shared_scope(Company.objects, request.user, WRITE), pk=pk)
        form = HiringSignalForm(request.POST)
        if not form.is_valid():
            messages.error(request, _first_error(form))
            return redirect(_back(request, company))
        with transaction.atomic():
            signal = form.save(commit=False)
            signal.company, signal.author = company, request.user
            signal.save()
            trail.record(company, request.user, trail.EDITED, fields=["hiring_signals"])
            status.touch(company)
        messages.success(request, _("Hiring signal added."))
        return redirect(_back(request, company))


class SignalView(LoginRequiredMixin, View):
    """Change one entry, or take it off. The row is looked up among the signals of the
    companies the caller may change (anything else is a 404, as a company's own page is)."""

    def row(self, request, pk: int):
        return get_object_or_404(
            CompanyHiringSignal.objects.filter(
                company__in=shared_scope(Company.objects, request.user, WRITE).values("pk")
            ).select_related("company"),
            pk=pk,
        )


class SignalEditView(SignalView):
    def post(self, request, pk: int):
        signal = self.row(request, pk)
        form = HiringSignalForm(request.POST, instance=signal)
        if not form.is_valid():
            messages.error(request, _first_error(form))
            return redirect(_back(request, signal.company))
        with transaction.atomic():
            form.save()
            trail.record(signal.company, request.user, trail.EDITED, fields=["hiring_signals"])
            status.touch(signal.company)
        messages.success(request, _("Hiring signal changed."))
        return redirect(_back(request, signal.company))


class SignalRemoveView(SignalView):
    def post(self, request, pk: int):
        signal = self.row(request, pk)
        company = signal.company
        with transaction.atomic():
            signal.delete()
            trail.record(company, request.user, trail.EDITED, fields=["hiring_signals"])
            status.touch(company)
        messages.success(request, _("Hiring signal removed."))
        return redirect(_back(request, company))
