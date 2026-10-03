"""Views for companies, contacts and postings."""

from __future__ import annotations

from functools import cached_property
from pathlib import PurePosixPath

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
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

from postulo.core import tables
from postulo.core.cells import EditableCellView
from postulo.core.files import serve_private_file
from postulo.core.mixins import (
    ConfirmDeleteMixin,
    GdprNoticeMixin,
    OwnedObjectMixin,
    OwnerFormMixin,
    PageOrFragmentMixin,
    PhoneNumbersMixin,
    WebLinksMixin,
)
from postulo.core.redirects import safe_next
from postulo.core.search import clean_query

from . import duplicates, identifiers, logos, merging
from .forms import CompanyForm, CompanyIdentifierFormSet, ContactForm, IndustryForm, JobPostingForm
from .models import Company, Contact, DiscardReason, Industry, JobPosting
from .tables import CompaniesTable


class UserFormKwargsMixin:
    """Hand the signed-in user to the form, so it can scope its own choices."""

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


# ------------------------------------------------------------------- companies


class CompanyListView(PageOrFragmentMixin, OwnedObjectMixin, ListView):
    """The table of employers: sortable, narrowable, and laid out as the person likes."""

    model = Company
    template_name = "jobs/company_list.html"
    context_object_name = "companies"

    @cached_property
    def table(self) -> CompaniesTable:
        return CompaniesTable(
            self.request, tables.settings_for(self.request.user, CompaniesTable.name)
        )

    def get_paginate_by(self, queryset) -> int:
        return self.table.page_size

    def get(self, request, *args, **kwargs):
        # A bare address opens as the person's default view, when they have kept one (#259).
        # Only a bare one: anything with a parameter is a question they asked. Not for an
        # htmx swap either -- a swap is the page updating itself, not somebody arriving.
        opening = self.table.opening_url
        if opening and not getattr(request, "htmx", None):
            from django.shortcuts import redirect

            return redirect(opening)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        # The table's ordering always ends in the key, so pagination over the aggregated
        # rows never repeats or skips one between pages.
        queryset = (
            super()
            .get_queryset()
            .with_table_data()
            .select_related("parent")
            .prefetch_related("industries", "identifiers")
        )
        # Through `clean_query`, as the search over everything reads its own: the words
        # with their spaces tidied, and no more than two hundred characters of them. An
        # address is not a form, anything can be typed into it, and SQLite refuses a
        # pattern of sixty thousand characters with an error, which was a 500 (#313).
        search = clean_query(self.request.GET.get("q", ""))
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(location__icontains=search)
                | Q(industries__name__icontains=search)
                | Q(identifiers__value__icontains=search)
            )
        queryset = self._within_group(queryset)
        # A company in two matching industries is still one row.
        return self.table.apply(queryset).distinct()

    def _within_group(self, queryset):
        """Narrow to one ownership tree, when `?group=` names a company in one.

        A walk rather than a join: an ownership chain is arbitrarily deep and the depth cap
        is ten, so expressing "anywhere in this group" as a lookup would be ten outer joins
        on every row of every page. Two small queries instead, and the answer is exact.

        An unknown name narrows to nothing rather than to everything, because a filter that
        silently does not apply is worse than one that shows an empty page.
        """
        from . import structure

        wanted = self.request.GET.get("group", "").strip()
        if not wanted or not structure.structure_allowed(self.request.user):
            return queryset
        tops = Company.objects.for_user(self.request.user).filter(name__iexact=wanted)
        members: set[int] = set()
        for top in tops.select_related("parent"):
            members.update(member.pk for member in top.group_members())
        return queryset.filter(pk__in=members)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("q", "")
        context["table"] = self.table
        context["page_sizes"] = tables.PAGE_SIZES
        # The bar is offered only where there is something to apply: no fields of activity
        # means no additive action, and an action bar with an empty select is a promise the
        # page cannot keep (#134).
        context["bulk_industries"] = Industry.objects.for_user(self.request.user)
        context["group"] = self.request.GET.get("group", "").strip()
        return context


