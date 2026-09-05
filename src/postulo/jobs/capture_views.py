"""Capturing a posting from a URL, and reviewing what came back.

The flow is deliberately two steps. Postulo reads the page, then shows what it found in
the ordinary application form for somebody to correct and accept. Nothing reaches the
database as an application until a person has looked at it, because a parser reading
markup it has never seen is wrong often enough that the alternative would put invented
job titles into the records people rely on.
"""

from __future__ import annotations

import functools

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.core.mixins import OwnedObjectMixin
from postulo.plugins.base import CaptureError
from postulo.plugins.fetching import fetch_page
from postulo.plugins.registry import available_sources, parse_page

from .models import Capture, CaptureStatus


class CaptureURLForm(forms.Form):
    """An address to fetch, or a page you already have.

    The second field exists because plenty of large employers sit behind bot protection
    that refuses anything not driving a browser. Their advert is perfectly visible to
    you and completely unreachable from your server, and the answer to that is to hand
    Postulo the page rather than to dress the request up as a browser. The same field
    covers postings behind a login, for the same reason.
    """

    url = forms.URLField(
        label=_("Posting address"),
        max_length=500,
        widget=forms.URLInput(attrs={"placeholder": "https://…", "autofocus": "autofocus"}),
        help_text=_("Postulo fetches this one page, and nothing else."),
    )
    html = forms.CharField(
        label=_("Page source"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "<!doctype html>…"}),
        help_text=_(
            "Optional. If the site refuses Postulo, open the posting in your browser, "
            "view the page source, and paste it here. Nothing is fetched when you do."
        ),
    )


class CaptureCreateView(OwnedObjectMixin, View):
    """Fetch a page, read it, and send the result to review."""

    template_name = "jobs/capture_form.html"

    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def _render(self, request: HttpRequest, form: CaptureURLForm) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {"form": form, "sources": available_sources()},
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        return self._render(request, CaptureURLForm())

    def post(self, request: HttpRequest) -> HttpResponse:
        form = CaptureURLForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        url = form.cleaned_data["url"]
        supplied = form.cleaned_data.get("html", "").strip()

        if supplied:
            # Nothing is fetched: the page came from a browser that was already allowed
            # to see it, which is also how the future extension will work.
            page_url, page_html = url, supplied
        else:
            try:
                fetched = fetch_page(url)
            except CaptureError as exc:
                # Refusals here are explanations, not failures: the message says what to
                # do instead, and the form keeps what was typed so it can be acted on.
                messages.error(request, str(exc))
                return self._render(request, form)
            page_url, page_html = fetched.url, fetched.html

        result = parse_page(page_url, page_html)
        if result is None:
            messages.error(
                request,
                _("Nothing resembling a job posting was found on that page."),
            )
            return self._render(request, form)

        data, source = result
        capture = Capture.objects.create(
            owner=request.user,
            url=page_url[:500],
            source_name=source.name,
            source_version=getattr(source, "version", ""),
            origin="web",
            data=data.model_dump(mode="json"),
        )
        return redirect("jobs:capture_review", pk=capture.pk)


class CaptureListView(OwnedObjectMixin, ListView):
    model = Capture
    template_name = "jobs/capture_list.html"
    context_object_name = "captures"
    paginate_by = 50

    def get_queryset(self):
        queryset = super().get_queryset().select_related("application")
        if self.request.GET.get("show") != "all":
            queryset = queryset.filter(status=CaptureStatus.PENDING)
        return queryset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["showing_all"] = self.request.GET.get("show") == "all"
        return context


@functools.cache
def review_form_class():
    """The posting half of intake, plus one question: has the person already applied?

    Built lazily rather than at import: applications already depends on jobs, and
    importing it back at import time would close the loop.
    """
    from postulo.applications.forms import PostingIntakeForm

    class CaptureReviewForm(PostingIntakeForm):
        already_applied = forms.BooleanField(
            label=_("I have already applied to this one"),
            required=False,
            help_text=_(
                "Ticked, the listing becomes an application straight away, marked as "
                "applied today. Otherwise it waits in your listings for you to decide."
            ),
        )

    return CaptureReviewForm


class CaptureReviewView(OwnedObjectMixin, View):
    """Show what was read, in the form that will make it a listing.

    The correction step is what makes captures safe: a parser reading somebody else's
    markup gets things wrong. Saving lands the result in the person's listings, not in
    their applications — unless they say they have already applied, which is the common
    case of recording after the fact, and then it becomes both.
    """

    template_name = "jobs/capture_review.html"

    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def _initial(self, capture: Capture) -> dict:
        data = capture.posting_data
        return {
            "company_name": data.company_name,
            "title": data.title,
            "url": data.url or capture.url,
            "location": data.location,
            "remote_type": data.remote_type,
            "employment_type": data.employment_type,
            "source": data.source,
            "salary_min": data.salary_min,
            "salary_max": data.salary_max,
            "salary_currency": data.salary_currency or "EUR",
            "salary_period": data.salary_period or "year",
            "closes_at": data.closes_at,
            "description": data.description,
        }

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = review_form_class()(initial=self._initial(capture), user=request.user)
        return render(request, self.template_name, {"capture": capture, "form": form})

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.applications.models import Priority, Status
        from postulo.applications.services import (
            apply_to_listing,
            create_listing,
            get_or_create_company,
        )

        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = review_form_class()(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"capture": capture, "form": form})

        company = get_or_create_company(request.user, form.cleaned_data["company_name"])
        listing = create_listing(request.user, company=company, posting_data=form.posting_data)
        capture.status = CaptureStatus.ACCEPTED
        capture.posting = listing

        if form.cleaned_data.get("already_applied"):
            application = apply_to_listing(
                listing,
                {
                    "status": Status.APPLIED,
                    "channel": "",
                    "priority": Priority.NORMAL,
                    "deadline": None,
                },
            )
            capture.application = application
            capture.save(update_fields=["status", "posting", "application", "updated_at"])
            messages.success(request, _("Application recorded from the capture."))
            return redirect(application.get_absolute_url())

        capture.save(update_fields=["status", "posting", "updated_at"])
        messages.success(request, _("Saved to your listings. Decide about it when you are ready."))
        return redirect(listing.get_absolute_url())


class CaptureDiscardView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        capture.status = CaptureStatus.DISCARDED
        capture.save(update_fields=["status", "updated_at"])
        messages.success(request, _("Capture discarded."))
        return redirect("listings:list")


capture_list_url = reverse_lazy("jobs:capture_list")
