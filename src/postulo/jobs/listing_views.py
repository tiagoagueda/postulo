"""Listings: every posting a person has noticed, until they decide about it.

The stage before Applications. A listing arrives by capture or by hand, sits as *new*
until it is shortlisted, discarded or applied to, and the page here is where that
decision is made. Applying hands over to the applications app through one service call,
so the record is the same as if it had been typed in one go.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.core.mixins import OwnedObjectMixin
from postulo.core.redirects import safe_next

from .models import LISTING_FILTERS, Capture, CaptureStatus, DiscardReason, JobPosting, ListingState

FILTER_LABELS = {
    "undecided": _("To decide"),
    ListingState.SHORTLISTED: _("Shortlisted"),
    ListingState.DISCARDED: _("Discarded"),
    "applied": _("Applied"),
    "closed": _("Closed"),
    "all": _("Everything"),
}


class ListingListView(OwnedObjectMixin, ListView):
    model = JobPosting
    template_name = "jobs/listing_list.html"
    context_object_name = "listings"
    paginate_by = 50

    def current_filter(self) -> str:
        wanted = self.request.GET.get("state", "undecided")
        if wanted in LISTING_FILTERS or wanted == "all":
            return wanted
        return "undecided"

    def get_queryset(self):
        # Annotating with a count groups the query, and a grouped query drops the model's
        # default ordering, so every branch orders explicitly: pagination needs it.
        queryset = super().get_queryset().select_related("company").with_application_count()
        current = self.current_filter()
        if current == "undecided":
            return queryset.undecided().order_by("state", "closes_at", "-noted_at", "-pk")
        if current == "all":
            return queryset.order_by("-noted_at", "-pk")
        return queryset.in_state(current).order_by("-noted_at", "-pk")

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        everything = JobPosting.objects.for_user(self.request.user)
        current = self.current_filter()
        context["current_filter"] = current
        context["filters"] = [
            {
                "value": value,
                "label": FILTER_LABELS[value],
                "active": value == current,
                "count": count,
            }
            for value, count in (
                ("undecided", everything.undecided().count()),
                (ListingState.SHORTLISTED, everything.in_state(ListingState.SHORTLISTED).count()),
                (ListingState.DISCARDED, everything.in_state(ListingState.DISCARDED).count()),
                ("applied", everything.in_state("applied").count()),
                ("closed", everything.in_state("closed").count()),
                ("all", everything.count()),
            )
        ]
        context["pending_captures"] = Capture.objects.for_user(self.request.user).filter(
            status=CaptureStatus.PENDING
        )[:20]
        context["discard_reasons"] = DiscardReason.choices
        return context


class ListingCreateView(OwnedObjectMixin, View):
    """Add a listing by hand: a company and a title are enough; the rest can come later."""

    template_name = "jobs/listing_form.html"

    def get_queryset(self):
        return JobPosting.objects.for_user(self.request.user)

    def _form_class(self):
        from postulo.applications.forms import PostingIntakeForm

        return PostingIntakeForm

    def get(self, request: HttpRequest) -> HttpResponse:
        form = self._form_class()(user=request.user, initial={"url": request.GET.get("url", "")})
        return render(request, self.template_name, {"form": form})

    def post(self, request: HttpRequest) -> HttpResponse:
        from postulo.applications.services import create_listing, get_or_create_company

        form = self._form_class()(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})
        company = get_or_create_company(request.user, form.cleaned_data["company_name"])
        listing = create_listing(request.user, company=company, posting_data=form.posting_data)
        messages.success(request, _("Added to your listings."))
        return redirect(listing.get_absolute_url())


def _back_to(request: HttpRequest, fallback: str) -> HttpResponse:
    return HttpResponseRedirect(safe_next(request, fallback))


class ListingStateView(OwnedObjectMixin, View):
    """Shortlist, discard or restore a listing. One POST, one decision, back where you were."""

    action = ""

    def get_queryset(self):
        return JobPosting.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        listing = get_object_or_404(self.get_queryset(), pk=pk)
        if self.action == "shortlist":
            listing.shortlist()
            messages.success(request, _("Shortlisted."))
        elif self.action == "discard":
            reason = request.POST.get("reason", "")
            if reason not in DiscardReason.values:
                reason = DiscardReason.OTHER
            listing.discard(reason)
            messages.success(request, _("Discarded. It stays in your history and can be restored."))
        elif self.action == "restore":
            listing.restore()
            messages.success(request, _("Back in your listings."))
        return _back_to(request, reverse("listings:list"))


class ListingApplyView(OwnedObjectMixin, View):
    """The decision that matters: apply. Creates the application and leaves the listing."""

    template_name = "jobs/listing_apply.html"

    def get_queryset(self):
        return JobPosting.objects.for_user(self.request.user).select_related("company")

    def _form_class(self):
        from postulo.applications.forms import ApplicationDetailsForm

        return ApplicationDetailsForm

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        listing = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form_class()(user=request.user)
        return render(request, self.template_name, {"listing": listing, "form": form})

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.applications.services import apply_to_listing

        listing = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form_class()(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"listing": listing, "form": form})
        application = apply_to_listing(listing, form.application_data)
        application.tags.set(form.cleaned_data["tags"])
        messages.success(request, _("Application recorded."))
        return redirect(application.get_absolute_url())