class CompanyMapView(LoginRequiredMixin, TemplateView):
    """The places the person's companies are in, and the map that draws them (#108).

    The list is the page and the map is beside it: a map is the classic
    screen-reader failure, so every place, every company and every count is text
    before any of it is drawn, and the page works with scripts off — the drawing
    is an SVG the server writes, not a script that fetches tiles, and a tile
    request would tell a tile server where somebody is applying.

    A place is a company's location text as recorded; its dot, when one is
    drawn, is the coordinate one of the companies there carries, and its size is
    the number of companies at the place — the question the map answers is where
    the search is spread, not where any single office sits.
    """

    template_name = "jobs/company_map.html"

    def get_context_data(self, **kwargs) -> dict:
        from . import places as places_data

        context = super().get_context_data(**kwargs)
        places: dict[str, dict] = {}
        rows = (
            Company.objects.for_user(self.request.user)
            .filter(location__gt="")
            .values_list("location", "name", "pk", "location_lat", "location_lon")
            .order_by("location", "name")
        )
        for location, name, pk, lat, lon in rows:
            entry = places.setdefault(
                location,
                {"label": location, "count": 0, "companies": [], "lat": None, "lon": None},
            )
            entry["count"] += 1
            entry["companies"].append((name, reverse("jobs:company_detail", args=[pk])))
            if entry["lat"] is None and lat is not None:
                entry["lat"] = lat
                entry["lon"] = lon
        for entry in places.values():
            # The equirectangular projection the outline is drawn in: a degree of
            # longitude is a unit of the width, a degree of latitude one of the
            # height, so the dot sits on the coast the data puts it on.
            if entry["lat"] is not None:
                entry["cx"] = entry["lon"] + 180.0
                entry["cy"] = 90.0 - entry["lat"]
                entry["radius"] = min(1.5 + 1.8 * entry["count"] ** 0.5, 9.0)
            else:
                entry["cx"] = entry["cy"] = entry["radius"] = None
        context["places"] = sorted(
            places.values(), key=lambda entry: (-entry["count"], entry["label"].casefold())
        )
        context["dataset_available"] = places_data.available()
        return context


class CompanyCellView(LoginRequiredMixin, EditableCellView):
    """One cell of the companies table, changed where it sits (#135).

    Three attributes and the machinery does the rest, which is the measure of whether the
    second table to want this is a view or a paragraph.
    """

    model = Company
    form_class = CompanyForm
    columns = CompaniesTable.columns
    form_url_name = "jobs:company_update"


