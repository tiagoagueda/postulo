"""Listings: every posting a person has noticed, until they decide about it.

The stage before Applications. A listing arrives by capture or by hand, sits as *new*
until it is shortlisted, discarded or applied to, and the page here is where that
decision is made. Applying hands over to the applications app through one service call,
so the record is the same as if it had been typed in one go.
"""

from __future__ import annotations

from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.core import tables
from postulo.core.cells import EditableCellView
from postulo.core.mixins import OwnedObjectMixin
from postulo.core.redirects import safe_next

from .forms import JobPostingForm
from .models import LISTING_FILTERS, Capture, CaptureStatus, DiscardReason, JobPosting, ListingState
from .tables import ListingsTable

FILTER_LABELS = {
    "undecided": _("To decide"),
    ListingState.SHORTLISTED: _("Shortlisted"),
    ListingState.DISCARDED: _("Discarded"),
    "applied": _("Applied"),
    "closed": _("Closed"),
    "all": _("Everything"),
}


class ListingListView(OwnedObjectMixin, ListView):
    """The triage table: many rows, looked at once, mostly discarded (#160).

    The page drew its own rows until now, so it had none of what `core/tables.py` gives a
    list -- and it is the page in Postulo that wanted them most. Two kinds of narrowing sit
    on it, and they are deliberately different controls:

    **The tabs are the workflow.** To decide, shortlisted, discarded, applied, closed. Three
    of those are not a column at all -- *applied* is read from the applications and *closed*
    from two dates -- and each carries a count, which is the reason to look at it. They stay
    a strip of links above the table.

    **The header row is the question.** Role, company, location, when it closes: narrowing
    within whatever the tabs left. The two compose, and the tab travels in the query string
    beside the filters, so one link carries both.
    """

    model = JobPosting
    template_name = "jobs/listing_list.html"
    context_object_name = "listings"

    @cached_property
    def table(self) -> ListingsTable:
        return ListingsTable(
            self.request, tables.settings_for(self.request.user, ListingsTable.name)
        )

    def get_paginate_by(self, queryset) -> int:
        return self.table.page_size

    def current_filter(self) -> str:
        wanted = self.request.GET.get("state", "undecided")
        if wanted in LISTING_FILTERS or wanted == "all":
            return wanted
        return "undecided"

    def get_queryset(self):
        # The tab first, because `undecided` and `in_state` do their own counting and a
        # second annotation under the same name is an error rather than a no-op; the
        # table's ordering and filters second. That ordering always ends in the key, so
        # pagination over the aggregated rows never repeats a row or skips one.
        queryset = super().get_queryset()
        current = self.current_filter()
        if current == "undecided":
            queryset = queryset.undecided()
        elif current == "all":
            queryset = queryset.with_application_count()
        else:
            queryset = queryset.in_state(current)
        queryset = queryset.select_related("company").with_table_data()
        return self.table.apply(queryset)

    def get_template_names(self) -> list[str]:
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return [f"{self.template_name}#htmx"]
        return [self.template_name]

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        everything = JobPosting.objects.for_user(self.request.user)
        current = self.current_filter()
        # One query for all six numbers (#231). It was six, each grouping the whole table by
        # posting to count applications it then compared with zero.
        counts = everything.tab_counts()
        context["current_filter"] = current
        context["filters"] = [
            {
                "value": value,
                "label": FILTER_LABELS[value],
                "active": value == current,
                "count": count,
            }
            for value, count in counts.items()
        ]
        # Whether this person has any listings *at all*, which is what decides whether the
        # page wears its table chrome. A Columns control, a filter row and a bulk bar over
        # no rows is worse than the sentence that used to be here; an empty *Discarded* tab
        # belonging to somebody with forty listings is not that, and keeps them (#160).
        context["has_listings"] = counts["all"] > 0
        context["pending_captures"] = Capture.objects.for_user(self.request.user).filter(
            status=CaptureStatus.PENDING
        )[:20]
        context["discard_reasons"] = DiscardReason.choices
        context["table"] = self.table
        context["page_sizes"] = tables.PAGE_SIZES
        return context


class ListingCellView(LoginRequiredMixin, EditableCellView):
    """One cell of the listings table, changed where it sits (#135, #160)."""

    model = JobPosting
    form_class = JobPostingForm
    columns = ListingsTable.columns
    form_url_name = "jobs:posting_update"


class ListingBulkView(LoginRequiredMixin, View):
    """Decide about several listings at once: the gesture this page exists for (#160).

    *Look at forty, keep three* is the shape of the work, and doing it a row at a time was
    forty round trips. Three actions, every one of them a move between states that any of
    the others undoes -- there is no bulk delete here, any more than anywhere else (#134).

    **Discard asks for its reason once, in the bar, and gives it to every ticked row.** The
    alternative was leaving discard out of the bulk actions, which would have left out the
    thing people do most on this page. Asked once is a fair description of the gesture:
    somebody sweeping a page is turning those rows down *for the same reason*, and a row
    whose reason was really another can be discarded again from its own row, which replaces
    it.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from postulo.core import bulk

        rows = bulk.chosen_rows(request, JobPosting)
        if not rows.exists():
            messages.info(request, bulk.nothing_chosen())
            return redirect(self._back(request))

        action = request.POST.get(bulk.ACTION, "")
        if action not in ("shortlist", "discard", "restore"):
            messages.error(request, _("That is not something Postulo can do to several at once."))
            return redirect(self._back(request))

        reason = request.POST.get("reason", "")
        if reason not in DiscardReason.values:
            reason = DiscardReason.OTHER

        changed = 0
        for listing in rows:
            if action == "shortlist":
                listing.shortlist()
            elif action == "discard":
                listing.discard(reason)
            else:
                listing.restore()
            changed += 1
        messages.success(request, bulk.changed(changed, ListingsTable.noun))
        return redirect(self._back(request))

    @staticmethod
    def _back(request: HttpRequest) -> str:
        return safe_next(request, reverse("listings:list"))


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
