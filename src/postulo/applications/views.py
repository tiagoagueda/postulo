"""Views for applications, their timeline, tags and reminders."""

from __future__ import annotations

from datetime import timedelta
from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    RedirectView,
    UpdateView,
)

from postulo.core import tables
from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin
from postulo.core.models import Tag
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin

from . import ical, quiet, reports, suggestions
from .forms import (
    ApplicationForm,
    ApplicationIntakeForm,
    EventForm,
    InterviewForm,
    ReminderForm,
    StatusChangeForm,
    TagForm,
)
from .models import (
    BOARD_STATUSES,
    SETTLED_OUTCOMES,
    Application,
    EventKind,
    Interview,
    InterviewOutcome,
    Reminder,
    Status,
    Suggestion,
)
from .services import (
    DEFAULT_INTERVIEW_LENGTH,
    change_status,
    create_application,
    get_or_create_company,
    record_event,
    reschedule_interview,
    schedule_interview,
    settle_interview,
)
from .tables import ApplicationsTable


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

        if params.get("quiet", "").strip():
            queryset = queryset.quiet(quiet.threshold_for(self.request.user))

        return queryset.distinct()

    def filter_context(self) -> dict:
        return {
            "search": self.request.GET.get("q", ""),
            "selected_status": self.request.GET.get("status", ""),
            "selected_tag": self.request.GET.get("tag", ""),
            "selected_state": self.request.GET.get("state", ""),
            "selected_quiet": bool(self.request.GET.get("quiet", "").strip()),
            "statuses": Status.choices,
            "tags": Tag.objects.for_user(self.request.user),
        }


class ApplicationListView(OwnedObjectMixin, ApplicationFilterMixin, ListView):
    """The table: sortable, narrowable from its headers, and laid out as the person likes."""

    model = Application
    template_name = "applications/application_list.html"
    context_object_name = "applications"

    @cached_property
    def table(self) -> ApplicationsTable:
        return ApplicationsTable(
            self.request, tables.settings_for(self.request.user, ApplicationsTable.name)
        )

    def get_paginate_by(self, queryset) -> int:
        return self.table.page_size

    def get_queryset(self):
        queryset = super().get_queryset().with_display_data().with_table_data()
        return self.table.apply(self.filter_queryset(queryset))

    def get_template_names(self) -> list[str]:
        # An htmx request wants the table alone; the back button's restore wants the page.
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return [f"{self.template_name}#htmx"]
        return [self.template_name]

    def get_context_data(self, **kwargs) -> dict:
        return {
            **super().get_context_data(**kwargs),
            **self.filter_context(),
            "table": self.table,
            "page_sizes": tables.PAGE_SIZES,
            "bulk_tags": Tag.objects.for_user(self.request.user),
            "bulk_statuses": Status.choices,
        }


