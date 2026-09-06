"""Forms for companies, contacts and postings.

Every form that offers a choice of another record takes the signed-in user and narrows
the queryset to their own data. A select box populated from the whole table would be a
disclosure even if the resulting save were rejected.
"""

from __future__ import annotations

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from . import identifiers, industries, logos
from .models import Company, CompanyIdentifier, Contact, Industry, JobPosting


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

    logo_url = forms.URLField(
        label=_("Logo address"),
        required=False,
        max_length=500,
        help_text=_(
            "Postulo fetches it once and keeps a copy; the address is never shown to a "
            "browser. PNG, JPEG, GIF or WebP."
        ),
    )
    logo_upload = forms.ImageField(
        label=_("Or upload one"),
        required=False,
        help_text=_("Kept at 256 pixels square, re-encoded, with nothing else of the file."),
    )
    remove_logo = forms.BooleanField(label=_("Remove the logo"), required=False)

    field_order = (
        "name",
        "website",
        "careers_url",
        "location",
        "industries",
        "new_industries",
        "logo_url",
        "logo_upload",
        "remove_logo",
        "notes",
    )

    class Meta:
        model = Company
        fields = ("name", "website", "careers_url", "location", "industries", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def scope_querysets(self) -> None:
        self.fields["industries"].queryset = Industry.objects.for_user(self.user)
        if self.instance.pk and self.instance.logo_source_url:
            self.fields["logo_url"].initial = self.instance.logo_source_url
        if not (self.instance.pk and self.instance.logo):
            del self.fields["remove_logo"]

    def clean_logo_upload(self):
        """Refuse an oversized file before anything tries to decode it."""
        upload = self.cleaned_data.get("logo_upload")
        if upload and upload.size > logos.MAX_BYTES:
            raise forms.ValidationError(_("That file is larger than a logo should be."))
        return upload

    @property
    def suggestions(self) -> list[str]:
        """Starter industries the person has not already got, for the input's datalist."""
        return industries.suggestions(
            exclude=self.fields["industries"].queryset.values_list("name", flat=True)
        )

    def apply_logo(self, company: Company) -> str:
        """Do what the logo fields asked for. Returns a problem to show, or "".

        A logo that cannot be fetched never stops a company being saved: the person was
        recording an employer, and an icon that would not come is not a reason to lose
        the rest of what they typed.
        """
        if self.cleaned_data.get("remove_logo"):
            logos.clear(company)
            return ""
        upload = self.cleaned_data.get("logo_upload")
        if upload:
            try:
                logos.from_upload(company, upload.read())
            except logos.UnusableLogo as error:
                return str(error)
            return ""
        url = (self.cleaned_data.get("logo_url") or "").strip()
        if url and url != company.logo_source_url:
            try:
                logos.from_url(company, url)
            except logos.UnusableLogo as error:
                return str(error)
        return ""

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


class CompanyIdentifierForm(forms.ModelForm):
    """One row of the identifiers block: a scheme, the value, and a name when it is Other."""

    class Meta:
        model = CompanyIdentifier
        fields = ("scheme", "value", "label")
        widgets = {
            "value": forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A blank first choice, so an untouched extra row counts as unchanged and is
        # dropped rather than complaining that its value is missing.
        self.fields["scheme"].choices = [("", "—"), *identifiers.CHOICES]
        self.fields["scheme"].required = False
        self.fields["value"].required = False

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("scheme") and (data.get("value") or data.get("label")):
            self.add_error("scheme", _("Choose what kind of identifier this is."))
        if data.get("scheme") and not data.get("value"):
            self.add_error("value", _("Type the identifier."))
        return data


class BaseCompanyIdentifierFormSet(forms.BaseInlineFormSet):
    """The rows together: no scheme twice, no value twice, and the owner filled in."""

    def clean(self) -> None:
        super().clean()
        seen_schemes: set[str] = set()
        seen_values: set[tuple[str, str]] = set()
        for form in self.forms:
            if not form.is_valid() or not form.has_changed() or form.cleaned_data.get("DELETE"):
                continue
            scheme = form.cleaned_data.get("scheme")
            value = form.instance.value  # normalised by the model's clean()
            if not scheme or not value:
                continue
            if scheme != identifiers.OTHER:
                if scheme in seen_schemes:
                    form.add_error("scheme", _("This kind of identifier is already listed."))
                seen_schemes.add(scheme)
            if (scheme, value) in seen_values:
                form.add_error("value", _("This identifier is already listed."))
            seen_values.add((scheme, value))
            if scheme != identifiers.OTHER and self.owner is not None:
                clash = (
                    CompanyIdentifier.objects.for_user(self.owner)
                    .filter(scheme=scheme, value=value)
                    .exclude(company=self.instance if self.instance.pk else None)
                    .select_related("company")
                    .first()
                )
                if clash is not None:
                    form.add_error(
                        "value",
                        _("%(company)s already carries this identifier.")
                        % {"company": clash.company.name},
                    )

    @property
    def owner(self):
        return getattr(self.instance, "owner", None) or getattr(self, "user", None)

    def save_new(self, form, commit=True):
        form.instance.owner = self.instance.owner
        return super().save_new(form, commit=commit)


CompanyIdentifierFormSet = forms.inlineformset_factory(
    Company,
    CompanyIdentifier,
    form=CompanyIdentifierForm,
    formset=BaseCompanyIdentifierFormSet,
    extra=1,
    can_delete=True,
)


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
