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
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.core import throttle
from postulo.core.mixins import OwnedObjectMixin
from postulo.plugins.base import CaptureError
from postulo.plugins.fetching import fetch_page
from postulo.plugins.policy import plugins_for
from postulo.plugins.registry import parse_page

from .known import known
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

    def _render(self, request: HttpRequest, form: CaptureURLForm, known_=None) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {"form": form, "sources": plugins_for(request.user, "source"), "known": known_},
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        return self._render(request, CaptureURLForm())

    def post(self, request: HttpRequest) -> HttpResponse:
        form = CaptureURLForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        # Told before anything is fetched, and asked once: an address already in the
        # person's listings, or already waiting for review, is shown with a link, and the
        # same form submitted again -- `anyway` set -- is their answer (#178). A duplicate
        # is theirs to want; it is not this form's to make in silence.
        if request.POST.get("anyway") != "1":
            seen = known(request.user, form.cleaned_data["url"])
            if seen.listings or seen.captures:
                return self._render(request, form, known_=seen)

        # Before anything is fetched or parsed. Capture is the one surface that makes this
        # server talk to somebody else's, and nothing bounded how fast an account could ask
        # it to (#112). Counted even when the page came with the request rather than being
        # fetched: no outbound call then, but the parse is still work somebody asked for.
        try:
            throttle.capture(request.user)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
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


def next_pending(user, *, after: Capture | None = None) -> Capture | None:
    """The capture to look at next: the one after this in the queue, else the first left.

    The queue is the list's order, newest first, so "next" is the next older one; once
    the end is reached it wraps to whatever is still waiting, so a capture skipped once
    comes round again rather than being lost (#179).
    """
    pending = Capture.objects.for_user(user).filter(status=CaptureStatus.PENDING)
    if after is not None:
        pending = pending.exclude(pk=after.pk)
        following = pending.filter(created_at__lt=after.created_at).first()
        if following is not None:
            return following
    return pending.first()


def after_deciding(request: HttpRequest, decided: Capture) -> HttpResponse:
    """Where *and next* goes once a capture is decided: the next one, or the list."""
    following = next_pending(request.user, after=decided)
    if following is None:
        messages.info(request, _("That was the last capture waiting."))
        return redirect("listings:list")
    return redirect(following.get_absolute_url())


#: Said beside a value the page did not state, so a default never reads as a reading. The
#: sources stopped reporting a currency with no amount behind it (#176); the form still
#: needs something in a select, and this is what makes that visibly a default (#179).
NOT_ON_THE_PAGE = _("Not on the page — a default. Kept only beside an amount.")


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

    def _known(self, capture: Capture):
        """What the person already holds for this address, said before they save it (#178).

        A capture can be reviewed long after it arrived, so the review screen says it as
        well as the form and the extension did.
        """
        data = capture.posting_data
        return known(
            self.request.user,
            data.url or capture.url,
            data.title,
            data.company_name,
            except_capture=capture.pk,
        )

    def _form(self, request: HttpRequest, capture: Capture, data=None):
        """The form, with the values the page never stated marked as defaults."""
        form = review_form_class()(data, initial=self._initial(capture), user=request.user)
        read = capture.posting_data
        for name, stated in (
            ("salary_currency", read.salary_currency),
            ("salary_period", read.salary_period),
        ):
            if not stated:
                form.fields[name].help_text = NOT_ON_THE_PAGE
        return form

    def _context(self, request: HttpRequest, capture: Capture, form) -> dict:
        following = next_pending(request.user, after=capture)
        return {
            "capture": capture,
            "form": form,
            "known": self._known(capture),
            "next_url": following.get_absolute_url() if following else "",
            "queue_left": (
                Capture.objects.for_user(request.user)
                .filter(status=CaptureStatus.PENDING)
                .exclude(pk=capture.pk)
                .count()
            ),
        }

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form(request, capture)
        return render(request, self.template_name, self._context(request, capture, form))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.applications.models import Priority, Status
        from postulo.applications.services import (
            apply_to_listing,
            create_listing,
            get_or_create_company,
        )

        capture = get_object_or_404(self.get_queryset(), pk=pk)
        form = self._form(request, capture, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._context(request, capture, form))
        # Pressed *and next*: the decision is recorded exactly as below, and the page moves
        # on to the next capture rather than to what this one became (#179).
        onwards = bool(request.POST.get("next"))

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
            if onwards:
                return after_deciding(request, capture)
            return redirect(application.get_absolute_url())

        capture.save(update_fields=["status", "posting", "updated_at"])
        messages.success(request, _("Saved to your listings. Decide about it when you are ready."))
        if onwards:
            return after_deciding(request, capture)
        return redirect(listing.get_absolute_url())


class CaptureDiscardView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = get_object_or_404(self.get_queryset(), pk=pk)
        capture.status = CaptureStatus.DISCARDED
        capture.save(update_fields=["status", "updated_at"])
        messages.success(request, _("Capture discarded."))
        if request.POST.get("next"):
            return after_deciding(request, capture)
        return redirect("listings:list")


class CaptureDiscardSelectedView(OwnedObjectMixin, View):
    """Throw away several at once, from the list, with no form in between.

    Most of triage is the second of three answers, given in under a second, and a page
    load per answer is what made reviewing forty captures forty page loads (#179). Only
    what is still pending and this person's own is touched; a stray id is ignored rather
    than refused, because the honest outcome of "discard these" is how many went.
    """

    def get_queryset(self):
        return Capture.objects.for_user(self.request.user)

    def post(self, request: HttpRequest) -> HttpResponse:
        chosen = [value for value in request.POST.getlist("selected") if value.isdigit()]
        count = (
            self.get_queryset()
            .filter(pk__in=chosen, status=CaptureStatus.PENDING)
            .update(status=CaptureStatus.DISCARDED, updated_at=timezone.now())
        )
        if count:
            messages.success(request, _("Captures discarded: %(count)s.") % {"count": count})
        else:
            messages.info(request, _("Nothing was selected."))
        return redirect("listings:list")


capture_list_url = reverse_lazy("jobs:capture_list")
