"""Forms for companies, contacts and postings.

Every form that offers a choice of another record takes the signed-in user and narrows
the queryset to their own data. A select box populated from the whole table would be a
disclosure even if the resulting save were rejected.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Company, Contact, JobPosting


class OwnerScopedModelForm(forms.ModelForm):
    """A ModelForm that knows whose data it is allowed to offer."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.scope_querysets()

    def scope_querysets(self) -> None:  # pragma: no cover - overridden where needed
        """Narrow any related-object fields to ``self.user``."""


class CompanyForm(OwnerScopedModelForm):
    class Meta:
        model = Company
        fields = ("name", "website", "careers_url", "location", "industry", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def clean_name(self) -> str:
        """Refuse a duplicate before the database constraint does.

        The unique constraint is case-sensitive, so it would happily accept "acme"
        alongside "Acme"; this catches that, and produces a readable message instead of
        an IntegrityError.
        """
        name = self.cleaned_data["name"].strip()
        if self.user is None:
            return name
        clash = Company.objects.for_user(self.user).filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("You already have a company with that name."))
        return name


class ContactForm(OwnerScopedModelForm):
    class Meta:
        model = Contact
        fields = ("name", "role", "company", "email", "phone", "linkedin_url", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def scope_querysets(self) -> None:
        self.fields["company"].queryset = Company.objects.for_user(self.user)


class JobPostingForm(OwnerScopedModelForm):
    class Meta:
        model = JobPosting
        fields = (
            "title",
            "company",
            "location",
            "remote_type",
            "employment_type",
            "url",
            "source",
            "salary_min",
            "salary_max",
            "salary_currency",
            "salary_period",
            "posted_at",
            "closes_at",
            "description",
        )
        widgets = {
            "posted_at": forms.DateInput(attrs={"type": "date"}),
            "closes_at": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 10}),
        }

    def scope_querysets(self) -> None:
        self.fields["company"].queryset = Company.objects.for_user(self.user)

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("salary_min"), cleaned.get("salary_max")
        if low is not None and high is not None and low > high:
            self.add_error("salary_max", _("The upper figure cannot be below the lower one."))
        return cleaned
