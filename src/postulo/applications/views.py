"""Views for applications, their timeline, tags and reminders."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import cached_property
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import formats, timezone
from django.utils.decorators import method_decorator
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

from postulo.core import languages, tables
from postulo.core.mixins import (
    ConfirmDeleteMixin,
    OwnedObjectMixin,
    OwnerFormMixin,
    PageOrFragmentMixin,
)
from postulo.core.models import Tag
from postulo.core.params import as_pk
from postulo.core.redirects import safe_next
from postulo.core.search import clean_query
from postulo.jobs.history import history_of
from postulo.jobs.views import UserFormKwargsMixin

from . import agenda, endings, ical, quiet, reports, suggestions
from .forms import (
    ApplicationForm,
    ApplicationIntakeForm,
    EventForm,
    InterviewForm,
    OfferForm,
    ReminderForm,
    StatusChangeForm,
    TagForm,
)
from .models import (
    BOARD_STATUSES,
    END_STATUSES,
    SETTLED_OUTCOMES,
    STATUS_ICONS,
    Application,
    EventKind,
    Interview,
    InterviewOutcome,
    Offer,
    Reminder,
    Status,
    Suggestion,
    status_options,
)
from .services import (
    DEFAULT_INTERVIEW_LENGTH,
    change_status,
    create_application,
    get_or_create_company,
    later_time,
    postpone_reminder,
    record_event,
    record_offer,
    reschedule_interview,
    revise_offer,
    schedule_interview,
    settle_interview,
    withdraw_offer,
)
from .tables import ApplicationsTable


class ApplicationFilterMixin:
    """Shared filtering for the table and the board.

    Both views answer the same question — "which of my applications am I looking at?" —
    so the filters live in one place rather than drifting apart.
    """

    @property
    def asked_status(self) -> str:
        """The status the address asks for: the parameter itself, so the last of two.

        One reading for both shapes (#315). The table narrows to it and the board folds to
        it, and a board that read the address another way would fold to one column under a
        count of another.
        """
        return self.request.GET.get("status", "").strip()

    def filter_queryset(self, queryset, *, by_status: bool = True):
        """Narrow by everything the address asks. ``by_status=False`` leaves the status out,
        for the columns a folded board draws as strips: each still says how many cards it
        holds of what the other filters match."""
        params = self.request.GET

        # Through `clean_query`, which keeps two hundred characters: a longer pattern is
        # one SQLite refuses with an error, and an address can hold anything (#313).
        search = clean_query(params.get("q", ""))
        if search:
            queryset = queryset.filter(
                Q(posting__title__icontains=search)
                | Q(posting__company__name__icontains=search)
                | Q(posting__location__icontains=search)
            )

        status = self.asked_status if by_status else ""
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
            "selected_status": self.asked_status,
            "selected_tag": self.request.GET.get("tag", ""),
            "selected_state": self.request.GET.get("state", ""),
            "selected_quiet": bool(self.request.GET.get("quiet", "").strip()),
            "statuses": Status.choices,
            # The same statuses with the icon each draws in a card's list, and the icons
            # themselves, drawn once for the whole board (#301).
            "status_options": status_options(),
            "status_icons": list(STATUS_ICONS.values()),
            "tags": Tag.objects.for_user(self.request.user),
        }


class ApplicationListView(PageOrFragmentMixin, OwnedObjectMixin, ApplicationFilterMixin, ListView):
    """Applications, in one of two shapes: the table, or the board (#102).

    Both answer "which of my applications am I looking at?", so they were always one set
    of filters; they were two pages with a bare link between them that threw the filters
    away. One address now, and a switch on the page that changes the shape of what is
    below it and nothing else. The shape is remembered with the person's other table
    preferences, or asked for in the address with ``?view=``, which remembers nothing.

    The table is sortable, narrowable from its headers, paginated, and laid out as the
    person likes. The board arranges the same rows by status, and only open statuses get
    a column: rejections and withdrawals belong in the table and the figures, not taking
    up space on a board meant to show what is still live. A filter that matches settled
    applications is therefore said on the board rather than shown as nothing.

    A status in the address means one thing in both shapes (#315): the table narrows to
    it, and the board folds to it -- that column open, the others strips that keep their
    name and their count. So the count above either shape is of the same applications.
    """

    model = Application
    template_name = "applications/application_list.html"
    context_object_name = "applications"

    @cached_property
    def table(self) -> ApplicationsTable:
        return ApplicationsTable(
            self.request, tables.settings_for(self.request.user, ApplicationsTable.name)
        )

    @cached_property
    def asked_shape(self) -> str:
        """The shape the address asks for, or empty: a link into one shape, not a choice."""
        asked = self.request.GET.get("view", "")
        return asked if asked in ApplicationsTable.shapes else ""

    @cached_property
    def shape(self) -> str:
        return self.asked_shape or self.table.shape

    @property
    def on_board(self) -> bool:
        return self.shape == "board"

    def get_paginate_by(self, queryset):
        # A board is every card at once; the columns are what make it readable.
        return None if self.on_board else self.table.page_size

    def get(self, request, *args, **kwargs):
        # A bare address opens as the person's default view, when they have kept one (#259).
        # Only a bare one: anything with a parameter is a question they asked. Not for an
        # htmx swap either -- a swap is the page updating itself, not somebody arriving.
        opening = self.table.opening_url
        if opening and not getattr(request, "htmx", None):
            from django.shortcuts import redirect

            return redirect(opening)
        return super().get(request, *args, **kwargs)

    def matching(self):
        """Every application the filters match, whatever shape is being drawn.

        The board draws only the live ones, and the two counts above it are about all of
        them: *how many match* and *how many of those are settled* are questions about the
        filter, not about the columns. Answered with `COUNT` rather than by loading the rows
        and measuring the list, which is what the board did before it stopped loading the
        rows it does not draw (#231).
        """
        return self.filter_queryset(super().get_queryset())

    def get_queryset(self):
        queryset = super().get_queryset().with_display_data()
        if self.on_board:
            # The cards say how long a quiet application has been quiet and whether it is,
            # so they need the activity annotations and the badge that reads them (#231).
            #
            # `status__in` in SQL, not in Python. The board used to load every application
            # ever recorded -- rejected, accepted, withdrawn, all of it -- with four
            # subqueries each, and then drop the ones its columns do not show. Somebody with
            # four hundred settled applications and twelve live ones was reading four
            # hundred and twelve rows to draw twelve.
            return self.filter_queryset(
                queryset.with_quiet_flag(quiet.threshold_for(self.request.user))
            ).filter(status__in=list(BOARD_STATUSES))
        return self.table.apply(self.filter_queryset(queryset.with_table_data()))

    def shape_url(self, shape: str) -> str:
        """This page, with these filters, in ``shape``."""
        query = self.request.GET.copy()
        query["view"] = shape
        return f"{self.request.path}?{query.urlencode()}"

    def fold_url(self, status: str = "") -> str:
        """This board folded to one column, or with every column open (#315).

        The rest of the question is kept. Not a saved view's name, which the question no
        longer is, as the search box and *Narrow* leave it out (`Table.search_keeps`).

        Where nothing is left to ask and a default view is kept, the address says the
        plain board, as `Table.clear_url` does and for its reason (#259): the bare
        address opens as the default view. *All columns* pointing at it led back into
        the fold where that view is a status, and into the view where it is anything
        else; with scripts off the board could not be unfolded.
        """
        query = self.request.GET.copy()
        for name in ("status", *tables.NOT_SAVED):
            query.pop(name, None)
        if status:
            query["status"] = status
        elif not query and self.table.default_view is not None:
            query[tables.SAVED] = tables.PLAIN
        return f"{self.request.path}?{query.urlencode()}" if query else self.request.path

    def column_counts(self) -> dict[str, int]:
        """How many cards each column holds of what the filters match, the status aside.

        For a folded board, whose folded columns are drawn as strips: a `COUNT` for each,
        in one query, and none of their rows.
        """
        counted = (
            self.filter_queryset(super().get_queryset(), by_status=False)
            .filter(status__in=list(BOARD_STATUSES))
            .order_by()
            .values("status")
            .annotate(cards=Count("pk", distinct=True))
        )
        return {row["status"]: row["cards"] for row in counted}

    def board_columns(self, applications: list) -> list[dict]:
        """The board's columns, and which of them the address leaves open (#315).

        ``?status=`` folds the board: the column of that status is open and holds its
        cards, and every other is a strip that says its name and its count. The rows read
        are the open column's alone, which is what the address has always asked for; the
        strips are counted. A status with no column -- a settled one -- leaves every
        column a strip, and the page says where those applications are.

        Each column carries the address its heading leads to: the board folded to it, or,
        for the one a folded board has open, the board with every column open again.
        """
        asked = self.asked_status
        counts = self.column_counts() if asked else {}
        columns = []
        for status in BOARD_STATUSES:
            cards = [a for a in applications if a.status == status]
            is_open = not asked or status == asked
            columns.append(
                {
                    "status": status,
                    "label": Status(status).label,
                    "applications": cards,
                    "count": len(cards) if is_open else counts.get(status, 0),
                    "open": is_open,
                    "unfolds": bool(asked) and is_open,
                    "url": self.fold_url("" if asked and is_open else status),
                }
            )
        return columns

    def get_context_data(self, **kwargs) -> dict:
        context = {
            **super().get_context_data(**kwargs),
            **self.filter_context(),
            "table": self.table,
            "shape": self.shape,
            "shape_param": self.asked_shape,
            "shapes": [
                (shape, ApplicationsTable.SHAPE_LABELS[shape]) for shape in ApplicationsTable.shapes
            ],
            "page_sizes": tables.PAGE_SIZES,
            "bulk_tags": Tag.objects.for_user(self.request.user),
            "bulk_statuses": Status.choices,
        }
        # Where the switch sends the person back to: here, with every filter and the sort,
        # and without an asked-for shape, so the one just chosen is what shows.
        back = self.request.GET.copy()
        back.pop("view", None)
        context["switch_next"] = (
            f"{self.request.path}?{back.urlencode()}" if back else self.request.path
        )
        if self.on_board:
            applications = list(context["applications"])
            # `is_quiet` is annotated on the rows (#231). It used to be a second query over
            # the whole set, comparing primary keys, which is the board's four subqueries run
            # again for an answer the rows already carried.
            context["columns"] = self.board_columns(applications)
            # Folded whenever the address asks for a status, with a column or without one:
            # *All columns* is then the one way back, drawn in the one place (#315). It
            # leaves the focus on the heading of the column that was open, or on the first.
            asked = self.asked_status
            context["folded"] = bool(asked)
            context["unfold_url"] = self.fold_url()
            context["unfold_lands"] = asked if asked in BOARD_STATUSES else BOARD_STATUSES[0]
            context["total"] = self.matching().count()
            # Said only when a filter is narrowing: with none, settled applications are
            # simply not the board's business and the table is where they live. A `COUNT`
            # rather than the rows, because the rows are not drawn and were never needed --
            # and only when the sentence is going to be said at all.
            context["off_board"] = (
                self.matching().exclude(status__in=list(BOARD_STATUSES)).count()
                if self.table.filters_active
                else 0
            )
            # A board folded to a status that has no column draws no column open, and has
            # to say why whether anything matches or not: seven strips and no word was
            # what *Withdrawn* drew for somebody who never withdrew. A status that is no
            # status at all is said to be none, since nothing is settled about it.
            no_column = bool(asked) and asked not in BOARD_STATUSES
            context["off_board_said"] = bool(context["off_board"]) or (
                no_column and asked in Status.values
            )
            context["no_such_status"] = no_column and asked not in Status.values
            context["table_url"] = self.shape_url("table")
        else:
            context["total"] = context["paginator"].count
        return context


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

        messages.success(request, bulk.changed(count, ApplicationsTable))
        return redirect(self._back(request))

    def _tag(self, request: HttpRequest, rows) -> int:
        """Add one of this person's own tags to each. Never creates one from a posted name.

        A tag arriving as a *name* would let a bulk action invent taxonomy from a field
        nobody looked at; a tag arriving as an id is one they already have, re-scoped like
        everything else here.
        """
        from postulo.core import bulk

        tag = Tag.objects.for_user(request.user).filter(pk=as_pk(request.POST.get("tag"))).first()
        if tag is None:
            return 0
        return bulk.link_all(list(rows), "tags", tag)

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


class ApplicationBoardView(RedirectView):
    """The board's old address. It is a shape of the Applications page now (#102), and a
    bookmark or a link from before still lands on the board, with whatever it carried."""

    permanent = False

    def get_redirect_url(self, *args, **kwargs) -> str:
        query = self.request.GET.copy()
        query["view"] = "board"
        return f"{reverse('applications:list')}?{query.urlencode()}"


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
                "referred_by",
                "referred_by__company",
                "through_agency",
            )
        )

    def get_context_data(self, **kwargs) -> dict:
        from postulo.jobs import structure

        context = super().get_context_data(**kwargs)
        # One answer about one person, decided here rather than asked of each block.
        context["structure_on"] = structure.structure_allowed(self.request.user)
        context["group"] = structure.group_of(self.object.posting.company, self.request.user)
        # Read once and used twice: the timeline draws them, and how the application ended
        # is read from the same rows rather than asked for again (#239).
        context["events"] = list(self.object.events.all())
        context["ending"] = endings.read(self.object.status, endings.entries(context["events"]))
        # What arrived about the listing, read through the posting rather than copied when
        # the application was made (#270): the first part of one history, stored once.
        context["listing_events"] = history_of(self.object.posting)
        context["reminders"] = self.object.reminders.filter(done_at__isnull=True)
        context["offers"] = list(self.object.offers.all())
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
        """Save everything but the status, then move the status through the service.

        Otherwise the edit form becomes a quiet way to end up with a status the log cannot
        account for.

        The status written by the save is the one on the row *now*, read under a lock, not
        the one the form was drawn with (#231). Writing back what the form was drawn with is
        a write of a stale value: somebody advancing this application from the board while
        the form was open had their move overwritten by an edit that was not about the status
        at all, and `change_status` then found nothing to change and said nothing. The whole
        thing is one transaction, so the row cannot move between the reading and the writing.
        """
        requested_status = form.cleaned_data["status"]
        with transaction.atomic():
            current = (
                Application.objects.select_for_update()
                .filter(pk=form.instance.pk)
                .values_list("status", flat=True)
                .first()
            )
            form.instance.status = current or self.status_before_edit
            response = super().form_valid(form)
            change_status(self.object, requested_status)
        messages.success(self.request, _("Application updated."))
        return response


class ApplicationDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Application
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("applications:list")

    def get_context_data(self, **kwargs):
        """Say what goes with it, counted (#217).

        The timeline is the record of what happened, and it goes. What was *sent* does not:
        those PDFs are kept and still name the role and employer, which is the whole reason
        `RenderedDocument.application` is `SET_NULL`.
        """
        from django.utils.translation import ngettext

        context = super().get_context_data(**kwargs)
        application = self.object
        entries = application.events.count()
        interviews = application.interviews.count()
        reminders = application.reminders.count()
        # Each ngettext carries its two texts as literals: the extractor reads call sites.
        context["consequences"] = [
            text % {"count": number}
            for number, text in (
                (
                    entries,
                    ngettext(
                        "%(count)d entry on its timeline",
                        "%(count)d entries on its timeline",
                        entries,
                    ),
                ),
                (
                    interviews,
                    ngettext("%(count)d interview", "%(count)d interviews", interviews),
                ),
                (
                    reminders,
                    ngettext("%(count)d reminder", "%(count)d reminders", reminders),
                ),
            )
            if number
        ]
        sent = application.rendered_documents.count()
        if sent:
            context["kept"] = ngettext(
                "The %(count)d document you sent is kept, and still says where it went.",
                "The %(count)d documents you sent are kept, and still say where they went.",
                sent,
            ) % {"count": sent}
        return context

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
            status = form.cleaned_data["status"]
            reason = form.cleaned_data.get("end_reason", "")
            if reason and status not in END_STATUSES:
                # Told rather than refused, and rather than dropped without a word: the
                # box is on the form whatever the status, so a reason chosen beside
                # *Interviewing* is a slip, and the move is still what was asked for (#239).
                reason = ""
                messages.info(
                    request,
                    _(
                        "The reason was not kept: only an application that was rejected, "
                        "withdrawn or ghosted has one."
                    ),
                )
            changed = change_status(
                application, status, note=form.cleaned_data.get("note", ""), end_reason=reason
            )
            if changed is not None:
                # From a status to itself is a reason given for where it already stood.
                if changed.from_status == changed.to_status:
                    said = _("Reason recorded.")
                elif moved_on_the_board(request):
                    # A card moved into a folded column is gone from the screen, and one
                    # dropped on a column at the far end may be off it: where it went is
                    # the thing to say (#315).
                    said = _("Moved to %(status)s.") % {"status": Status(changed.to_status).label}
                else:
                    said = _("Status updated.")
                messages.success(request, said)
        else:
            messages.error(request, _("That is not a status Postulo recognises."))

        if wants_a_detail_fragment(request):
            return detail_fragments(request, application, target="status-card")
        if request.htmx:
            application.refresh_from_db()
            return render(
                request, "applications/partials/application_row.html", {"application": application}
            )
        return redirect(safe_next(request, application.get_absolute_url()))


def moved_on_the_board(request) -> bool:
    """Whether a status was changed from a card on the board.

    The same view serves the application's own page and the dashboard, and a card's form
    posts nothing that says *board*. What it does post is where to go back to. So a move
    was made on the board when that is the Applications page drawn as the board: by the
    address (`view=board`), or, with no shape in the address, by the person's own choice
    of shape -- the reading `ApplicationListView.shape` makes of the same two things.
    """
    back = urlsplit(safe_next(request, ""))
    if back.path != reverse("applications:list"):
        return False
    asked = QueryDict(back.query).get("view", "")
    if asked in ApplicationsTable.shapes:
        return asked == "board"
    table = ApplicationsTable(request, tables.settings_for(request.user, ApplicationsTable.name))
    return table.shape == "board"


#: What the detail page's forms name as their swap target. The list page posts to the
#: same status view and wants a table row, so the two are told apart by what they asked
#: for rather than by a flag the caller has to remember to set (#257).
DETAIL_TARGETS = ("status-card", "event-form", "reminders")


def wants_a_detail_fragment(request) -> bool:
    return bool(request.htmx) and request.htmx.target in DETAIL_TARGETS


def detail_fragments(request, application, *, target: str, event_form=None):
    """One region to swap, plus the timeline out of band where the timeline moved.

    Two regions from one request rather than two requests. A status change and an added
    entry both write to the timeline, and the timeline is not where either button is --
    so it comes back marked `hx-swap-oob`, which htmx places by id.

    Rendered from fresh reads rather than from what the caller has in hand: `record_event`
    and `change_status` both write, and a queryset evaluated before them would draw the
    page as it was a moment ago.
    """
    from .forms import EventForm, StatusChangeForm

    application.refresh_from_db()
    pieces = {
        "status-card": "applications/partials/status_card.html",
        "event-form": "applications/partials/event_form.html",
        "reminders": "applications/partials/reminders.html",
    }
    context = {
        "application": application,
        "status_form": StatusChangeForm(initial={"status": application.status}),
        # Asked afresh, for the reason the rest of this is: the card says how the
        # application ended, and the entry that says so was written a moment ago (#239).
        "ending": endings.of(application, afresh=True),
        "event_form": event_form if event_form is not None else EventForm(),
        "reminders": application.reminders.filter(done_at__isnull=True),
    }
    html = render_to_string(pieces[target], context, request=request)
    if target in ("status-card", "event-form"):
        html += render_to_string(
            "applications/partials/timeline.html",
            {
                "application": application,
                "events": application.events.all(),
                # Both parts of the history, or the swap would draw the timeline without
                # the listing's half until the next full page (#270).
                "listing_events": history_of(application.posting),
                "oob": True,
            },
            request=request,
        )
    return HttpResponse(html)


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
            if wants_a_detail_fragment(request):
                # An empty form, because the card it replaces is where the next entry is
                # typed; the entry just added arrives in the timeline beside it.
                return detail_fragments(request, application, target="event-form")
        else:
            messages.error(request, _("That entry could not be saved."))
            if wants_a_detail_fragment(request):
                # The same card with the errors in it. A swap that answered 422 would be
                # discarded by htmx and the person would see nothing at all.
                return detail_fragments(request, application, target="event-form", event_form=form)
        return redirect(application.get_absolute_url())


# ------------------------------------------------------------------- reminders


class ReminderListView(RedirectView):
    """The reminders page's old address. Reminders live in the calendar now (#316): the
    list was everything not yet done, soonest first, and that is the agenda narrowed to
    reminders, which opens with what is overdue. A bookmark still lands.

    Temporary rather than permanent while the calendar's shape is young: a browser keeps a
    permanent redirect for good, and this address may yet mean something again.

    What the address carried comes along, except `show`: the list hid what was done unless
    asked, and the calendar always draws a done reminder, struck through on its day.
    """

    permanent = False

    def get_redirect_url(self, *args, **kwargs) -> str:
        query = self.request.GET.copy()
        query.pop("show", None)
        query["view"] = "agenda"
        query["kinds"] = agenda.REMINDER
        return f"{reverse('applications:calendar')}?{query.urlencode()}"


#: The hour a reminder made for a day starts at, when the day is all that was chosen: the
#: start of a working morning, rather than a midnight that would announce it in the night.
REMINDER_HOUR = 9


class ReminderFormMixin:
    """Where the reminder form goes afterwards, and where *Cancel* goes: back to the page it
    was opened from when that page said so (`next`), and otherwise to the application it is
    about, or to the reminders on the calendar (#316)."""

    def fallback_url(self) -> str:
        reminder = getattr(self, "object", None)
        application_id = getattr(reminder, "application_id", None) or self.asked_application()
        if application_id:
            return reverse("applications:detail", args=[application_id])
        return agenda.reminders_address()

    def asked_application(self):
        return None

    def get_success_url(self) -> str:
        return safe_next(self.request, self.fallback_url())

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = safe_next(self.request, self.fallback_url())
        return context


class ReminderCreateView(
    ReminderFormMixin, OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView
):
    """A new reminder: from the calendar, where the day clicked is its day (#316), from an
    application's page, where the application is its application, or from nowhere."""

    model = Reminder
    form_class = ReminderForm
    template_name = "applications/reminder_form.html"

    def asked_application(self):
        application_id = as_pk(self.request.GET.get("application"))
        if (
            application_id
            and Application.objects.for_user(self.request.user).filter(pk=application_id).exists()
        ):
            return application_id
        return None

    def get_initial(self) -> dict:
        initial = super().get_initial()
        application_id = self.asked_application()
        if application_id:
            initial["application"] = application_id
        # The day a calendar cell was for, read the way the calendar reads its own days: a
        # date or nothing, so an address somebody typed wrongly opens an empty form rather
        # than an error. Naive on purpose -- the field shows it as it is, in the person's
        # own zone, which is the zone it is read back in.
        day = agenda.day_from(self.request.GET.get("on", ""))
        if day is not None:
            initial["due_at"] = datetime(day.year, day.month, day.day, REMINDER_HOUR)
        return initial


class ReminderCompleteView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Reminder.objects.for_user(self.request.user)

    def post(self, request, pk: int) -> HttpResponse:
        reminder = get_object_or_404(self.get_queryset(), pk=pk)
        reminder.complete()
        messages.success(request, _("Marked as done."))
        if wants_a_detail_fragment(request) and reminder.application_id:
            return detail_fragments(request, reminder.application, target="reminders")
        return redirect(safe_next(request, agenda.reminders_address()))


def to_the_minute(when):
    return when.replace(second=0, microsecond=0)


class ReminderUpdateView(ReminderFormMixin, OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    """Change what a reminder says or when it falls due (#238).

    There was no way to. A reminder could be made and it could be ticked off, so a due time
    typed wrongly -- half past nine in the evening for half past nine in the morning -- could
    only be ticked off and written again, losing the fact that it had ever been set.

    The stamp goes with the time, through the same service *Later* uses: a reminder moved
    into the future has not been announced yet, whatever the column says.
    """

    model = Reminder
    form_class = ReminderForm
    template_name = "applications/reminder_form.html"

    def get_object(self, queryset=None):
        # Read before the form binds: validation writes the posted values onto the instance.
        reminder = super().get_object(queryset)
        self.due_before = reminder.due_at
        return reminder

    def form_valid(self, form):
        # The form shows and posts the minute; a time set by *Later* or *Snooze* once kept
        # its seconds, so only a difference at the minute is a move (#445).
        moved = to_the_minute(form.cleaned_data["due_at"]) != to_the_minute(self.due_before)
        response = super().form_valid(form)
        if moved:
            postpone_reminder(self.object, self.object.due_at)
        messages.success(self.request, _("Reminder updated."))
        return response


class ReminderDeleteView(ReminderFormMixin, ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    """Deleting a reminder, confirmed first. Back where it was asked from when that page
    said so -- the calendar, on the day and in the shape it was showing (#316) -- and
    otherwise to its application or to the reminders, and *Cancel* the same way."""

    model = Reminder
    template_name = "partials/confirm_delete.html"

    def get_cancel_url(self) -> str:
        return self.get_success_url()


class ReminderLaterView(OwnedObjectMixin, View):
    """Put a reminder off: tomorrow, next week, or a day the person names (#238).

    The only postponement Postulo had was *Snooze* on a quiet application, which makes a
    *new* reminder every time it is pressed -- so pressing it twice left two. This moves the
    one that is there.

    A `when` of `tomorrow` or `next_week` needs no date; anything else reads `due_at`, which
    is the same field name and the same format the form uses, so the date control on the row
    posts here without a form class of its own.
    """

    def get_queryset(self):
        return Reminder.objects.for_user(self.request.user).select_related(
            "application", "application__posting"
        )

    def post(self, request, pk: int) -> HttpResponse:
        reminder = get_object_or_404(self.get_queryset(), pk=pk)
        due = later_time(request.POST.get("when", ""), reminder) or _a_named_day(
            request.POST.get("due_at")
        )
        if due is None:
            messages.error(request, _("That is not a time Postulo can read."))
        elif reminder.is_done:
            messages.error(request, _("It is already done."))
        else:
            postpone_reminder(reminder, due)
            messages.success(
                request,
                _("Put off until %(when)s.")
                % {"when": formats.date_format(timezone.localtime(due), "DATETIME_FORMAT")},
            )
        if wants_a_detail_fragment(request) and reminder.application_id:
            return detail_fragments(request, reminder.application, target="reminders")
        return redirect(safe_next(request, agenda.reminders_address()))


def _a_named_day(raw: str | None):
    """A date or a moment the person typed, made aware in their own zone.

    A bare date means the start of that day, which is what a date control offers and what
    somebody choosing one means. Anything unreadable is `None` and the view says so rather
    than moving the reminder somewhere nobody asked for.
    """
    for shape in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime((raw or "").strip(), shape)
        except ValueError:
            continue
        return timezone.make_aware(naive) if timezone.is_naive(naive) else naive
    return None


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


class CalendarView(LoginRequiredMixin, View):
    """The dated things of a search, by day: a month by default, or a week, a day, an
    agenda (#204). Everything is in the address -- `?month=2026-09`, or `?view=week&on=`
    -- so a period is bookmarkable and the page needs no script. `agenda` builds it."""

    template_name = "applications/calendar.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        page = agenda.page_for(request.GET, request.user)
        return render(request, self.template_name, {"page": page})


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


#: How far ahead the feed carries deadlines and closing dates. The interviews in it are
#: *everything* still ahead, because a diary is short; deadlines and closings are not, and a
#: year of them in somebody's calendar application is a year of clutter they did not ask
#: for. Half a year is past any notice period worth planning around (#238).
FEED_DAYS = 183


class InterviewCalendarView(OwnedObjectMixin, View):
    """An .ics file: one interview, or everything still ahead.

    The whole-diary feed carries the deadlines and the closing dates too, as whole days
    (#238). They were stored and never left Postulo — so somebody who subscribed to the feed
    to stop missing interviews still had to remember the two dates that actually close.

    The single-interview file does not, and should not: it is a meeting somebody forwards or
    imports once, and their own deadlines are nothing to do with it.

    The address still says `interviews`. Renaming it would break every subscription already
    in somebody's calendar, which is exactly the thing a feed must not do.
    """

    def get_queryset(self):
        return Interview.objects.for_user(self.request.user).with_display_data()

    def get(self, request, pk: int | None = None) -> HttpResponse:
        days: list[ical.DayEntry] = []
        if pk is not None:
            interviews = [get_object_or_404(self.get_queryset(), pk=pk)]
            filename = f"interview-{pk}.ics"
        else:
            interviews = list(self.get_queryset().upcoming())
            filename = "interviews.ics"
            days = self.dated_days(request)
        text = ical.calendar(
            interviews,
            url_for=lambda i: request.build_absolute_uri(i.application.get_absolute_url()),
            days=days,
        )
        response = HttpResponse(text, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def dated_days(self, request) -> list[ical.DayEntry]:
        """The deadlines and closing dates ahead, as whole days.

        Read through `agenda.events_between`, which is the one place that decides what a
        deadline is, which ones are over and what they are called -- so the feed and the
        calendar page can never disagree about somebody's month.
        """
        today = timezone.localdate()
        events = agenda.events_between(
            request.user,
            today,
            today + timedelta(days=FEED_DAYS),
            kinds=(agenda.DEADLINE, agenda.CLOSING, agenda.ANSWER),
        )
        return [
            ical.DayEntry(
                summary=event.title,
                day=event.day,
                url=request.build_absolute_uri(event.url),
                description=event.detail,
                over=event.muted,
            )
            for event in events
        ]


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


class TagDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
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


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ReportPDFView(LoginRequiredMixin, View):
    """The report as a document to hand over.

    It carries the name, the period and the day it was produced, because a document with no
    date is not evidence of anything.

    **Outside a transaction of its own (#220).** Building the report reads the whole record
    and then a renderer draws it, and under `ATOMIC_REQUESTS` on SQLite that held the write
    lock for every second of it — on a GET that writes nothing at all. The GET still writes
    nothing; the POST's one write is the snapshot row, in the short transaction `rendering`
    puts around it.

    **Pressing the button files it; opening the address does not.** The button on the page
    posts, and the PDF it hands back is also filed under Sent documents as a *report* -- a
    document handed to an employment office is exactly the document somebody wants kept,
    and a store copies it the way it copies a CV (#162). A GET is the same PDF and no
    record: a bookmarked address, a link in a message, a crawler that follows one, must
    never leave a document behind. The moment of freezing is a deliberate press.
    """

    def _html(self, request: HttpRequest) -> tuple:
        report = reports.build(request.user, reports.period_from(request.GET))
        language = languages.current()
        html = render(
            request,
            "applications/report_print.html",
            {
                "report": report,
                "document_language": language,
                "document_direction": languages.direction(language),
            },
        ).content
        return report, html.decode()

    def _back_to_the_page(self, request: HttpRequest, report, unavailable) -> HttpResponse:
        messages.error(request, str(unavailable))
        return redirect(
            f"{reverse('applications:report')}?{urlencode(reports.as_query(report.period))}"
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        from postulo.documents.pdf import PDFBackendUnavailable, draft_pdf

        report, html = self._html(request)
        try:
            # A draft: handed over and filed nowhere, so the same report asked for twice is
            # the same bytes drawn twice, and the second may have the first's (#220).
            pdf = draft_pdf(html)
        except PDFBackendUnavailable as unavailable:
            return self._back_to_the_page(request, report, unavailable)
        response = HttpResponse(pdf, content_type="application/pdf")
        name = reports.filename(report, "pdf")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    def post(self, request: HttpRequest) -> HttpResponse:
        from postulo.core import errands

        # Building the report reads the whole record and then a renderer draws it, so the
        # press sends the work off and lands on the page that watches it (#247). What the
        # press *means* has not changed: it files the report under Sent documents, where
        # the finished errand points.
        errand = errands.send("report_pdf", request.user, query=dict(request.GET.items()))
        return redirect("core:errand", pk=errand.pk)


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
        # Only the two words are answers; anything else is not a route (#447).
        if action not in ("accept", "decline"):
            raise Http404
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
            chosen = as_pk(chosen)
            application = get_object_or_404(Application.objects.for_user(request.user), pk=chosen)
        if application is None:
            messages.error(request, _("Choose which application this is about first."))
            return redirect(safe_next(request, fallback))

        suggestions.accept(suggestion, application=application)
        messages.success(request, _("Added to the timeline."))
        return redirect(safe_next(request, fallback))


# ---------------------------------------------------------------------- offers


class OfferCreateView(OwnedObjectMixin, View):
    """Record what one application was offered (#237)."""

    template_name = "applications/offer_form.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user).select_related(
            "posting", "posting__company"
        )

    def get(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        # The advertised range is the obvious starting point, and often not the answer.
        posting = application.posting
        initial = {"currency": posting.salary_currency or "", "period": posting.salary_period}
        if posting.salary_max is not None:
            initial["base_amount"] = posting.salary_max
        elif posting.salary_min is not None:
            initial["base_amount"] = posting.salary_min
        form = OfferForm(user=request.user, initial=initial)
        return render(request, self.template_name, {"form": form, "application": application})

    def post(self, request, pk: int) -> HttpResponse:
        application = get_object_or_404(self.get_queryset(), pk=pk)
        form = OfferForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "application": application})
        record_offer(application, **form.cleaned_data)
        messages.success(request, _("Offer recorded."))
        return redirect(f"{application.get_absolute_url()}#offers")


class OfferUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Offer
    form_class = OfferForm
    template_name = "applications/offer_form.html"

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
        offer = form.save()
        # The revision is written on the timeline and the reminder follows the date, which
        # is the service's business and not the form's.
        revise_offer(offer)
        messages.success(self.request, _("Offer updated."))
        return redirect(f"{offer.application.get_absolute_url()}#offers")


class OfferDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Offer
    template_name = "partials/confirm_delete.html"

    def get_queryset(self):
        return super().get_queryset().select_related("application", "reminder")

    def get_success_url(self) -> str:
        return f"{self.object.application.get_absolute_url()}#offers"

    def form_valid(self, form):
        success_url = self.get_success_url()
        withdraw_offer(self.object)
        return HttpResponseRedirect(success_url)

    def get_cancel_url(self) -> str:
        return self.get_success_url()


class OfferCompareView(LoginRequiredMixin, View):
    """Every application at *Offer*, side by side, and nothing decided for anybody (#237).

    The latest offer of each application that stands at *Offer* now. One column per offer,
    one row per term, the base pay brought to a year within its currency so that a monthly
    figure and an annual one can be read against each other. No score: the person decides.
    """

    template_name = "applications/offer_compare.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        offers = (
            Offer.objects.for_user(request.user)
            .filter(application__status=Status.OFFER)
            .select_related("application", "application__posting", "application__posting__company")
            .order_by("application_id", "-created_at", "-pk")
        )
        latest: dict[int, Offer] = {}
        for offer in offers:
            latest.setdefault(offer.application_id, offer)
        columns = sorted(latest.values(), key=lambda o: o.application.posting.company.name)
        return render(
            request,
            self.template_name,
            {"columns": columns, "rows": offer_rows(columns)},
        )


def offer_rows(offers: list) -> list[tuple[str, list[tuple[str, str]]]]:
    """(term, [(whose, cell) per offer]), in the order a person reads an offer.

    Built here rather than in the template, so that the same rows reach the browser test,
    the phone layout and anything that later wants a CSV of the comparison. Each cell
    carries the employer's name because on a phone the header row is gone and the cell has
    to say whose it is (`table-cards`, `data-label`).
    """
    from django.utils.formats import number_format

    def yearly(offer) -> str:
        amount = offer.yearly_amount
        if amount is None:
            return ""
        figure = number_format(amount.normalize(), use_l10n=True, force_grouping=True)
        return str(
            _("%(figure)s %(currency)s a year") % {"figure": figure, "currency": offer.currency}
        )

    def day(value) -> str:
        return formats.date_format(value, "DATE_FORMAT") if value else ""

    def whose(offer) -> str:
        return str(offer.application.posting.company.name)

    def row(term, value) -> tuple[str, list[tuple[str, str]]]:
        return (str(term), [(whose(offer), value(offer)) for offer in offers])

    return [
        row(_("Base pay"), lambda o: o.terms),
        row(_("Brought to a year"), yearly),
        row(_("Variable pay"), lambda o: o.variable_pay),
        row(_("Equity"), lambda o: o.equity),
        row(_("Benefits"), lambda o: o.benefits),
        row(_("Where you would work"), lambda o: o.location),
        row(
            _("Days of holiday a year"), lambda o: str(o.holidays) if o.holidays is not None else ""
        ),
        row(_("Start date"), lambda o: day(o.starts_on)),
        row(_("Answer by"), lambda o: day(o.answer_by)),
        row(_("Notes"), lambda o: o.notes),
    ]
