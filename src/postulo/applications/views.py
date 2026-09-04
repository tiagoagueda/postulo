"""Views for applications, their timeline, tags and reminders."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin
from postulo.core.models import Tag
from postulo.jobs.views import UserFormKwargsMixin

from .analytics import build as build_insights
from .forms import (
    ApplicationForm,
    ApplicationIntakeForm,
    EventForm,
    ReminderForm,
    StatusChangeForm,
    TagForm,
)
from .models import BOARD_STATUSES, Application, Reminder, Status
from .services import change_status, create_application, get_or_create_company, record_event


class ApplicationFilterMixin:
    """Shared filtering for the table and the board.

    Both views answer the same question — "which of my applications am I looking at?" —
    so the filters live in one place rather than drifting apart.
    """

    def filter_queryset(self, queryset):
        params = self.request.GET

        search = params.get("q", "").strip()
        if search:
            queryset = queryset.filter(
                Q(posting__title__icontains=search)
                | Q(posting__company__name__icontains=search)
                | Q(posting__location__icontains=search)
            )

        status = params.get("status", "").strip()
        if status:
            queryset = queryset.filter(status=status)

        tag = params.get("tag", "").strip()
        if tag:
            queryset = queryset.filter(tags__slug=tag)

        state = params.get("state", "").strip()
        if state == "open":
            queryset = queryset.open()
        elif state == "closed":
            queryset = queryset.closed()

        return queryset.distinct()

    def filter_context(self) -> dict:
        return {
            "search": self.request.GET.get("q", ""),
            "selected_status": self.request.GET.get("status", ""),
            "selected_tag": self.request.GET.get("tag", ""),
            "selected_state": self.request.GET.get("state", ""),
            "statuses": Status.choices,
            "tags": Tag.objects.for_user(self.request.user),
        }


class ApplicationListView(OwnedObjectMixin, ApplicationFilterMixin, ListView):
    model = Application
    template_name = "applications/application_list.html"
    context_object_name = "applications"
    paginate_by = 50

    def get_queryset(self):
        return self.filter_queryset(super().get_queryset().with_display_data())

    def get_context_data(self, **kwargs) -> dict:
        return {**super().get_context_data(**kwargs), **self.filter_context()}


class ApplicationBoardView(OwnedObjectMixin, ApplicationFilterMixin, ListView):
    """The same applications, arranged by status.

    Only open statuses get a column. Rejections and withdrawals belong in the table and
    the figures, not taking up space on a board meant to show what is still live.
    """

    model = Application
    template_name = "applications/application_board.html"
    context_object_name = "applications"

    def get_queryset(self):
        return self.filter_queryset(super().get_queryset().with_display_data())

    def get_context_data(self, **kwargs) -> dict:
        context = {**super().get_context_data(**kwargs), **self.filter_context()}
        applications = list(context["applications"])
        context["columns"] = [
            {
                "status": status,
                "label": Status(status).label,
                "applications": [a for a in applications if a.status == status],
            }
            for status in BOARD_STATUSES
        ]
        return context


class ApplicationDetailView(OwnedObjectMixin, DetailView):
    model = Application
    template_name = "applications/application_detail.html"
    context_object_name = "application"

    def get_queryset(self):
        return super().get_queryset().select_related("posting", "posting__company", "contact")

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["events"] = self.object.events.all()
        context["reminders"] = self.object.reminders.filter(done_at__isnull=True)
        context["event_form"] = EventForm()
        context["status_form"] = StatusChangeForm(initial={"status": self.object.status})
        return context


class ApplicationCreateView(OwnedObjectMixin, View):
    """Record a company, a posting and an application in one submission."""

    template_name = "applications/application_intake.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user)

    def get(self, request) -> HttpResponse:
        return render(
            request, self.template_name, {"form": ApplicationIntakeForm(user=request.user)}
        )

    def post(self, request) -> HttpResponse:
        form = ApplicationIntakeForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        company = get_or_create_company(request.user, form.cleaned_data["company_name"])
        application = create_application(
            request.user,
            company=company,
            posting_data=form.posting_data,
            application_data=form.application_data,
        )
        application.tags.set(form.cleaned_data["tags"])

        messages.success(request, _("Application recorded."))
        return redirect(application.get_absolute_url())


class ApplicationUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Application
    form_class = ApplicationForm
    template_name = "applications/application_form.html"

    def get_object(self, queryset=None):
        application = super().get_object(queryset)
        # Captured before the form binds: form.instance *is* this object, so once the
        # form has been populated the previous status is no longer available anywhere.
        self.status_before_edit = application.status
        return application

    def form_valid(self, form):
        # Save everything except the status, then move the status through the service so
        # the change reaches the timeline. Otherwise the edit form becomes a quiet way
        # to end up with a status the log cannot account for.
        requested_status = form.cleaned_data["status"]
        form.instance.status = self.status_before_edit
        response = super().form_valid(form)
        change_status(self.object, requested_status)
        messages.success(self.request, _("Application updated."))
        return response


class ApplicationDeleteView(OwnedObjectMixin, DeleteView):
    model = Application
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("applications:list")

    def form_valid(self, form):
        messages.success(self.request, _("Application deleted."))
        return super().form_valid(form)


class ApplicationStatusView(OwnedObjectMixin, View):
    """The quick status action, used from the board and the detail page."""

    def get_queryset(self):
        return Application.objects.for_user(self.request.user).with_display_data()

    def post(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        form = StatusChangeForm(request.POST)
        if form.is_valid():
            changed = change_status(
                application, form.cleaned_data["status"], note=form.cleaned_data.get("note", "")
            )
            if changed is not None:
                messages.success(request, _("Status updated."))
        else:
            messages.error(request, _("That is not a status Postulo recognises."))

        if request.htmx:
            application.refresh_from_db()
            return render(
                request, "applications/partials/application_row.html", {"application": application}
            )
        return redirect(request.POST.get("next") or application.get_absolute_url())


class EventCreateView(OwnedObjectMixin, View):
    """Append an entry to an application's timeline."""

    def get_queryset(self):
        return Application.objects.for_user(self.request.user)

    def post(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        form = EventForm(request.POST)
        if form.is_valid():
            record_event(
                application,
                kind=form.cleaned_data["kind"],
                summary=form.cleaned_data["summary"],
                body=form.cleaned_data["body"],
                occurred_at=form.cleaned_data["occurred_at"],
            )
            messages.success(request, _("Added to the timeline."))
        else:
            messages.error(request, _("That entry could not be saved."))
        return redirect(application.get_absolute_url())


# ------------------------------------------------------------------- reminders


class ReminderListView(OwnedObjectMixin, ListView):
    model = Reminder
    template_name = "applications/reminder_list.html"
    context_object_name = "reminders"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("application", "application__posting")
        if self.request.GET.get("show") != "all":
            queryset = queryset.outstanding()
        return queryset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["showing_all"] = self.request.GET.get("show") == "all"
        return context


class ReminderCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = Reminder
    form_class = ReminderForm
    template_name = "applications/reminder_form.html"
    success_url = reverse_lazy("applications:reminder_list")

    def get_initial(self) -> dict:
        initial = super().get_initial()
        application_id = self.request.GET.get("application")
        if (
            application_id
            and Application.objects.for_user(self.request.user).filter(pk=application_id).exists()
        ):
            initial["application"] = application_id
        return initial

    def get_success_url(self) -> str:
        if self.object.application_id:
            return reverse("applications:detail", args=[self.object.application_id])
        return str(self.success_url)


class ReminderCompleteView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Reminder.objects.for_user(self.request.user)

    def post(self, request, pk: int) -> HttpResponse:
        reminder = get_object_or_404(self.get_queryset(), pk=pk)
        reminder.complete()
        messages.success(request, _("Marked as done."))
        return redirect(request.POST.get("next") or reverse("applications:reminder_list"))


# ------------------------------------------------------------------------ tags


class TagListView(OwnedObjectMixin, ListView):
    model = Tag
    template_name = "applications/tag_list.html"
    context_object_name = "tags"


class TagCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = Tag
    form_class = TagForm
    template_name = "applications/tag_form.html"
    success_url = reverse_lazy("applications:tag_list")


class TagUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Tag
    form_class = TagForm
    template_name = "applications/tag_form.html"
    success_url = reverse_lazy("applications:tag_list")


class TagDeleteView(OwnedObjectMixin, DeleteView):
    model = Tag
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("applications:tag_list")


# --------------------------------------------------------------------- insights


class InsightsView(OwnedObjectMixin, TemplateView):
    """What the record says about the search.

    Read from the event log rather than from current statuses, so an application that
    was interviewing before it was rejected still counts as an interview. Anything else
    would report that a search which reached three final rounds had reached none.
    """

    template_name = "applications/insights.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["insights"] = build_insights(self.request.user)
        return context
