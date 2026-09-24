"""Forms for companies, contacts and postings.

Every form that offers a choice of another record takes the signed-in user and narrows
the queryset to their own data. A select box populated from the whole table would be a
disclosure even if the resulting save were rejected.
"""

from __future__ import annotations

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from postulo.core import phone_field, phone_numbers, phones, web_links

from . import employment_services, esco, identifiers, industries, logos, structure
from .models import (
    Company,
    CompanyIdentifier,
    CompanyKind,
    Contact,
    Department,
    Industry,
    JobPosting,
)

#: What a posting's fields say about themselves, in one place (#205).
#:
#: The same thirteen fields are declared three times -- `JobPostingForm` here, and
#: `PostingIntakeForm` and `ApplicationIntakeForm` in `applications.forms`, which are plain
#: forms rather than model forms and so inherit nothing. Writing the sentences three times
#: would be three things to keep true; the ones that drifted would be the ones on the form
#: nobody looked at.
POSTING_HELP = {
    "title": _(
        "As the posting words it. The ISCO-08 code the words match in the ESCO "
        "classification follows the posting, in the language you read; a title that matches "
        "nothing has no code, and that is not a lesser kind of job."
    ),
    "location": _(
        "As the posting words it — a city, a region, a country. Free text: nothing is "
        "looked up anywhere."
    ),
    "remote_type": _(
        "On site is at their address; hybrid is some days there and some not; remote is no "
        "address of theirs at all."
    ),
    "employment_type": _(
        "Postulo's own words. A board's label may not land exactly on one of them, and the "
        "nearest is fine — this is for your own sorting."
    ),
    "url": _(
        "The posting's own address, kept so you can go back to it. Postulo reads the page "
        "when you capture it and never fetches it again."
    ),
    "salary_min": _("Gross, before tax. One end on its own is fine — most postings give one."),
    "salary_max": _("Gross, before tax. Leave it empty if the posting names a single figure."),
    "salary_currency": _("It means nothing without an amount beside it, and is ignored then."),
    "salary_period": _(
        "What the figure is per. It means nothing without an amount beside it, and is ignored then."
    ),
    "posted_at": _("When the employer put it up, if the posting says. Empty means unknown."),
    "closes_at": _(
        "The employer's own closing date, if the posting gives one. Empty means unknown, "
        "not open forever."
    ),
    "description": _(
        "Paste the posting's text. It is kept exactly as pasted, and nothing is ever "
        "fetched from the address above to fill it."
    ),
}


