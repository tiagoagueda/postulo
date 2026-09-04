"""Capturing a posting from a URL, and reviewing what came back.

The flow is deliberately two steps. Postulo reads the page, then shows what it found in
the ordinary application form for somebody to correct and accept. Nothing reaches the
database as an application until a person has looked at it, because a parser reading
markup it has never seen is wrong often enough that the alternative would put invented
job titles into the records people rely on.
"""

from __future__ import annotations

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
    url = forms.URLField(
        label=_("Posting address"),
        max_length=500,
        widget=forms.URLInput(attrs={"placeholder": "https://…", "autofocus": "autofocus"}),
        help_text=_("Postulo fetches this one page, and nothing else."),
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
        try:
            fetched = fetch_page(url)
        except CaptureError as exc:
            # Refusals here are explanations, not failures: the message says what to do
            # instead, which is nearly always "paste the text in by hand".
            messages.error(request, str(exc))
            return self._render(request, form)

        result = parse_page(fetched.url, fetched.html)
        if result is None:
            messages.error(
                request,
                _("Nothing resembling a job posting was found on that page."),
            )
            return self._render(request, form)

        data, source = result
        capture = Capture.objects.create(
            owner=request.user,
            url=fetched.url[:500],
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


class CaptureReviewView(OwnedObjectMixin, View):
    """Show what was read, in the form that will create the application."""

    template_name = "jobs/capture_review.html"

    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def _form_class(self):
        # Imported here rather than at module scope: applications already depends on
        # jobs, and importing it back at import time would close the loop.
        from postulo.applications.forms import ApplicationIntakeForm

        return ApplicationIntakeForm

    def _initial(self, capture: Capture) -> dict:
        from postulo.applications.models import Status

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
            "status": Status.DRAFT,
        }

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form_class()(initial=self._initial(capture), user=request.user)
        return render(request, self.template_name, {"capture": capture, "form": form})

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.applications.services import create_application, get_or_create_company

        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form_class()(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"capture": capture, "form": form})

        company = get_or_create_company(request.user, form.cleaned_data["company_name"])
        application = create_application(
            request.user,
            company=company,
            posting_data=form.posting_data,
            application_data=form.application_data,
        )
        application.tags.set(form.cleaned_data["tags"])

        capture.status = CaptureStatus.ACCEPTED
        capture.application = application
        capture.save(update_fields=["status", "application", "updated_at"])

        messages.success(request, _("Application recorded from the capture."))
        return redirect(application.get_absolute_url())


class CaptureDiscardView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        capture.status = CaptureStatus.DISCARDED
        capture.save(update_fields=["status", "updated_at"])
        messages.success(request, _("Capture discarded."))
        return redirect("jobs:capture_list")


capture_list_url = reverse_lazy("jobs:capture_list")
