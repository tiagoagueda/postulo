"""Calendar → Subscriptions: the calendars somebody follows, and their events (#1098).

A subscription is the person's own: every view here narrows to it (`OwnedObjectMixin`, or
`for_user` on the events), so another account's is a 404. Its events are read-only: the one
thing done to one here is making an appointment or an interview of the person's own from it,
which is then theirs and is not touched by the next reading.
"""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from postulo.core import throttle
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin

from . import subscriptions
from .forms import SubscribedEventForm, SubscriptionForm
from .ical_reader import stored_uid
from .models import Appointment, CalendarSubscription, Interview, SubscribedEvent
from .services import (
    DEFAULT_INTERVIEW_LENGTH,
    InterviewRefused,
    add_appointment,
    schedule_interview,
)

#: How often one account may ask for a calendar to be read now: each is a request from the
#: server to somebody else's, as a Gravatar lookup is (#796).
REFRESH_RATE = throttle.Rate(12, 3600)


class SubscriptionListView(OwnedObjectMixin, ListView):
    model = CalendarSubscription
    template_name = "applications/subscription_list.html"
    context_object_name = "subscriptions"

    def get_queryset(self):
        return super().get_queryset().annotate(event_count=Count("events"))

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["cards"] = [
            {
                "subscription": subscription,
                "next_attempt": subscriptions.next_attempt(subscription),
                "left_out": subscriptions.left_out_lines(subscription),
            }
            for subscription in context["subscriptions"]
        ]
        return context


class SubscriptionCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = CalendarSubscription
    form_class = SubscriptionForm
    template_name = "applications/subscription_form.html"
    success_url = reverse_lazy("applications:subscription_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            _(
                "Subscribed. Its events appear on the calendar once it has been read, "
                "within minutes."
            ),
        )
        return response


class SubscriptionUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CalendarSubscription
    form_class = SubscriptionForm
    template_name = "applications/subscription_form.html"
    success_url = reverse_lazy("applications:subscription_list")


class SubscriptionDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    """Deleting a subscription takes its events off the calendar with it; what was made from
    one of them is the person's own and stays."""

    model = CalendarSubscription
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("applications:subscription_list")


class SubscriptionRefreshView(OwnedObjectMixin, View):
    """*Read it now*: one subscription, at once, within an allowance an hour."""

    model = CalendarSubscription

    def get_queryset(self):
        return CalendarSubscription.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        subscription = get_object_or_404(self.get_queryset(), pk=pk)
        try:
            throttle.consume("calendar-subscription", request.user, REFRESH_RATE)
        except throttle.TooOften:
            messages.warning(
                request, _("That is a lot of tries. Wait a little before asking again.")
            )
            return redirect("applications:subscription_list")
        if subscriptions.refresh(subscription):
            messages.success(request, _("Read again. The calendar shows what it holds now."))
        else:
            subscription.refresh_from_db(fields=["last_error"])
            messages.error(request, subscription.last_error)
        return redirect("applications:subscription_list")


def _already_made(user, event: SubscribedEvent):
    """The interview or appointment of this person already made from ``event``, if one was."""
    if not event.uid:
        return None
    uid = stored_uid(event.uid)
    return (
        Interview.objects.for_user(user).filter(uid=uid).first()
        or Appointment.objects.for_user(user).filter(uid=uid).first()
    )


class SubscribedEventView(LoginRequiredMixin, View):
    """One subscribed event, read-only, and the form that makes it the person's own."""

    template_name = "applications/subscribed_event.html"

    def get_object(self, request: HttpRequest, pk: int) -> SubscribedEvent:
        return get_object_or_404(
            SubscribedEvent.objects.for_user(request.user).select_related("subscription"), pk=pk
        )

    def render(self, request, event, form) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {
                "event": event,
                "form": form,
                "made": _already_made(request.user, event),
                "back_url": safe_next(request, reverse("applications:calendar")),
            },
        )

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        event = self.get_object(request, pk)
        return self.render(request, event, SubscribedEventForm(user=request.user))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        event = self.get_object(request, pk)
        form = SubscribedEventForm(request.POST, user=request.user)
        if event.all_day or _already_made(request.user, event) is not None:
            # The page offers no form for either; a post anyway changes nothing.
            return redirect(event.get_absolute_url())
        if not form.is_valid():
            return self.render(request, event, form)
        uid = stored_uid(event.uid) if event.uid else ""
        notes = "\n\n".join(part for part in (event.summary, event.description) if part)
        ends_at = event.ends_at
        if ends_at is not None and ends_at - event.starts_at > dt.timedelta(days=2):
            ends_at = event.starts_at + DEFAULT_INTERVIEW_LENGTH
        data = form.cleaned_data
        if data["action"] == SubscribedEventForm.AS_INTERVIEW:
            try:
                interview = schedule_interview(
                    data["application"],
                    kind=data.get("kind") or "video",
                    starts_at=event.starts_at,
                    ends_at=ends_at,
                    location=event.location,
                    notes=notes,
                    actor=_("the subscribed calendar %(name)s") % {"name": event.subscription.name},
                    uid=uid,
                )
            except InterviewRefused as refused:
                form.add_error(None, str(refused))
                return self.render(request, event, form)
            messages.success(request, _("Added to your interviews. It is yours to change now."))
            return redirect(interview.get_absolute_url())
        appointment = add_appointment(
            data["company"],
            starts_at=event.starts_at,
            ends_at=ends_at,
            place=event.location,
            notes=notes,
            uid=uid,
        )
        messages.success(request, _("Added to your appointments. It is yours to change now."))
        return redirect(appointment.get_absolute_url())
