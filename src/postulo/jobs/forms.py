"""Forms for companies, contacts and postings.

Every form that offers a choice of another record takes the signed-in user and narrows
the queryset to their own data. A select box populated from the whole table would be a
disclosure even if the resulting save were rejected.
"""

from __future__ import annotations

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from . import industries
from .models import Company, Contact, Industry, JobPosting


class OwnerScopedModelForm(forms.ModelForm):
    """A ModelForm that knows whose data it is allowed to offer."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.scope_querysets()

    def scope_querysets(self) -> None:  # pragma: no cover - overridden where needed
        """Narrow any related-object fields to ``self.user``."""


class CompanyForm(OwnerScopedModelForm):
    """A company, and the industries it operates in: pick from your own, or type new ones."""

    industries = forms.ModelMultipleChoiceField(
        label=_("Industries"),
        queryset=Industry.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    new_industries = forms.CharField(
        label=_("Other industries"),
        required=False,
        max_length=300,
        help_text=_("Separate several with commas. Anything new joins your list."),
        widget=forms.TextInput(attrs={"list": "industry-suggestions", "autocomplete": "off"}),
    )

    field_order = (
        "name",
        "website",
        "careers_url",
        "location",
        "industries",
        "new_industries",
        "notes",
    )

    class Meta:
        model = Company
        fields = ("name", "website", "careers_url", "location", "industries", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def scope_querysets(self) -> None:
        self.fields["industries"].queryset = Industry.objects.for_user(self.user)

    @property
    def suggestions(self) -> list[str]:
        """Starter industries the person has not already got, for the input's datalist."""
        return industries.suggestions(
            exclude=self.fields["industries"].queryset.values_list("name", flat=True)
        )

    def save(self, commit: bool = True) -> Company:
        company = super().save(commit=commit)
        if commit:
            self._add_new_industries(company)
        else:
            save_m2m = self.save_m2m

            def save_m2m_and_new():
                save_m2m()
                self._add_new_industries(company)

            self.save_m2m = save_m2m_and_new
        return company

    def _add_new_industries(self, company: Company) -> None:
        names = Industry.split(self.cleaned_data.get("new_industries", ""))
        if names:
            company.industries.add(*Industry.named(company.owner, names))

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


class IndustryForm(OwnerScopedModelForm):
    """Rename an industry, or fold it into another one."""

    merge_into = forms.ModelChoiceField(
        label=_("Merge into"),
        queryset=Industry.objects.none(),
        required=False,
        help_text=_(
            "Every company under this industry moves to the one chosen, and this one goes."
        ),
    )

    class Meta:
        model = Industry
        fields = ("name",)

    def scope_querysets(self) -> None:
        if self.instance.pk:
            self.fields["merge_into"].queryset = Industry.objects.for_user(self.user).exclude(
                pk=self.instance.pk
            )
        else:
            del self.fields["merge_into"]

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if self.user is None:
            return name
        clash = Industry.objects.for_user(self.user).filter(slug=slugify(name)[:60])
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists() and not self.data.get("merge_into"):
            raise forms.ValidationError(_("You already have that industry."))
        return name

    def save(self, commit: bool = True) -> Industry:
        target = self.cleaned_data.get("merge_into")
        if target is not None and self.instance.pk:
            for company in self.instance.companies.all():
                company.industries.add(target)
            self.instance.delete()
            return target
        industry = super().save(commit=False)
        industry.slug = slugify(industry.name)[:60] or "other"
        if commit:
            industry.save()
        return industry


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
