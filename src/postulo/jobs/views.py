"""Views for companies, contacts and postings."""

from __future__ import annotations

from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from postulo.core import tables
from postulo.core.cells import EditableCellView
from postulo.core.files import serve_private_file
from postulo.core.mixins import (
    OwnedObjectMixin,
    OwnerFormMixin,
    PhoneNumbersMixin,
    WebLinksMixin,
)
from postulo.core.redirects import safe_next

from . import identifiers, logos
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


class CompanyListView(OwnedObjectMixin, ListView):
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
        search = self.request.GET.get("q", "").strip()
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

    def get_template_names(self) -> list[str]:
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return [f"{self.template_name}#htmx"]
        return [self.template_name]

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
        industry = Industry.objects.for_user(request.user).filter(pk=wanted).first()
        if industry is None:
            return 0
        changed = 0
        for company in rows:
            if not company.industries.filter(pk=industry.pk).exists():
                company.industries.add(industry)
                changed += 1
        return changed

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

    def form_valid(self, form):
        formset = self.get_identifiers()
        if formset.is_bound and not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, identifiers=formset))
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
        messages.success(self.request, _("Company added."))
        return super().form_valid(form)


class CompanyUpdateView(
    LogoFormMixin, OwnedObjectMixin, UserFormKwargsMixin, CompanyIdentifiersMixin, UpdateView
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"

    def form_valid(self, form):
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
        response = serve_private_file(request, company.logo, download_name="logo.png")
        response["Cache-Control"] = "private, max-age=86400"
        return response


class CompanyLogoActionView(OwnedObjectMixin, View):
    """*Find logo* and *Refresh*: one request each, when a person presses the button."""

    def get_queryset(self):
        return Company.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int, action: str) -> HttpResponse:
        company = get_object_or_404(self.get_queryset(), pk=pk)
        try:
            if action == "website":
                found = logos.find_on_website(company)
                messages.success(request, _("Found a logo at %(url)s.") % {"url": found})
            elif action == "refresh":
                if not company.logo_source_url:
                    messages.info(request, _("There is no address to fetch it from again."))
                else:
                    logos.from_url(company, company.logo_source_url)
                    messages.success(request, _("Fetched again."))
            elif action == "remove":
                logos.clear(company)
                messages.success(request, _("The logo is gone."))
            else:
                raise Http404
        except logos.UnusableLogo as error:
            messages.error(request, str(error))
        return redirect(safe_next(request, company.get_absolute_url()))


class CompanyDeleteView(OwnedObjectMixin, DeleteView):
    model = Company
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

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


class IndustryDeleteView(OwnedObjectMixin, DeleteView):
    model = Industry
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:industry_list")


# -------------------------------------------------------------------- contacts


class ContactCreateView(
    OwnedObjectMixin,
    UserFormKwargsMixin,
    OwnerFormMixin,
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
    OwnedObjectMixin, UserFormKwargsMixin, PhoneNumbersMixin, WebLinksMixin, UpdateView
):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

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


class ContactDeleteView(OwnedObjectMixin, DeleteView):
    model = Contact
    template_name = "partials/confirm_delete.html"

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")


# -------------------------------------------------------------------- postings


class PostingDetailView(OwnedObjectMixin, DetailView):
    model = JobPosting
    template_name = "jobs/posting_detail.html"
    context_object_name = "posting"

    def get_queryset(self):
        return super().get_queryset().select_related("company").with_application_count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["discard_reasons"] = DiscardReason.choices
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


class PostingDeleteView(OwnedObjectMixin, DeleteView):
    model = JobPosting
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")
