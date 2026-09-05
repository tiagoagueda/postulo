"""Views for companies, contacts and postings."""

from __future__ import annotations

from functools import cached_property

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from postulo.core import tables
from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin

from . import identifiers
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
            super().get_queryset().with_table_data().prefetch_related("industries", "identifiers")
        )
        search = self.request.GET.get("q", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(location__icontains=search)
                | Q(industries__name__icontains=search)
                | Q(identifiers__value__icontains=search)
            )
        # A company in two matching industries is still one row.
        return self.table.apply(queryset).distinct()

    def get_template_names(self) -> list[str]:
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return [f"{self.template_name}#htmx"]
        return [self.template_name]

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("q", "")
        context["table"] = self.table
        context["page_sizes"] = tables.PAGE_SIZES
        return context


class CompanyDetailView(OwnedObjectMixin, DetailView):
    model = Company
    template_name = "jobs/company_detail.html"
    context_object_name = "company"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("industries", "identifiers")

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["contacts"] = self.object.contacts.all()
        context["postings"] = self.object.postings.prefetch_related("applications")
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
        context["identifier_schemes"] = identifiers.SCHEMES.values()
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


class CompanyCreateView(
    OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CompanyIdentifiersMixin, CreateView
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"

    def form_valid(self, form):
        messages.success(self.request, _("Company added."))
        return super().form_valid(form)


class CompanyUpdateView(OwnedObjectMixin, UserFormKwargsMixin, CompanyIdentifiersMixin, UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"

    def form_valid(self, form):
        messages.success(self.request, _("Company updated."))
        return super().form_valid(form)


class CompanyDeleteView(OwnedObjectMixin, DeleteView):
    model = Company
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

    def form_valid(self, form):
        messages.success(self.request, _("Company deleted, along with its postings."))
        return super().form_valid(form)


# ------------------------------------------------------------------- industries


class IndustryListView(OwnedObjectMixin, ListView):
    """The person's vocabulary of industries, with how many companies each holds."""

    model = Industry
    template_name = "jobs/industry_list.html"
    context_object_name = "industries"

    def get_queryset(self):
        return super().get_queryset().annotate(company_count=Count("companies", distinct=True))


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


class ContactCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
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
        messages.success(self.request, _("Contact added."))
        return super().form_valid(form)


class ContactUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")


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