class OwnerScopedModelForm(forms.ModelForm):
    """A ModelForm that knows whose data it is allowed to offer."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.scope_querysets()

    def scope_querysets(self) -> None:  # pragma: no cover - overridden where needed
        """Narrow any related-object fields to ``self.user``."""


class CompanyForm(OwnerScopedModelForm):
    """A company, and the industries it operates in: pick from your own, or type new ones.

    Or the employment service the person is registered with (#202): a kind, and beside it
    the services Postulo knows by country, so that adding France Travail is a choice
    rather than a typing exercise. Picking one fills the name and the website where the
    person left them blank; what they typed wins.
    """

    known_service = forms.ChoiceField(
        label=_("A known service"),
        required=False,
        help_text=_(
            "Pick the public employment service you are registered with and leave the "
            "name blank; Postulo fills in the name and the website. Anything you type "
            "here is kept instead."
        ),
    )
    industries = forms.ModelMultipleChoiceField(
        label=_("Industries"),
        queryset=Industry.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Tick as many as fit. The list is your own — Industries, beside Companies, "
            "is where it is kept."
        ),
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
    #: `FileField`, not `ImageField`: an `ImageField` verifies with Pillow, which cannot
    #: read an SVG and so refused the format logos most often come in — with "that file
    #: could not be read as an image", about a file that was perfectly valid (#264). What
    #: an image is, is decided in `jobs.logos` for every route into it, not twice.
    logo_upload = forms.FileField(
        label=_("Or upload one"),
        required=False,
        help_text=_(
            "PNG, JPEG, GIF, WebP or SVG. It is re-encoded at its own size, with nothing "
            "else the file carried; an SVG is kept as a vector and stripped of everything "
            "but the drawing."
        ),
    )
    remove_logo = forms.BooleanField(label=_("Remove the logo"), required=False)

    field_order = (
        "name",
        "kind",
        "known_service",
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
        fields = (
            "name",
            "kind",
            "parent",
            "website",
            "careers_url",
            "location",
            "industries",
            "notes",
        )
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "kind": _(
                "An employer is somewhere you might work; an employment service is an "
                "office or agency that lists other people's openings."
            ),
            "website": _(
                "Used by Find logo, and only when you press it. Postulo fetches nothing "
                "from here on its own."
            ),
            "location": _("Where they are, as you would write it. Nothing is looked up."),
            "notes": _("Yours. They never appear on a document or go anywhere else."),
        }

    def scope_querysets(self) -> None:
        """Narrow every field this form actually has to this person's own rows.

        Each one is guarded, because this form is also used one field at a time: an
        editable cell builds a version of it holding only the column being changed, so that
        a cell refuses exactly what the page refuses (#135). Scoping a field that is not
        there would make that impossible for the sake of an assumption nothing needs.
        """
        if "parent" in self.fields and not structure.structure_allowed(self.user):
            # The feature is off, so the field is not offered. Not cleared: the link stays
            # exactly where it is and comes back when the feature does, which is what "off
            # deletes nothing" means everywhere else here too (#138).
            del self.fields["parent"]

        if "parent" in self.fields:
            # The parent is offered as this person's other companies, minus this one and
            # everything already under it. `Company.clean` refuses a loop anyway; refusing
            # something the form offered is worse than not offering it.
            companies = Company.objects.for_user(self.user)
            if self.instance and self.instance.pk:
                excluded = {self.instance.pk, *(c.pk for c in self.instance.descendants())}
                companies = companies.exclude(pk__in=excluded)
            self.fields["parent"].queryset = companies
            self.fields["parent"].empty_label = _("Not part of another company")

        if "kind" in self.fields:
            # Not required, so that a form posted without it -- an older client, a script,
            # a test that predates kinds -- records an employer, which is the default.
            self.fields["kind"].required = False

        if "known_service" in self.fields:
            self.fields["known_service"].choices = [
                ("", _("Not one of these")),
                *employment_services.grouped(),
            ]
            # The name may be left blank when a known service is picked, since the
            # service has one; `clean` puts it back to required otherwise.
            self.fields["name"].required = False

        if "industries" in self.fields:
            self.fields["industries"].queryset = Industry.objects.for_user(self.user)
        if "logo_url" in self.fields and self.instance.pk and self.instance.logo_source_url:
            self.fields["logo_url"].initial = self.instance.logo_source_url
        if "remove_logo" in self.fields and not (self.instance.pk and self.instance.logo):
            del self.fields["remove_logo"]

    def clean_logo_upload(self):
        """Refuse here whatever `logos.process` would refuse later, in the same words.

        The field is a `FileField` since #264, so Pillow no longer vets it on the way past
        — which is the point, because Pillow cannot read the format logos most often come
        in and refused an SVG as "not an image". What an image is, is `jobs.logos`'
        decision, and this asks it rather than keeping a second opinion.
        """
        upload = self.cleaned_data.get("logo_upload")
        if not upload:
            return upload
        if upload.size > logos.MAX_BYTES:
            raise forms.ValidationError(_("That file is larger than a logo should be."))
        data = upload.read()
        upload.seek(0)
        try:
            logos.process(data)
        except logos.UnusableLogo as error:
            raise forms.ValidationError(str(error)) from error
        return upload

    @property
    def suggestions(self) -> list[str]:
        """What the datalist offers: Postulo's short names, then the NACE divisions.

        Minus anything this person already has, because a suggestion for something already
        on their list is a line of noise (#140).
        """
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

    def clean(self) -> dict:
        """A known service picked means an employment service, named and addressed as the
        registry has it wherever the person wrote nothing; otherwise the name is required,
        exactly as it always was (#202)."""
        data = super().clean()
        if "kind" in self.fields and not data.get("kind"):
            data["kind"] = CompanyKind.EMPLOYER
        if "known_service" not in self.fields:
            return data
        service = employment_services.by_key(data.get("known_service", ""))
        name = (data.get("name") or "").strip()
        if service is not None:
            data["kind"] = CompanyKind.EMPLOYMENT_SERVICE
            if not name:
                data["name"] = service.name
                # The registry's name may clash with one the person already has, and
                # `clean_name` ran on a blank; the same refusal, in the same words.
                clash = Company.objects.for_user(self.user).filter(name__iexact=service.name)
                if self.instance.pk:
                    clash = clash.exclude(pk=self.instance.pk)
                if clash.exists():
                    self.add_error("name", _("You already have a company with that name."))
            if not data.get("website"):
                data["website"] = service.website
        elif not name and "name" not in self.errors:
            self.add_error("name", self.fields["name"].error_messages["required"])
        return data

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
        self.fields["scheme"].choices = [("", "—"), *identifiers.choices()]
        self.fields["scheme"].required = False
        self.fields["value"].required = False

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("scheme") and (data.get("value") or data.get("label")):
            self.add_error("scheme", _("Choose what kind of identifier this is."))
        if data.get("scheme") and not data.get("value"):
            self.add_error("value", _("Type the identifier."))
        # The name means something only for Other, and the display side has always read
        # it only then. A name typed and then given another kind used to be stored,
        # invisible everywhere and still in the export; it is blanked now, and an Other
        # with no name -- which showed as the word "Other", telling nobody anything -- is
        # refused (#284).
        if data.get("scheme") == "other":
            if not (data.get("label") or "").strip():
                self.add_error("label", _("Say what this identifier is."))
        else:
            data["label"] = ""
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
            # Case-blind, like the constraint underneath it (#211): "AB-12" and "ab-12" are
            # one identifier, and the form has to say so before the database refuses it.
            if (scheme, value.casefold()) in seen_values:
                form.add_error("value", _("This identifier is already listed."))
            seen_values.add((scheme, value.casefold()))
            if scheme != identifiers.OTHER and self.owner is not None:
                clash = (
                    CompanyIdentifier.objects.for_user(self.owner)
                    .filter(scheme=scheme, value__iexact=value)
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


def _language_of(user) -> str:
    """What the owner reads Postulo in, which is the best first guess at their country."""
    profile = getattr(user, "profile", None) if user is not None else None
    return getattr(profile, "language", "") or ""


class ContactForm(OwnerScopedModelForm):
    #: Typed rather than chosen, because a team is usually known before it is a record and
    #: making somebody create one first is a form standing in front of a form. The name is
    #: matched against the company's existing departments and only made when it is new,
    #: which is how `Industry.named` already works.
    new_department = forms.CharField(
        label=_("Department"),
        required=False,
        max_length=120,
        help_text=_("The team they are in, if you know it. A new name joins the company's list."),
        widget=forms.TextInput(attrs={"list": "department-suggestions", "autocomplete": "off"}),
    )

    class Meta:
        model = Contact
        fields = ("name", "role", "company", "email", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "role": _("Their job title, as they give it themselves."),
            "company": _("Where they work. Only companies you have recorded are offered."),
            "email": _(
                "Used to address them on an interview's calendar file. Postulo never writes "
                "to it by itself."
            ),
            "notes": _("Yours. They never appear on a document or go anywhere else."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A recruiter's number written down as "06 12 34 56 78" cannot be dialled from
        # anywhere else, and the moment to fix that is the moment somebody types it.
        #
        # One box while *Several telephone numbers* is switched off; the rows are their own
        # formset while it is on, and two controls writing one primary row would be two
        # answers to the same question.
        self.several_numbers = phone_numbers.several_allowed(self.user)
        if not self.several_numbers:
            self.fields["phone"] = phone_field.PhoneField(
                label=_("Phone"),
                required=False,
                default_country=phones.default_country(_language_of(self.user)),
                help_text=_(
                    "Kept in the international form, so you can still ring it from another "
                    "country. A number that already starts with + is taken as it is."
                ),
            )
            if self.instance and self.instance.pk:
                primary = phone_numbers.primary_for(self.instance)
                self.fields["phone"].initial = primary.number if primary else ""
        # And one box per kind of link whose feature is off, on the same terms (#189).
        web_links.add_single_boxes(self, self.user, holder=self.instance)

    def clean_phone(self) -> str:
        typed = (self.cleaned_data.get("phone") or "").strip()
        primary = phone_numbers.primary_for(self.instance) if self.instance.pk else None
        if typed and phone_numbers.taken_elsewhere(
            typed, exclude_pk=primary.pk if primary else None
        ):
            raise forms.ValidationError(phone_numbers.collision_message(self.user))
        return typed

    def scope_querysets(self) -> None:
        self.fields["company"].queryset = Company.objects.for_user(self.user)
        if not structure.structure_allowed(self.user):
            # Off, so a contact is simply somebody at a company, which is what it was
            # before departments existed. The row keeps its department (#138).
            del self.fields["new_department"]
        elif self.instance and self.instance.pk and self.instance.department_id:
            self.fields["new_department"].initial = self.instance.department.name

    @property
    def department_suggestions(self) -> list[str]:
        """The departments already recorded at this contact's company, for the datalist."""
        company_id = self.instance.company_id if self.instance else None
        if not company_id:
            return []
        return list(
            Department.objects.for_user(self.user)
            .filter(company_id=company_id)
            .values_list("name", flat=True)
        )

    def save(self, commit: bool = True):
        contact = super().save(commit=commit)
        if commit and not self.several_numbers:
            phone_numbers.save_only_number(contact, self.user, self.cleaned_data.get("phone", ""))
        if commit:
            web_links.save_single_boxes(self, contact, self.user)
            if "new_department" in self.fields:
                self._save_department(contact)
        return contact

    def _save_department(self, contact) -> None:
        """Attach the named department, making it if the company has not got one.

        Clearing the box detaches the contact and leaves the department alone: a team does
        not stop existing because one person moved out of it.
        """
        name = (self.cleaned_data.get("new_department") or "").strip()[:120]
        if not name or not contact.company_id:
            if contact.department_id:
                contact.department = None
                contact.save(update_fields=["department", "updated_at"])
            return
        department, _created = Department.objects.get_or_create(
            owner=self.user, company_id=contact.company_id, name=name
        )
        if contact.department_id != department.pk:
            contact.department = department
            contact.save(update_fields=["department", "updated_at"])


class JobPostingForm(OwnerScopedModelForm):
    #: The title offers the ESCO unit groups as you type it, in the language read (#266).
    #: `autocomplete="off"` keeps the browser's memory of the box off it, as on the intake
    #: form; the code the words match follows the posting and is not offered here.
    title = forms.CharField(
        label=_("Job title"),
        max_length=250,
        widget=forms.TextInput(attrs={"list": "title-suggestions", "autocomplete": "off"}),
        help_text=POSTING_HELP["title"],
    )

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
        #: `source` is left out: the model's own sentence says it, and a model form takes
        #: that already. Naming it here would be the same words in a second place.
        help_texts = POSTING_HELP

    def scope_querysets(self) -> None:
        # `get`, because a cell editor is this form narrowed to one field, and the field it
        # was narrowed to is usually not this one (#135, #160).
        company = self.fields.get("company")
        if company is not None:
            company.queryset = Company.objects.for_user(self.user)

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("salary_min"), cleaned.get("salary_max")
        if low is not None and high is not None and low > high:
            self.add_error("salary_max", _("The upper figure cannot be below the lower one."))
        return cleaned

    @property
    def datalists(self) -> dict[str, list[str]]:
        """What the title's ``<datalist>`` offers: the ESCO unit groups, in the language read.

        Classification, not records, so a form with no user attached offers the same thing
        one with a user does — there is nobody else's data in it to keep apart (#266).
        """
        return {"title-suggestions": esco.suggestions()}