class CompanyBulkView(LoginRequiredMixin, View):
    """Put several companies in a field of activity at once (#134).

    Additive only, and more pointedly here than for applications: deleting a company cascades
    to every posting under it, which the interface says plainly when it is one company and
    would be saying about an unseen number when it is forty.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from django.contrib import messages

        from postulo.core import bulk

        rows = bulk.chosen_rows(request, Company)
        if not rows.exists():
            messages.info(request, bulk.nothing_chosen())
            return redirect(self._back(request))

        if request.POST.get(bulk.ACTION, "") != "industry":
            messages.error(request, _("That is not something Postulo can do to several at once."))
            return redirect(self._back(request))

        count = self._industry(request, rows)
        messages.success(request, bulk.changed(count, CompaniesTable.noun))
        return redirect(self._back(request))

    def _industry(self, request: HttpRequest, rows) -> int:
        try:
            wanted = int(request.POST.get("industry") or 0)
        except (TypeError, ValueError):
            return 0
        from postulo.core import bulk

        industry = Industry.objects.for_user(request.user).filter(pk=wanted).first()
        if industry is None:
            return 0
        return bulk.link_all(list(rows), "industries", industry)

    @staticmethod
    def _back(request: HttpRequest) -> str:
        from postulo.core.redirects import safe_next

        return safe_next(request, reverse("jobs:company_list"))


class CompanyDetailView(OwnedObjectMixin, DetailView):
    model = Company
    template_name = "jobs/company_detail.html"
    context_object_name = "company"

    def get_queryset(self):
        # `parent` is named in the header, so it is fetched with the company rather than
        # by a second query while rendering.
        return (
            super()
            .get_queryset()
            .select_related("parent")
            .prefetch_related("industries", "identifiers")
        )

    def get_context_data(self, **kwargs) -> dict:
        from postulo.core import phone_numbers, web_links

        from . import structure

        context = super().get_context_data(**kwargs)
        person = self.request.user
        # The data-subject export beside each contact, while the feature offers it (#297).
        from postulo.core import gdpr

        context["gdpr_offered"] = gdpr.is_offered(person)
        # A hierarchy that silently keeps counting leaves is a hierarchy that changes
        # nothing, so the page asks which reading it is showing -- and defaults to the one
        # every figure in Postulo has always meant, this company alone (#138).
        across = self.request.GET.get("across", "")
        counted = structure.companies_counted(self.object, person, across=across)
        context["across"] = across
        context["across_group"] = structure.ACROSS_GROUP
        context["showing_group"] = len(counted) > 1
        context["in_a_group"] = structure.in_a_group(self.object, person)
        context["group"] = structure.group_of(self.object, person)
        context["parent"] = structure.parent_of(self.object, person)
        context["children"] = structure.children_of(self.object, person)
        context["contacts"] = self.object.contacts.select_related("department").prefetch_related(
            "phone_numbers", "web_links"
        )
        context["postings"] = (
            JobPosting.objects.for_user(person)
            .filter(company__in=counted)
            .select_related("company")
            .prefetch_related("applications")
        )
        # Decided once for the page rather than per contact: it is one answer about one
        # person, and asking it per row would be a query per row for the same answer.
        context["several_numbers"] = phone_numbers.several_allowed(person)
        # The kinds of link this person is offered several of; a contact's other rows of
        # a kind that is off stay unlisted, exactly as the numbers do (#189).
        context["several_links"] = web_links.offered_kinds(person)
        context["structure_on"] = structure.structure_allowed(person)
        # Worked out as the page is drawn, from the record as it is now (#239): which of
        # this person's other companies look like this one, and which of the people here
        # look like somebody recorded elsewhere. Told, and nothing done about it.
        merge_url = reverse("jobs:company_merge", args=[self.object.pk])
        context["duplicates"] = [
            (candidate, f"{merge_url}?with={candidate.record.pk}")
            for candidate in duplicates.for_company(self.object)
        ]
        context["people_alike"] = duplicates.contacts_with_any(person, context["contacts"])
        return context


class CompanyIdentifiersMixin:
    """The identifiers block on the company form: an inline formset saved with the company.

    The formset validates against the person's other companies, so it needs the user
    before the company exists; on create it is bound to the unsaved instance and told
    the owner explicitly.
    """

    def get_identifiers(self) -> CompanyIdentifierFormSet:
        instance = getattr(self, "object", None) or Company(owner=self.request.user)
        kwargs = {"instance": instance, "prefix": "identifiers"}
        # A form posted without the block (an older client, a script) means no change to
        # the identifiers, not an error about a missing management form.
        if self.request.method == "POST" and "identifiers-TOTAL_FORMS" in self.request.POST:
            kwargs["data"] = self.request.POST
        formset = CompanyIdentifierFormSet(**kwargs)
        formset.user = self.request.user
        return formset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.setdefault("identifiers", self.get_identifiers())
        context["identifier_schemes"] = identifiers.schemes().values()
        return context

    def refuse_bad_identifiers(self, form):
        """The page again, if an identifier row is wrong; None when the save may go on.

        Asked first by the outermost ``form_valid``, so that the success message and the
        logo fields are reached only when the company really is saved.
        """
        formset = self.get_identifiers()
        if formset.is_bound and not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, identifiers=formset))
        return None

    def form_valid(self, form):
        formset = self.get_identifiers()
        with transaction.atomic():
            response = super().form_valid(form)
            if formset.is_bound:
                formset.instance = self.object
                for row in formset.forms:
                    row.instance.company = self.object
                formset.save()
        return response


class LogoFormMixin:
    """Whatever the logo fields asked for, done after the company is saved.

    After, because a logo needs the company's primary key to be filed under; and never
    fatally, because a picture that would not come is no reason to lose everything else
    the person typed. The problem is shown and the company is saved.
    """

    def form_valid(self, form):
        response = super().form_valid(form)
        problem = form.apply_logo(self.object)
        if problem:
            messages.warning(
                self.request, _("The logo was not changed: %(problem)s") % {"problem": problem}
            )
        return response


class CompanyCreateView(
    LogoFormMixin,
    OwnedObjectMixin,
    UserFormKwargsMixin,
    OwnerFormMixin,
    CompanyIdentifiersMixin,
    CreateView,
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"

    def form_valid(self, form):
        if (refused := self.refuse_bad_identifiers(form)) is not None:
            return refused
        messages.success(self.request, _("Company added."))
        return super().form_valid(form)


class CompanyUpdateView(
    LogoFormMixin, OwnedObjectMixin, UserFormKwargsMixin, CompanyIdentifiersMixin, UpdateView
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"

    def form_valid(self, form):
        if (refused := self.refuse_bad_identifiers(form)) is not None:
            return refused
        messages.success(self.request, _("Company updated."))
        return super().form_valid(form)


class CompanyLogoView(OwnedObjectMixin, View):
    """Serve a company's logo — from this instance, never from anybody else's server.

    It lives under private media like every other file and comes out only through here,
    which is what lets the content security policy keep saying ``img-src 'self'``. The
    address carries the moment it was fetched, so a new logo is never hidden behind an
    old cache entry.
    """

    def get_queryset(self):
        return Company.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        company = get_object_or_404(self.get_queryset(), pk=pk)
        if not company.logo:
            raise Http404
        # The extension follows what is stored, which is no longer always PNG (#264): a
        # hardcoded name would make `serve_private_file` guess the wrong content type for
        # an SVG and serve a vector as an octet-stream.
        suffix = PurePosixPath(company.logo.name).suffix or ".png"
        response = serve_private_file(request, company.logo, download_name=f"logo{suffix}")
        response["Cache-Control"] = "private, max-age=86400"
        return response


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CompanyLogoActionView(OwnedObjectMixin, View):
    """*Find logo* and *Refresh*: one request each, when a person presses the button.

    **Outside a transaction of its own (#220).** *Find logo* reads the company's site and
    then tries up to six images from it, one network round trip each, and under
    `ATOMIC_REQUESTS` on SQLite every one of those seconds was a second nothing else could
    write. What has to be atomic is putting the picture on the company, and `logos.store`
    and `logos.clear` each do that in a transaction of one statement.
    """

    def get_queryset(self):
        return Company.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int, action: str) -> HttpResponse:
        from postulo.core import errands

        company = get_object_or_404(self.get_queryset(), pk=pk)
        # Removing a logo is a database write and nothing else, so it stays here: sending
        # an instant act off to a worker would be a spinner shown for the sake of symmetry.
        if action == "remove":
            logos.clear(company)
            messages.success(request, _("The logo is gone."))
            return redirect(safe_next(request, company.get_absolute_url()))
        if action not in ("website", "refresh"):
            raise Http404
        # The other two read the company's site and then try up to six images, one round
        # trip each, which is not a thing to make somebody sit through (#247).
        errand = errands.send(
            "logo", request.user, subject=company, company_id=company.pk, action=action
        )
        return redirect("core:errand", pk=errand.pk)


class MergeView(LoginRequiredMixin, View):
    """Two records of one thing made into one, behind a page that says what will move (#239).

    One address and two states of it. Bare, it asks *which one is the same as this?* --
    the records `jobs.duplicates` noticed first, then a list of all the others, because
    the person knows things the comparison does not. With ``?with=`` it is the
    confirmation: what moves, what the kept record takes, where the two differ and what
    becomes of the difference. Both are a GET and change nothing.

    **The merge is a POST from the confirmation and from nowhere else.** A link cannot
    merge anything: a page that was only opened, a bookmark, a crawler following addresses
    must never be able to delete a record.

    **Both records are looked up among the person's own**, the one in the address and the
    one in the query alike, so somebody else's is a 404 and never a 403 -- and never a
    merge of one person's company into another's.

    The record in the address is the one that is kept. *Keep the other instead* is the
    same page with the two exchanged, which is a link and needs no script.
    """

    model = None
    template_name = "jobs/merge.html"
    #: `company` or `contact`: which words the page uses, and which address it is at.
    kind = ""
    url_name = ""

    def records(self):
        return self.model.objects.for_user(self.request.user)

    def plan(self, kept, other):
        raise NotImplementedError

    def merge(self, kept, other):
        raise NotImplementedError

    def alike(self, kept) -> list:
        raise NotImplementedError

    def after(self, kept) -> str:
        """Where the page leads once it is done, and where *Cancel* goes."""
        raise NotImplementedError

    def address(self, kept, other=None) -> str:
        url = reverse(self.url_name, args=[kept.pk])
        return f"{url}?with={other.pk}" if other is not None else url

    def other_or_404(self, kept, raw):
        """The record named beside the kept one: another of this person's, or a 404."""
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            raise Http404 from None
        return get_object_or_404(self.records().exclude(pk=kept.pk), pk=pk)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        kept = get_object_or_404(self.records(), pk=pk)
        context = {"kind": self.kind, "kept": kept, "cancel_url": self.after(kept)}
        named = request.GET.get("with", "")
        if named:
            other = self.other_or_404(kept, named)
            context["other"] = other
            context["plan"] = self.plan(kept, other)
            context["swap_url"] = self.address(other, kept)
        else:
            context["candidates"] = [
                (candidate, self.address(kept, candidate.record)) for candidate in self.alike(kept)
            ]
            context["choices"] = self.records().exclude(pk=kept.pk)
            context["merge_url"] = self.address(kept)
        return render(request, self.template_name, context)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        kept = get_object_or_404(self.records(), pk=pk)
        other = self.other_or_404(kept, request.POST.get("with", ""))
        try:
            plan = self.merge(kept, other)
        except merging.CannotMerge as refusal:
            # Undone whole by the time it is caught. The sentence is the merge's own.
            messages.error(request, refusal.why)
            return redirect(self.address(kept, other))
        messages.success(request, merging.done(plan))
        return redirect(self.after(kept))


class CompanyMergeView(MergeView):
    model = Company
    kind = "company"
    url_name = "jobs:company_merge"

    def plan(self, kept, other):
        return merging.plan_companies(kept, other)

    def merge(self, kept, other):
        return merging.merge_companies(kept, other)

    def alike(self, kept) -> list:
        return duplicates.for_company(kept)

    def after(self, kept) -> str:
        return kept.get_absolute_url()


class CompanyDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Company
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

    def get_context_data(self, **kwargs):
        """Count what goes with the company (#217).

        A company cascades into its postings and those into applications, their timelines,
        interviews and reminders — a year of a job search, behind one button that used to say
        only "anything belonging to it goes too".
        """
        from django.utils.translation import ngettext

        from postulo.applications.models import Application
        from postulo.documents.models import RenderedDocument

        context = super().get_context_data(**kwargs)
        company = self.object
        applications = Application.objects.filter(posting__company=company)
        postings = company.postings.count()
        applied = applications.count()
        contacts = company.contacts.count()
        # Each ngettext carries its two texts as literals: the extractor reads call sites.
        context["consequences"] = [
            text % {"count": number}
            for number, text in (
                (postings, ngettext("%(count)d posting", "%(count)d postings", postings)),
                (applied, ngettext("%(count)d application", "%(count)d applications", applied)),
                (contacts, ngettext("%(count)d contact", "%(count)d contacts", contacts)),
            )
            if number
        ]
        sent = RenderedDocument.objects.filter(application__in=applications).count()
        if sent:
            context["kept"] = ngettext(
                "The %(count)d document you sent is kept, and still says where it went.",
                "The %(count)d documents you sent are kept, and still say where they went.",
                sent,
            ) % {"count": sent}
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Company deleted, along with its postings."))
        return super().form_valid(form)


# ------------------------------------------------------------------- industries


class IndustryListView(OwnedObjectMixin, ListView):
    """The person's vocabulary of industries, with how many companies each holds.

    **Searchable, because this list is no longer necessarily short.** A vocabulary seeded
    from a classification of the whole economy can run to dozens of words, and a page that
    can only be scrolled is a page where merging two spellings means finding both by eye
    (#141). The box narrows by name and by NACE code, server-side, like every other filter
    in Postulo.

    A row appears here only once a company has been given that industry -- the classification
    is a list of *suggestions*, not a list of rows -- so this page grows at the speed somebody
    actually uses it, which is the reason it was safe to make the suggestions long.
    """

    model = Industry
    template_name = "jobs/industry_list.html"
    context_object_name = "industries"

    def get_queryset(self):
        found = super().get_queryset().annotate(company_count=Count("companies", distinct=True))
        wanted = self.request.GET.get("q", "").strip()
        if wanted:
            found = found.filter(Q(name__icontains=wanted) | Q(code__startswith=wanted))
        return found

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        context["total"] = Industry.objects.for_user(self.request.user).count()
        return context


class IndustryCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = Industry
    form_class = IndustryForm
    template_name = "jobs/industry_form.html"
    success_url = reverse_lazy("jobs:industry_list")


class IndustryUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Industry
    form_class = IndustryForm
    template_name = "jobs/industry_form.html"
    success_url = reverse_lazy("jobs:industry_list")

    def form_valid(self, form):
        merged = form.cleaned_data.get("merge_into") is not None
        response = super().form_valid(form)
        messages.success(self.request, _("Merged.") if merged else _("Industry renamed."))
        return response


class IndustryDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Industry
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:industry_list")


# -------------------------------------------------------------------- contacts


class ContactCreateView(
    OwnedObjectMixin,
    UserFormKwargsMixin,
    OwnerFormMixin,
    GdprNoticeMixin,
    PhoneNumbersMixin,
    WebLinksMixin,
    CreateView,
):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

    def get_initial(self) -> dict:
        initial = super().get_initial()
        company_id = self.request.GET.get("company")
        if (
            company_id
            and Company.objects.for_user(self.request.user).filter(pk=company_id).exists()
        ):
            initial["company"] = company_id
        return initial

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")

    def form_valid(self, form):
        numbers = self.get_phone_numbers()
        links = self.get_web_links()
        if self.phone_numbers_invalid(numbers) or self.web_links_invalid(links):
            return self.render_to_response(
                self.get_context_data(form=form, numbers=numbers, links=links)
            )
        with transaction.atomic():
            response = super().form_valid(form)
            self.save_phone_numbers(numbers, self.object)
            self.save_web_links(links, self.object)
        messages.success(self.request, _("Contact added."))
        return response


class ContactUpdateView(
    OwnedObjectMixin,
    UserFormKwargsMixin,
    GdprNoticeMixin,
    PhoneNumbersMixin,
    WebLinksMixin,
    UpdateView,
):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        # The one page a person has, so this is where it is said that they look like
        # somebody recorded twice (#239). Worked out as the page is drawn; told, not done.
        merge_url = reverse("jobs:contact_merge", args=[self.object.pk])
        context["merge_url"] = merge_url
        context["duplicates"] = [
            (candidate, f"{merge_url}?with={candidate.record.pk}")
            for candidate in duplicates.for_contact(self.object)
        ]
        return context

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")

    def form_valid(self, form):
        numbers = self.get_phone_numbers()
        links = self.get_web_links()
        if self.phone_numbers_invalid(numbers) or self.web_links_invalid(links):
            return self.render_to_response(
                self.get_context_data(form=form, numbers=numbers, links=links)
            )
        with transaction.atomic():
            response = super().form_valid(form)
            self.save_phone_numbers(numbers, self.object)
            self.save_web_links(links, self.object)
        return response


class ContactMergeView(MergeView):
    model = Contact
    kind = "contact"
    url_name = "jobs:contact_merge"

    def records(self):
        return super().records().select_related("company", "department")

    def plan(self, kept, other):
        return merging.plan_contacts(kept, other)

    def merge(self, kept, other):
        return merging.merge_contacts(kept, other)

    def alike(self, kept) -> list:
        return duplicates.for_contact(kept)

    def after(self, kept) -> str:
        # A person has no page but the one they are edited on.
        return reverse("jobs:contact_update", args=[kept.pk])


class ContactDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Contact
    template_name = "partials/confirm_delete.html"

    def form_valid(self, form):
        from postulo.core import gdpr

        # A deletion that does not say what it removed is a guess about its own effect, so
        # while the feature is offered the erasure carries the report and the person who
        # deleted reads it; off, the plain delete is what Postulo has always done (#297).
        if gdpr.is_offered(self.request.user):
            try:
                report = gdpr.erase_contact(self.object)
            except gdpr.ErasureRefused as refused:
                # A plugin holds rows about them that it could not remove, so nothing
                # was. Back to the person, who is still there, with the reason (#371).
                messages.error(self.request, str(refused))
                return redirect(reverse("jobs:contact_update", args=[self.object.pk]))
            messages.success(self.request, report.summary())
            return redirect(self.get_success_url())
        return super().form_valid(form)

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")


class ContactExportView(OwnedObjectMixin, View):
    """What the instance holds on one other person, as one document (#297).

    The answer to "what do you have on me?", handed over as a download rather than a page,
    because the point of a data-subject export is that it leaves the site with the person
    who asked. One `JsonResponse` rather than the archive's zip: a contact holds no files
    of its own, and a zip of one JSON entry would be ceremony.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.core import gdpr

        if not gdpr.is_offered(request.user):
            raise Http404
        contact = get_object_or_404(Contact.objects.for_user(request.user), pk=pk)
        response = JsonResponse(
            gdpr.contact_document(contact),
            json_dumps_params={"ensure_ascii": False},
            content_type="application/json",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="postulo-contact-{contact.pk}.json"'
        )
        return response


# -------------------------------------------------------------------- postings


def listing_page_context(request, posting, *, event_form=None) -> dict:
    """What the listing's page draws beyond the listing: its history, and what it kept.

    Written once for the two views that draw the page -- reading it, and adding to its
    history with a mistake in the form, which draws it again with the errors (#270).
    """
    from .forms import ListingEventForm
    from .history import history_of
    from .models import CapturedPage

    return {
        "posting": posting,
        "object": posting,
        "discard_reasons": DiscardReason.choices,
        # What was kept of the page this listing was captured from, if anything was
        # (#256). Here as well as on the review screen, because this is where somebody
        # looks once the advert has gone: the listing outlives the posting. A capture bound
        # to it later is one of these too (#270).
        "kept_pages": (
            CapturedPage.objects.for_user(request.user)
            .filter(capture__posting=posting)
            .select_related("capture")
        ),
        # What arrived about it, newest first (#270): the same entries the application's
        # page reads through its posting, once there is an application.
        "listing_events": history_of(posting),
        "event_form": event_form or ListingEventForm(user=request.user),
    }


class PostingDetailView(OwnedObjectMixin, DetailView):
    model = JobPosting
    template_name = "jobs/posting_detail.html"
    context_object_name = "posting"

    def get_queryset(self):
        return super().get_queryset().select_related("company").with_application_count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(listing_page_context(self.request, self.object))
        return context


class PostingUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = JobPosting
    form_class = JobPostingForm
    template_name = "jobs/posting_form.html"

    def form_valid(self, form):
        messages.success(self.request, _("Posting updated."))
        return super().form_valid(form)


class PostingCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = JobPosting
    form_class = JobPostingForm
    template_name = "jobs/posting_form.html"

    def get_initial(self) -> dict:
        initial = super().get_initial()
        company_id = self.request.GET.get("company")
        if (
            company_id
            and Company.objects.for_user(self.request.user).filter(pk=company_id).exists()
        ):
            initial["company"] = company_id
        return initial


class PostingDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = JobPosting
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

    def get_context_data(self, **kwargs):
        """A listing takes its applications with it; say how many before it does (#217)."""
        from django.utils.translation import ngettext

        from postulo.documents.models import RenderedDocument

        context = super().get_context_data(**kwargs)
        applications = self.object.applications.all()
        number = applications.count()
        consequences = []
        if number:
            consequences.append(
                ngettext(
                    "%(count)d application, with its timeline",
                    "%(count)d applications, with their timelines",
                    number,
                )
                % {"count": number}
            )
        # Its history goes with it; what the history pointed at does not (#270). A file
        # somebody forwarded lives in their documents and a capture stays a capture, and
        # saying so here is the difference between deleting a listing and deleting those.
        entries = self.object.events.all()
        if entries.exists():
            consequences.append(
                ngettext(
                    "%(count)d entry in its history",
                    "%(count)d entries in its history",
                    entries.count(),
                )
                % {"count": entries.count()}
            )
        if consequences:
            context["consequences"] = consequences
        kept = []
        sent = RenderedDocument.objects.filter(application__in=applications).count()
        if sent:
            kept.append(
                ngettext(
                    "The %(count)d document you sent is kept, and still says where it went.",
                    "The %(count)d documents you sent are kept, and still say where they went.",
                    sent,
                )
                % {"count": sent}
            )
        pointed = (
            entries.filter(artefact_id__isnull=False)
            .order_by()
            .values("artefact_type", "artefact_id")
            .distinct()
            .count()
        )
        if pointed:
            kept.append(
                ngettext(
                    "The %(count)d file or capture its history points at is kept where it is.",
                    "The %(count)d files and captures its history points at are kept where "
                    "they are.",
                    pointed,
                )
                % {"count": pointed}
            )
        if kept:
            context["kept"] = " ".join(kept)
        return context