class ApplicationBulkView(LoginRequiredMixin, View):
    """Tag several applications, or move several along, in one submission (#134).

    Additive only, deliberately. Deleting forty applications is a different act from deleting
    one and wants a confirmation of its own; until that exists there is no bulk delete.

    Status changes go through `change_status`, not through `update()`. The event log is the
    truth here, and forty applications quietly moved with no timeline entries would be forty
    records that cannot say when they moved or why — which is the one thing the log exists for.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from django.contrib import messages

        from postulo.core import bulk

        rows = bulk.chosen_rows(request, Application)
        if not rows.exists():
            messages.info(request, bulk.nothing_chosen())
            return redirect(self._back(request))

        action = request.POST.get(bulk.ACTION, "")
        if action == "tag":
            count = self._tag(request, rows)
        elif action == "status":
            count = self._status(request, rows)
        else:
            messages.error(request, _("That is not something Postulo can do to several at once."))
            return redirect(self._back(request))

        messages.success(request, bulk.changed(count, ApplicationsTable.noun))
        return redirect(self._back(request))

    def _tag(self, request: HttpRequest, rows) -> int:
        """Add one of this person's own tags to each. Never creates one from a posted name.

        A tag arriving as a *name* would let a bulk action invent taxonomy from a field
        nobody looked at; a tag arriving as an id is one they already have, re-scoped like
        everything else here.
        """
        tag = Tag.objects.for_user(request.user).filter(pk=_as_int(request.POST.get("tag"))).first()
        if tag is None:
            return 0
        changed = 0
        for application in rows:
            if not application.tags.filter(pk=tag.pk).exists():
                application.tags.add(tag)
                changed += 1
        return changed

    def _status(self, request: HttpRequest, rows) -> int:
        from .services import change_status

        wanted = request.POST.get("status", "")
        if wanted not in {value for value, _label in Status.choices}:
            return 0
        changed = 0
        for application in rows:
            # Returns None when the status was already that, which is not a change and must
            # not be counted as one.
            if change_status(application, wanted, actor=str(request.user)) is not None:
                changed += 1
        return changed

    @staticmethod
    def _back(request: HttpRequest) -> str:
        from postulo.core.redirects import safe_next

        return safe_next(request, reverse("applications:list"))


def _as_int(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


class ApplicationBoardView(OwnedObjectMixin, ApplicationFilterMixin, ListView):
    """The same applications, arranged by status.

    Only open statuses get a column. Rejections and withdrawals belong in the table and
    the figures, not taking up space on a board meant to show what is still live.
    """

    model = Application
    template_name = "applications/application_board.html"
    context_object_name = "applications"

    def get_queryset(self):
        # The cards say how long a quiet application has been quiet, so they need the
        # last activity too.
        return self.filter_queryset(super().get_queryset().with_display_data().with_activity())

    def get_context_data(self, **kwargs) -> dict:
        context = {**super().get_context_data(**kwargs), **self.filter_context()}
        applications = list(context["applications"])
        # The same predicate as the dashboard, so the badge and the block agree.
        quiet_ids = set(quiet.quiet_applications(self.request.user).values_list("pk", flat=True))
        for application in applications:
            application.is_quiet = application.pk in quiet_ids
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
        return (
            super()
            .get_queryset()
            .select_related(
                "posting",
                "posting__company",
                "posting__company__parent",
                "contact",
                "department",
                "department__company",
            )
        )

    def get_context_data(self, **kwargs) -> dict:
        from postulo.jobs import structure

        context = super().get_context_data(**kwargs)
        # One answer about one person, decided here rather than asked of each block.
        context["structure_on"] = structure.structure_allowed(self.request.user)
        context["group"] = structure.group_of(self.object.posting.company, self.request.user)
        context["events"] = self.object.events.all()
        context["reminders"] = self.object.reminders.filter(done_at__isnull=True)
        interviews = list(self.object.interviews.prefetch_related("contacts"))
        context["scheduled_interviews"] = [i for i in interviews if i.is_scheduled]
        context["settled_interviews"] = [i for i in interviews if i.is_settled][::-1]
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
        application.tags.set(form.chosen_tags())

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
        return redirect(safe_next(request, application.get_absolute_url()))


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
        return redirect(safe_next(request, reverse("applications:reminder_list")))


class ApplicationQuietActionView(OwnedObjectMixin, View):
    """What to do about an application that has gone quiet, from the dashboard.

    *Followed up* records the follow-up on the timeline; *Snooze* sets a reminder two
    weeks out, which by definition makes the application not quiet. *Ghosted* is the
    ordinary status action, so the timeline says when the person gave up waiting.
    """

    def get_queryset(self):
        return Application.objects.for_user(self.request.user).select_related(
            "posting", "posting__company"
        )

    def post(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        action = request.POST.get("action", "")
        if action == "follow_up":
            record_event(
                application,
                kind=EventKind.FOLLOW_UP,
                summary=str(_("Followed up")),
                body=request.POST.get("note", ""),
            )
            messages.success(request, _("Follow-up recorded."))
        elif action == "snooze":
            Reminder.objects.create(
                owner=request.user,
                application=application,
                summary=str(
                    _("Chase %(company)s about %(role)s")
                    % {
                        "company": application.posting.company.name,
                        "role": application.posting.title,
                    }
                ),
                due_at=quiet.snooze_until(),
            )
            messages.success(request, _("Snoozed for two weeks."))
        else:
            messages.error(request, _("That is not something Postulo can do about it."))
        return redirect(safe_next(request, application.get_absolute_url()))


# ------------------------------------------------------------------ interviews


class InterviewListView(OwnedObjectMixin, ListView):
    """Everything in the diary, soonest first; the past too on request."""

    model = Interview
    template_name = "applications/interview_list.html"
    context_object_name = "interviews"

    def get_queryset(self):
        queryset = super().get_queryset().with_display_data()
        if self.request.GET.get("show") == "all":
            return queryset.order_by("-starts_at", "-pk")
        return queryset.scheduled().order_by("starts_at", "pk")

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["showing_all"] = self.request.GET.get("show") == "all"
        return context


class InterviewCreateView(OwnedObjectMixin, View):
    """Schedule an interview for one application, or record one that already happened."""

    template_name = "applications/interview_form.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user).select_related(
            "posting", "posting__company"
        )

    def get(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        tomorrow = timezone.localtime() + timedelta(days=1)
        form = InterviewForm(
            user=request.user,
            application=application,
            initial={"starts_at": tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)},
        )
        return render(request, self.template_name, {"form": form, "application": application})

    def post(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        form = InterviewForm(request.POST, user=request.user, application=application)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "application": application})
        data = form.cleaned_data
        interview = schedule_interview(
            application,
            kind=data["kind"],
            starts_at=data["starts_at"],
            ends_at=data["ends_at"],
            location=data["location"],
            notes=data["notes"],
            contacts=data["contacts"],
            remind=data["remind"],
        )
        if interview.is_settled:
            messages.success(request, _("Interview recorded."))
        else:
            messages.success(request, _("Interview scheduled."))
        return redirect(application.get_absolute_url())


class InterviewUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Interview
    form_class = InterviewForm
    template_name = "applications/interview_form.html"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("application", "application__posting", "application__posting__company")
        )

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["application"] = self.object.application
        return context

    def form_valid(self, form):
        # Everything but the times is saved as edited; the times go through the service,
        # so a move is written on the timeline and the reminder moves with it.
        previous = Interview.objects.get(pk=self.object.pk)
        starts_at = form.cleaned_data["starts_at"]
        ends_at = form.cleaned_data["ends_at"] or starts_at + DEFAULT_INTERVIEW_LENGTH
        form.instance.starts_at, form.instance.ends_at = previous.starts_at, previous.ends_at
        interview = form.save()
        reschedule_interview(interview, starts_at=starts_at, ends_at=ends_at)
        messages.success(self.request, _("Interview updated."))
        return redirect(interview.application.get_absolute_url())


class InterviewOutcomeView(OwnedObjectMixin, View):
    """How it went: held, cancelled, or nobody came."""

    def get_queryset(self):
        return Interview.objects.for_user(self.request.user).select_related("application")

    def post(self, request, pk: int) -> HttpResponse:
        interview = get_object_or_404(self.get_queryset(), pk=pk)
        outcome = request.POST.get("outcome", "")
        if outcome not in SETTLED_OUTCOMES:
            messages.error(request, _("That is not an outcome Postulo recognises."))
        else:
            settle_interview(interview, outcome, note=request.POST.get("note", ""))
            messages.success(
                request,
                {
                    InterviewOutcome.DONE: _("Recorded as held."),
                    InterviewOutcome.CANCELLED: _("Recorded as cancelled."),
                    InterviewOutcome.NO_SHOW: _("Recorded: nobody showed up."),
                }[outcome],
            )
        return redirect(safe_next(request, interview.application.get_absolute_url()))


class InterviewCalendarView(OwnedObjectMixin, View):
    """An .ics file: one interview, or everything still ahead."""

    def get_queryset(self):
        return Interview.objects.for_user(self.request.user).with_display_data()

    def get(self, request, pk: int | None = None) -> HttpResponse:
        if pk is not None:
            interviews = [get_object_or_404(self.get_queryset(), pk=pk)]
            filename = f"interview-{pk}.ics"
        else:
            interviews = list(self.get_queryset().upcoming())
            filename = "interviews.ics"
        text = ical.calendar(
            interviews,
            url_for=lambda i: request.build_absolute_uri(i.application.get_absolute_url()),
        )
        response = HttpResponse(text, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


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


class InsightsView(LoginRequiredMixin, RedirectView):
    """Insights is the dashboard now (#44).

    It answered the same question the dashboard did — *how is this going?* — at a
    different distance, and a person had to remember which page held which number. Its
    figures are widgets on the one page. This stays so that a bookmark, a link in an old
    note or a browser's history lands somewhere useful instead of on a 404.
    """

    pattern_name = "core:home"
    permanent = False


# ------------------------------------------------------------------- reports


class ReportView(LoginRequiredMixin, View):
    """A report about a period: what was sent, how regularly, and what came back (#56).

    **On request rather than scheduled.** A report arriving on the first of the month is
    obvious once a scheduler and notifiers exist, and it is a different issue: this one had
    to settle what is *on* the page, and that is answerable now. Scheduling a page nobody has
    read yet would be scheduling a guess.

    **The period lives in the address**, which makes it a question rather than a preference
    -- the same line this project already draws for a table's sort and filters. A report for
    a particular month is a thing to bookmark, to send to somebody, and to produce again next
    year and get the same page back.
    """

    template_name = "applications/report.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        period = reports.period_from(request.GET)
        report = reports.build(request.user, period)
        earlier = period.shifted(-1)
        later = period.shifted(1)
        return render(
            request,
            self.template_name,
            {
                "report": report,
                "period": period,
                "earlier": urlencode(reports.as_query(earlier)) if earlier else "",
                # Never a period that has not happened. A report about next month is a blank
                # page pretending to be a document.
                "later": (
                    urlencode(reports.as_query(later))
                    if later and later.start <= timezone.localdate()
                    else ""
                ),
                "week_choices": reports.WEEK_CHOICES,
                "kind": period.kind,
                "on_month": period.start.strftime("%Y-%m"),
                "on_quarter": f"{period.start.year}-Q{(period.start.month - 1) // 3 + 1}",
                "weeks": period.days // 7,
            },
        )


class ReportCSVView(LoginRequiredMixin, View):
    """The evidence list as a spreadsheet, for anybody who wants to do their own sums."""

    def get(self, request: HttpRequest) -> HttpResponse:
        report = reports.build(request.user, reports.period_from(request.GET))
        response = HttpResponse(reports.as_csv(report), content_type="text/csv; charset=utf-8")
        name = reports.filename(report, "csv")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response


class ReportPDFView(LoginRequiredMixin, View):
    """The report as a document to hand over.

    It carries the name, the period and the day it was produced, because a document with no
    date is not evidence of anything.

    **Pressing the button files it; opening the address does not.** The button on the page
    posts, and the PDF it hands back is also filed under Sent documents as a *report* -- a
    document handed to an employment office is exactly the document somebody wants kept,
    and a store copies it the way it copies a CV (#162). A GET is the same PDF and no
    record: a bookmarked address, a link in a message, a crawler that follows one, must
    never leave a document behind. The moment of freezing is a deliberate press.
    """

    def _html(self, request: HttpRequest) -> tuple:
        report = reports.build(request.user, reports.period_from(request.GET))
        html = render(request, "applications/report_print.html", {"report": report}).content
        return report, html.decode()

    def _back_to_the_page(self, request: HttpRequest, report, unavailable) -> HttpResponse:
        messages.error(request, str(unavailable))
        return redirect(
            f"{reverse('applications:report')}?{urlencode(reports.as_query(report.period))}"
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        from postulo.documents.pdf import PDFBackendUnavailable, html_to_pdf

        report, html = self._html(request)
        try:
            pdf = html_to_pdf(html)
        except PDFBackendUnavailable as unavailable:
            return self._back_to_the_page(request, report, unavailable)
        response = HttpResponse(pdf, content_type="application/pdf")
        name = reports.filename(report, "pdf")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    def post(self, request: HttpRequest) -> HttpResponse:
        from postulo.core.files import serve_private_file
        from postulo.documents.pdf import PDFBackendUnavailable
        from postulo.documents.rendering import snapshot_report

        report, html = self._html(request)
        title = str(_("Job search report · %(period)s")) % {"period": report.period.label}
        try:
            document = snapshot_report(
                request.user,
                title=title,
                html=html,
                filename=reports.filename(report, "pdf"),
            )
        except PDFBackendUnavailable as unavailable:
            return self._back_to_the_page(request, report, unavailable)
        messages.success(
            request,
            _("Filed under Sent documents, dated today, so what you handed over is kept."),
        )
        return serve_private_file(
            request, document.file, download_name=document.download_name, as_attachment=True
        )


# --------------------------------------------------------------- suggestions


class SuggestionListView(OwnedObjectMixin, ListView):
    """What the plugins think happened, waiting for a person to agree or not."""

    model = Suggestion
    template_name = "applications/suggestion_list.html"
    context_object_name = "suggestions"
    paginate_by = 50

    def get_queryset(self):
        queryset = super().get_queryset().select_related("application__posting__company", "event")
        if self.request.GET.get("show") != "all":
            queryset = queryset.pending()
        return queryset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["showing_all"] = self.request.GET.get("show") == "all"
        context["pending_count"] = suggestions.pending_count(self.request.user)
        context["open_applications"] = (
            Application.objects.for_user(self.request.user)
            .open()
            .select_related("posting__company")[:200]
        )
        return context


class SuggestionActionView(OwnedObjectMixin, View):
    """Accept a suggestion into the record, or decline it for good."""

    def get_queryset(self):
        return Suggestion.objects.for_user(self.request.user)

    def post(self, request: HttpResponse, pk: int, action: str):
        suggestion = get_object_or_404(self.get_queryset(), pk=pk)
        fallback = reverse("applications:suggestion_list")
        if not suggestion.is_pending:
            messages.info(request, _("That suggestion has already been answered."))
            return redirect(safe_next(request, fallback))

        if action == "decline":
            suggestions.decline(suggestion)
            messages.success(request, _("Declined. It will not be suggested again."))
            return redirect(safe_next(request, fallback))

        application = suggestion.application
        chosen = request.POST.get("application")
        if chosen:
            application = get_object_or_404(Application.objects.for_user(request.user), pk=chosen)
        if application is None:
            messages.error(request, _("Choose which application this is about first."))
            return redirect(safe_next(request, fallback))

        suggestions.accept(suggestion, application=application)
        messages.success(request, _("Added to the timeline."))
        return redirect(safe_next(request, fallback))
