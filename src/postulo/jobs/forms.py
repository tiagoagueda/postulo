"""Forms for companies, contacts and postings.

Every form that offers a choice of another record takes the signed-in user and narrows
the queryset to their own data. A select box populated from the whole table would be a
disclosure even if the resulting save were rejected.
"""

from __future__ import annotations

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core import phone_field, phone_numbers, phones, slugs, web_links
from postulo.core.date_widgets import DateInput, DateTimeInput
from postulo.core.formsets import RowsAlreadyGone
from postulo.core.identifiers import IdentifierRow, OneOfEachKind, SchemeSelect, offering

from . import company_names, employment_services, esco, identifiers, industries, logos, structure
from .models import (
    Company,
    CompanyHiringSignal,
    CompanyIdentifier,
    CompanyKind,
    Contact,
    Department,
    Industry,
    JobPosting,
    LegalState,
    ListingEvent,
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
    "description_format": _(
        "Plain text is shown as typed. Markdown shows paragraphs, **bold**, *italic*, "
        "`code`, lists, quotes, [links](https://example.com) and # headings; raw HTML and "
        "images are never shown. The text is stored as you typed it either way."
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

    #: The fields that point at another shared record (#896): a listing's company, a
    #: contact's, a company's parent. Changed on a shared record, the new one must be one
    #: everybody it is shared with can see, or the save is refused saying who cannot.
    LINKS: tuple[str, ...] = ()

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.user is not None:
            from .sharing import check_link

            for name in self.LINKS:
                if name in self.fields and name in self.changed_data:
                    refusal = check_link(self.instance, cleaned.get(name), self.user)
                    if refusal:
                        self.add_error(name, refusal)
        return cleaned


class OwnNoteForm(OwnerScopedModelForm):
    """A form whose record holds one note per person: a company's, a contact's (#896).

    ``notes`` is the reader's own note, read and written for ``self.user`` and nobody
    else. A form narrowed to one field of the model -- a table cell -- drops it, so a cell
    saving a name never touches a note it did not draw.
    """

    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Yours. Nobody else sees them, and they never appear on a document."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if len(self._meta.fields or ()) <= 1:
            self.fields.pop("notes", None)
        elif "notes" in self.fields and self.instance.pk and self.user is not None:
            self.fields["notes"].initial = self.instance.note_for(self.user)

    def _save_m2m(self):
        super()._save_m2m()
        if "notes" in self.fields and self.user is not None:
            self.instance.write_note(self.user, self.cleaned_data.get("notes", ""))


def sector_choices(blank) -> list[tuple[str, str]]:
    """The 22 NACE sections as choices, in the reader's language, after a blank one (#897)."""
    return [("", blank), *industries.sections()]


class SectionedCheckboxes(forms.CheckboxSelectMultiple):
    """The person's industries, each box saying the sector it is in as ``data-section``, so
    the script can set the sector chooser from a box ticked (#897)."""

    sections: dict = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        section = self.sections.get(getattr(value, "value", value))
        if section:
            option["attrs"]["data-section"] = section
        return option


class HiringSignalForm(forms.ModelForm):
    """One dated entry about a company's hiring (#941): what was reported, as of when, and
    where it was learnt. Who noted it is the signed-in person, never a field."""

    class Meta:
        model = CompanyHiringSignal
        fields = ("kind", "noted_on", "source")
        # ISO, which is what a date box takes: the locale's own format shows it empty.
        widgets = {"noted_on": DateInput()}
        help_texts = {
            "kind": _("What was reported about its hiring."),
            "noted_on": _("The day it was true or reported, not today's date unless it is."),
        }

    def clean_noted_on(self):
        from django.utils import timezone

        day = self.cleaned_data["noted_on"]
        if day > timezone.localdate():
            raise forms.ValidationError(_("The date cannot be in the future."))
        return day

    def clean_source(self):
        from postulo.core.addresses import web_address

        try:
            return web_address(self.cleaned_data.get("source", ""))
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class CompanyForm(OwnNoteForm):
    LINKS = ("parent", "acquired_by")

    """A company, and the industries it operates in: pick from your own, or type new ones.

    **A sector above them (#897).** The sector is not the company's own: its sectors are its
    industries' sections. The chooser narrows the suggestions to one section's divisions and
    Postulo's short names in it, and a word of the person's own typed while a sector is
    chosen is filed under that sector. An industry with a division sets the chooser back to
    its section, so the two never disagree.

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
    sector = forms.ChoiceField(
        # Translators: the NACE section, the coarse level above an industry (a NACE
        # division): "Education", "Human health and social work". Keep the two words apart.
        label=_("Sector"),
        required=False,
        help_text=_(
            "The part of the economy it is in. Narrows the suggestions below, and an "
            "industry of your own added here is filed under it."
        ),
    )
    industries = forms.ModelMultipleChoiceField(
        label=_("Industries"),
        queryset=Industry.objects.none(),
        required=False,
        widget=SectionedCheckboxes,
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
        "email",
        "phone",
        "sector",
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
            "email",
            "industries",
            "from_career",
            "legal_state",
            "legal_state_since",
            "legal_state_source",
            "acquired_by",
        )
        widgets = {"legal_state_since": DateInput()}
        help_texts = {
            "legal_state": _("What the company is. Shown to everybody who can see it."),
            "kind": _(
                "An employer is somewhere you might work; a public employment service is "
                "the office you are registered with. A recruitment agency is an ordinary "
                "company: give it the industry Employment activities."
            ),
            "website": _(
                "Used by Find logo, and only when you press it. Postulo fetches nothing "
                "from here on its own."
            ),
        }

    def scope_querysets(self) -> None:
        """Narrow every field this form actually has to this person's own rows.

        Each one is guarded, because this form is also used one field at a time: an
        editable cell builds a version of it holding only the column being changed, so that
        a cell refuses exactly what the page refuses (#135). Scoping a field that is not
        there would make that impossible for the sake of an assumption nothing needs.
        """
        if "from_career" in self.fields and not self.instance.from_career:
            # Only offered where it is on, to be switched off by hand (#683): nobody marks
            # a company as from the career here, the career form does.
            del self.fields["from_career"]

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

        if "legal_state" in self.fields:
            # Not required either: left out, a company stays what it is (active, if new).
            self.fields["legal_state"].required = False
            self.fields["legal_state"].choices = LegalState.choices
        if "acquired_by" in self.fields:
            # Among the companies this person can see, minus this one: an acquirer nobody
            # the company is shared with could see is refused by `LINKS`, as a parent is.
            candidates = Company.objects.for_user(self.user)
            if self.instance and self.instance.pk:
                candidates = candidates.exclude(pk=self.instance.pk)
            self.fields["acquired_by"].queryset = candidates
            self.fields["acquired_by"].empty_label = _("Not recorded here")

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

        # Its switchboard (#329): one box while *Several telephone numbers* is off, as a
        # contact's is, and the rows are their own block on the page while it is on. Only
        # on the company's own form, never on a cell edited in the table.
        self.several_numbers = phone_numbers.several_allowed(self.user)
        if not self.several_numbers and "name" in self.fields and "notes" in self.fields:
            self.fields["phone"] = phone_field.PhoneField(
                label=_("Phone"),
                required=False,
                default_country=phones.default_country(_language_of(self.user)),
                has_notes=True,
            )
            if self.instance and self.instance.pk:
                primary = phone_numbers.primary_for(self.instance)
                self.fields["phone"].initial = primary.number if primary else ""
            self.order_fields(self.field_order)
        # Its social profiles (#906): one box while *Several social profiles* is off, as a
        # contact's, and the rows a block of their own on the page while it is on. Only on
        # the company's own form, never on a cell edited in the table.
        if "name" in self.fields and "notes" in self.fields:
            web_links.add_single_boxes(self, self.user, holder=self.instance, company=True)

        if "industries" in self.fields:
            # The person's own vocabulary, and whatever is on the company already: its
            # industries are the company's (#896), so somebody it is shared with sees -- and
            # may take off -- one somebody else gave it, and a save keeps the ones it drew.
            mine = Industry.objects.for_user(self.user)
            if self.instance.pk:
                mine = Industry.objects.filter(
                    Q(pk__in=mine.values("pk")) | Q(companies=self.instance)
                ).distinct()
            self.fields["industries"].queryset = mine
            widget = self.fields["industries"].widget
            widget.sections = dict(mine.exclude(section="").values_list("pk", "section"))
        if "sector" in self.fields:
            if "industries" not in self.fields:
                del self.fields["sector"]
            else:
                self.fields["sector"].choices = sector_choices(_("Any sector"))
                self.fields["sector"].widget.attrs["data-industry-sector"] = ""
                held = self.instance.sectors if self.instance.pk else []
                if len(held) == 1:
                    self.fields["sector"].initial = held[0]
        if "logo_url" in self.fields and self.instance.pk and self.instance.logo_source_url:
            self.fields["logo_url"].initial = self.instance.logo_source_url
        if "remove_logo" in self.fields and not (self.instance.pk and self.instance.has_logo):
            del self.fields["remove_logo"]

    def clean_legal_state(self):
        """Left blank, a company stays as it is (active, when new): a form posted without the
        block -- an older client, a script -- changes nothing."""
        return (
            self.cleaned_data.get("legal_state")
            or (self.instance.legal_state if self.instance.pk else "")
            or LegalState.ACTIVE
        )

    def clean_legal_state_source(self):
        """An http or https address, as every address drawn as a link is held to (#941): the
        field's own check also lets an ftp one through."""
        from postulo.core.addresses import web_address

        try:
            return web_address(self.cleaned_data.get("legal_state_source", ""))
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

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
    def suggestions(self) -> list[tuple[str, str]]:
        """What the datalist offers, as ``(name, section)``: Postulo's short names, then the
        NACE divisions, each with the sector it sits in for the chooser to narrow by.

        Minus anything this person already has, because a suggestion for something already
        on their list is a line of noise (#140). A company with no industries yet whose
        listings have titles ESCO knows is offered, first, the divisions ESCO's companion
        correspondence employs those occupations in (#897): offered, never required.
        """
        return industries.offered(
            exclude=self.fields["industries"].queryset.values_list("name", flat=True),
            first=self.divisions_from_postings(),
        )

    def divisions_from_postings(self) -> list[str]:
        """The NACE divisions the titles of this company's listings point at, most first,
        while it has no industries (#897)."""
        company = self.instance
        if not company.pk or company.industries.exists():
            return []
        counted: dict[str, int] = {}
        for group in company.postings.exclude(isco_code="").values_list("isco_code", flat=True):
            for division in esco.divisions_for(group):
                counted[division] = counted.get(division, 0) + 1
        return sorted(counted, key=lambda code: (-counted[code], code))

    def apply_logo(self, company: Company, by, *, spend: bool = True) -> str:
        """Do what the logo fields asked for. Returns a problem to show, or "".

        An address costs one use of ``by``'s fetch allowance, the person acting and not
        the company's owner (#960); ``spend=False`` where the caller has spent it already.

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
                logos.from_url(company, url, by=by, spend=spend)
            except logos.UnusableLogo as error:
                return str(error)
        return ""

    @property
    def link_boxes(self) -> list:
        """The social-profile box while *Several social profiles* is off (#906)."""
        return web_links.single_boxes(self, company=True)

    def save(self, commit: bool = True) -> Company:
        company = super().save(commit=commit)
        if commit:
            self._add_new_industries(company)
            if "phone" in self.fields:
                phone_numbers.save_only_number(
                    company, self.user, self.cleaned_data.get("phone", "")
                )
            web_links.save_single_boxes(self, company, self.user, company=True)
        else:
            save_m2m = self.save_m2m

            def save_m2m_and_new():
                save_m2m()
                self._add_new_industries(company)
                web_links.save_single_boxes(self, company, self.user, company=True)

            self.save_m2m = save_m2m_and_new
        return company

    def _add_new_industries(self, company: Company) -> None:
        names = Industry.split(self.cleaned_data.get("new_industries", ""))
        if names:
            sector = self.cleaned_data.get("sector", "")
            # Into the vocabulary of whoever typed them (#896), which is theirs whatever
            # company they are put on.
            company.industries.add(*Industry.named(self.user, names, section=sector))

    def clean(self) -> dict:
        """A known service picked means an employment service, named and addressed as the
        registry has it wherever the person wrote nothing; otherwise the name is required,
        exactly as it always was (#202)."""
        data = super().clean()
        if "parent" in self.fields and data.get("parent") is not None:
            if not self.instance.pk or "parent" in self.changed_data:
                from .sharing import check_child_link

                # A new company is not saved yet: it will be the person's alone (#957).
                subject = self.instance
                if not subject.pk and subject.owner_id is None:
                    subject.owner = self.user
                refusal = check_child_link(subject, data["parent"])
                if refusal:
                    self.add_error("parent", refusal)
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
                if self.name_clashes(service.name):
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
        if self.name_clashes(name):
            raise forms.ValidationError(_("You already have a company with that name."))
        return name

    def name_clashes(self, name: str) -> bool:
        """Whether the owner holds another company by ``name``.

        Somebody renaming a company shared with them is asking about its owner's other
        companies, so the answer is bounded, and a spent allowance is refused in its own
        words instead of either answer (#949).
        """
        from postulo.core import throttle

        try:
            return company_names.clashes(
                self.user,
                self.principal(),
                name,
                company=self.instance if self.instance.pk else None,
            )
        except throttle.TooOften as too_often:
            raise forms.ValidationError(str(too_often)) from too_often

    def principal(self):
        """Whose companies a name must not repeat: the company's owner, a person or a group
        (#896), or the person making a new one."""
        if self.instance.pk:
            return self.instance.principal
        return self.user


class CompanyLogoForm(forms.Form):
    """The logo dialog's own form: an address or an upload, and nothing else of the company (#853).

    Changing a logo does not save the rest of the company, as Your details' picture does not
    save the rest of the profile. The fields are the ones `CompanyForm` has always carried,
    with the same checks, so what is refused is refused in the same words.
    """

    logo_url = CompanyForm.base_fields["logo_url"]
    logo_upload = CompanyForm.base_fields["logo_upload"]
    clean_logo_upload = CompanyForm.clean_logo_upload
    apply_logo = CompanyForm.apply_logo

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("logo_url") or cleaned.get("logo_upload")) and not self.errors:
            raise forms.ValidationError(_("Give an address or choose a file."))
        return cleaned


class CompanyIdentifierForm(IdentifierRow, forms.ModelForm):
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
        scheme = self.fields["scheme"]
        # The registry's kinds, and this row's own where it is stored under a scheme the
        # instance has since deleted: it keeps the key, or the page could not be saved (#311).
        choices = offering([("", "—"), *identifiers.choices()], self.instance)
        scheme.choices = choices
        scheme.required = False
        # Django picks the widget from the model field when it builds the form, and that
        # field deliberately has no choices; the registry's schemes arrive only now, so the
        # select that shows them has to come with them, choices in hand — the field here is
        # a `CharField`, whose `choices` nothing reads from (#298). Its formset switches
        # off the kinds the other rows hold (#307).
        scheme.widget = SchemeSelect(choices=choices)
        self.fields["value"].required = False

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("scheme") and (data.get("value") or data.get("label")):
            self.add_error("scheme", _("Choose the type of identifier."))
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


class BaseCompanyIdentifierFormSet(RowsAlreadyGone, OneOfEachKind, forms.BaseInlineFormSet):
    """The rows together: one of each kind, no value twice, and the owner filled in.

    A row taken off at once by its dialog (#857) and still posted back by a copy of the page
    that drew it is treated as one marked for removal (`RowsAlreadyGone`).
    """

    def clean(self) -> None:
        super().clean()
        seen_values: set[tuple[str, str]] = set()
        for form in self.forms:
            if not form.is_valid() or not form.has_changed() or form.cleaned_data.get("DELETE"):
                continue
            scheme = form.cleaned_data.get("scheme")
            value = form.instance.value  # normalised by the model's clean()
            if not scheme or not value:
                continue
            # Case-blind, like the constraint underneath it (#211): "AB-12" and "ab-12" are
            # one identifier, and the form has to say so before the database refuses it.
            if (scheme, value.casefold()) in seen_values:
                form.add_error("value", _("This identifier is already listed."))
            seen_values.add((scheme, value.casefold()))
            if scheme != identifiers.OTHER and self.owner is not None:
                clash = (
                    CompanyIdentifier.objects.owned_by(self.owner)
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
        """Whose companies an identifier must not repeat: the company's owner, a person or a
        group (#896), or the person making a new company."""
        if getattr(self.instance, "pk", None):
            return self.instance.principal
        return getattr(self, "user", None)

    def save_new(self, form, commit=True):
        # Owned as the company is: `OwnedAsItsCompany` copies its owner on save (#896).
        form.instance.company = self.instance
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
    """Rename an industry, choose its sector, or fold it into another one.

    The sector is chosen only for a word of the person's own: a division's name has its
    section, and the box says so rather than offering a choice the save would undo (#897).
    """

    section = forms.ChoiceField(
        label=_("Sector"),
        required=False,
        help_text=_("The part of the economy this industry is in. You may leave it empty."),
    )

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
        fields = ("name", "section")

    def scope_querysets(self) -> None:
        self.fields["section"].choices = sector_choices(_("No sector"))
        if self.instance.pk and self.instance.code:
            self.fields["section"].disabled = True
            self.fields["section"].help_text = _(
                "Set by its NACE division, %(code)s. Rename it to a word of your own to "
                "choose another."
            ) % {"code": self.instance.code}
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
        clash = Industry.objects.for_user(self.user)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if slugs.same_name(clash, name) and not self.data.get("merge_into"):
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
        if commit:
            industry.save()
        return industry


def _language_of(user) -> str:
    """What the owner reads Postulo in, which is the best first guess at their country."""
    profile = getattr(user, "profile", None) if user is not None else None
    return getattr(profile, "language", "") or ""


class ContactForm(OwnNoteForm):
    LINKS = ("company",)

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
        fields = ("name", "role", "company", "email")
        help_texts = {
            "role": _("Their job title, as they give it themselves."),
            "company": _("Where they work. Only companies you have recorded are offered."),
            "email": _(
                "Used to address them on an interview's calendar file. Postulo never writes "
                "to it by itself."
            ),
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
                # A contact has notes, which is where an extension goes.
                has_notes=True,
            )
            if self.instance and self.instance.pk:
                primary = phone_numbers.primary_for(self.instance)
                self.fields["phone"].initial = primary.number if primary else ""
        # And one box per kind of link whose feature is off, on the same terms (#189).
        web_links.add_single_boxes(self, self.user, holder=self.instance)

    def clean(self):
        cleaned = super().clean()
        # A department belongs to one company, so a contact who changes employer, or
        # has none now, leaves the team behind. This runs before the model is validated,
        # which would otherwise refuse the old department against the new company with
        # an error on a field this form does not draw. `_save_department` then attaches
        # or makes the right team at the new company (#527).
        if (
            "company" in cleaned
            and self.instance.department_id
            and cleaned["company"] != self.instance.company
        ):
            self.instance.department = None
        return cleaned

    def clean_phone(self) -> str:
        typed = (self.cleaned_data.get("phone") or "").strip()
        primary = phone_numbers.primary_for(self.instance) if self.instance.pk else None
        # A contact's number is one this account recorded, so it collides with nothing on
        # the instance and asking spends nothing (#329).
        refused = phone_numbers.refusal(
            self.user, typed, exclude_pk=primary.pk if primary else None, identifies_owner=False
        )
        if refused:
            raise forms.ValidationError(refused)
        return typed

    def scope_querysets(self) -> None:
        self.fields["company"].queryset = Company.objects.for_user(self.user).offered(
            including=self.instance.company_id
        )
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
        # The company's own, whoever filed them (#896), at a company this person can see.
        return list(
            Department.objects.filter(
                company_id=company_id,
                company__in=Company.objects.visible_to(self.user).values("pk"),
            ).values_list("name", flat=True)
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
        # Capitals do not make a second team; the spelling first entered stays.
        department = (
            Department.objects.filter(
                company_id=contact.company_id,
                name_key=slugs.name_key(name),
            )
            .order_by("pk")
            .first()
        )
        if department is None:
            department = Department.objects.create(company_id=contact.company_id, name=name)
        if contact.department_id != department.pk:
            contact.department = department
            contact.save(update_fields=["department", "updated_at"])


class JobPostingForm(OwnerScopedModelForm):
    LINKS = ("company",)

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
            "description_format",
        )
        widgets = {
            "posted_at": DateInput(),
            "closes_at": DateInput(),
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
            company.queryset = Company.objects.for_user(self.user).offered(
                including=self.instance.company_id
            )

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
        one with a user does — there is nobody else's data in it to keep apart (#266). Where
        the listing's company is known and has sectors, the unit groups ESCO's companion
        correspondence places in them come first, and nothing is left out (#897).
        """
        return {"title-suggestions": esco.suggestions(sections=self.company_sectors())}

    def company_sectors(self) -> list[str]:
        """The sectors of the company this listing is for, where that is already known."""
        company = None
        if self.instance.pk and self.instance.company_id:
            company = self.instance.company
        else:
            chosen = self.initial.get("company") or self.data.get("company")
            if chosen and self.user is not None:
                pk = getattr(chosen, "pk", chosen)
                company = (
                    Company.objects.for_user(self.user).filter(pk=pk).first()
                    if str(pk).isdigit()
                    else None
                )
        return company.sectors if company is not None else []


def who_and_where(contact) -> str:
    """A contact as a choice: the name, and the company beside it where there is one.

    Two people called the same thing are otherwise two identical lines in a list, and
    choosing between them would be a guess. Here rather than beside the application form
    that first needed it (#239), because a listing's history asks the same question (#270).
    """
    if contact.company_id:
        return f"{contact.name} · {contact.company.name}"
    return contact.name


class ListingEventForm(OwnerScopedModelForm):
    """Add an entry to a listing's history: what arrived about it, or what was said (#270).

    What the timeline's own form asks -- the kind, when, a line, the details -- and two
    questions it never needed: who it came from, and which of the person's files it is
    about. Both are chosen from the person's own records, and each is offered only where
    there is something to choose, because a picker of one empty option is a control asking
    to be ignored. A capture is not offered as a kind: binding one writes its own entry, from
    the review screen, pointing at the capture itself.

    The details are held to the length `jobs.history` keeps, here as well as there, so a
    pasted thread is refused with a sentence rather than cut without one.
    """

    #: The person's own files, set in `scope_querysets`, which reaches into `documents` when it
    #: runs -- or the field is taken away there, where there are none to choose from.
    document = forms.ModelChoiceField(
        label=_("A file from your documents"),
        queryset=None,
        required=False,
        help_text=_(
            "It stays in your documents, where it is kept; the entry points at it, and "
            "keeps its own words if the file is ever deleted."
        ),
    )

    class Meta:
        model = ListingEvent
        fields = ("kind", "occurred_at", "summary", "body", "contact")
        labels = {"contact": _("From")}
        help_texts = {
            "kind": _(
                "What it was. Another capture of the same advert is added from the review "
                "screen, so it is not offered here."
            ),
            "occurred_at": _("When it happened, which is not always when you write it down."),
            "summary": _("The line the history shows."),
            "body": _(
                "Anything longer: the message itself, or what was said on the phone. It sits "
                "under the line."
            ),
            "contact": _("Who it came from, if it is somebody you have recorded."),
        }
        widgets = {
            "occurred_at": DateTimeInput(),
            "body": forms.Textarea(attrs={"rows": 4}),
        }

    def scope_querysets(self) -> None:
        from django.core.validators import MaxLengthValidator

        from postulo.documents.models import UploadedDocument

        from .history import BODY_MAX_CHARS
        from .models import SYSTEM_LISTING_EVENT_KINDS, ListingEventKind

        when = self.fields["occurred_at"]
        when.input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
        when.initial = timezone.localtime()

        body = self.fields["body"]
        body.max_length = BODY_MAX_CHARS
        body.validators.append(MaxLengthValidator(BODY_MAX_CHARS))
        body.widget.attrs["maxlength"] = str(BODY_MAX_CHARS)

        kinds = [
            (value, label)
            for value, label in ListingEventKind.choices
            if value not in SYSTEM_LISTING_EVENT_KINDS
        ]

        contacts = Contact.objects.for_user(self.user).select_related("company")
        if contacts.exists():
            self.fields["contact"].queryset = contacts
            self.fields["contact"].empty_label = _("Nobody you have recorded")
            self.fields["contact"].label_from_instance = who_and_where
        else:
            del self.fields["contact"]

        files = UploadedDocument.objects.for_user(self.user)
        if files.exists():
            self.fields["document"].queryset = files
            self.fields["document"].empty_label = _("No file")
        else:
            # And no *document* kind either: an entry that says it is a file has to point
            # at one, and there is none to point at yet.
            del self.fields["document"]
            kinds = [(value, label) for value, label in kinds if value != ListingEventKind.DOCUMENT]
        self.fields["kind"].choices = kinds

    def clean(self):
        from .models import ListingEventKind

        cleaned = super().clean()
        if cleaned.get("kind") == ListingEventKind.DOCUMENT and not cleaned.get("document"):
            self.add_error("document", _("Choose which of your files it is."))
        return cleaned
