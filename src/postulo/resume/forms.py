"""Forms for the career record."""

from __future__ import annotations

from django import forms
from django.db import models as django_models
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from postulo.core import language_names, languages, phones, postal
from postulo.core.language_field import LanguageChoiceField
from postulo.jobs.forms import OwnerScopedModelForm

from . import driving, publications, translatable
from .models import (
    Certification,
    DrivingLicence,
    Education,
    Experience,
    Honour,
    LanguageSkill,
    Link,
    Membership,
    Project,
    Publication,
    Skill,
    SkillGroup,
)

DATE_WIDGET = forms.DateInput(attrs={"type": "date"})


#: What the fields every kind of entry shares say about themselves (#205).
#:
#: Four models carry a start, an end, a summary and a list of highlights, and none of them
#: inherits another's form. The sentences are the same in each because the behaviour is:
#: a CV prints the month and the year, and prints a highlight as a bullet, wherever the
#: entry came from.
ENTRY_HELP = {
    "start_date": _("A CV prints the month and the year, so the day makes no difference."),
    "end_date": _("Leave it empty while it is still going; a CV then prints “present”."),
    "summary": _("A line or two. It prints under the dates on a CV."),
    "highlights": _("One per line. A CV prints them as bullets."),
}


class ResumeItemForm(OwnerScopedModelForm):
    """An entry's form, with the order number hidden unless the person asked for it.

    The arrows on the overview move an entry past its neighbour (#203), so the number is
    a second control for the same thing, and a strange one: an integer that means "lower
    first", asked of somebody editing a job. It is offered back as a preference under
    Settings > Appearance, for anybody who cannot use the arrows or would rather type,
    which is an accessibility choice. Every kind of entry lists ``order`` in its fields
    and this takes it off; `item_form.html` draws whatever is left.
    """

    #: The start and end field names of an entry that spans a time, or None for one that
    #: does not. An end before the start is refused for every kind that names them (#621).
    date_range: tuple[str, str] | None = None
    end_before_start_message = _("This is before the start date.")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if "order" in self.fields and not show_order_to(user):
            del self.fields["order"]

    def clean(self):
        cleaned = super().clean()
        if self.date_range:
            start_name, end_name = self.date_range
            start, end = cleaned.get(start_name), cleaned.get(end_name)
            if start and end and end < start:
                self.add_error(end_name, forms.ValidationError(self.end_before_start_message))
        return cleaned


def show_order_to(user) -> bool:
    profile = getattr(user, "profile", None) if user is not None else None
    return bool(profile and profile.show_career_order)


class ExperienceForm(ResumeItemForm):
    date_range = ("start_date", "end_date")

    class Meta:
        model = Experience
        fields = (
            "role",
            "organisation",
            "location",
            "start_date",
            "end_date",
            "summary",
            "highlights",
            "order",
        )
        widgets = {
            "organisation": forms.TextInput(attrs={"list": "company-suggestions"}),
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
            "highlights": forms.Textarea(attrs={"rows": 6}),
        }
        #: `end_date` and `highlights` say their piece on the model already, and a model
        #: form takes that; repeating them here would be the same words twice.
        help_texts = {
            "organisation": _(
                "As a CV should print it. It is linked to the company of that name among "
                "yours, and added to your companies if there is none."
            ),
            "start_date": ENTRY_HELP["start_date"],
            "summary": ENTRY_HELP["summary"],
            "location": _("Where the work was, as you would write it on a CV."),
        }

    @property
    def datalists(self) -> dict[str, list[str]]:
        """The person's own companies, former employers included (#683)."""
        if self.user is None:
            return {}
        from postulo.jobs import recall

        return {"company-suggestions": recall.companies(self.user, including_career=True)}

    def save(self, commit=True):
        """Link the organisation to a company of the person's own, adding one if need be."""
        from . import companies

        entry = super().save(commit=False)
        owner = entry.owner if entry.owner_id else self.user
        entry.company = companies.find_or_add(owner, entry.organisation) if owner else None
        if commit:
            entry.save()
            self.save_m2m()
        return entry


class EducationForm(ResumeItemForm):
    date_range = ("start_date", "end_date")

    class Meta:
        model = Education
        fields = (
            "qualification",
            "institution",
            "field_of_study",
            "location",
            "start_date",
            "end_date",
            "grade",
            "eqf_level",
            "highlights",
            "order",
        )
        widgets = {
            "institution": forms.TextInput(attrs={"list": "school-suggestions"}),
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "highlights": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "institution": _(
                "As a CV should print it. It is linked to the company of that name among "
                "yours, and added to your companies if there is none."
            ),
            "start_date": ENTRY_HELP["start_date"],
            "end_date": ENTRY_HELP["end_date"],
            "highlights": ENTRY_HELP["highlights"],
            "field_of_study": _("What it was in, where the qualification's name does not say."),
            "grade": _(
                "As the institution words it. Kept in your record; a CV does not print it yet."
            ),
            "eqf_level": _(
                "The level of the qualification in the European Qualifications Framework, "
                "which a diploma or its supplement may state. Postulo never works it out "
                "from the qualification’s name."
            ),
            "location": _("Where you studied, as you would write it on a CV."),
        }

    #: The company this save added, for the view to say so (#685).
    added_school = None

    @property
    def datalists(self) -> dict[str, list[str]]:
        """The person's companies, the places of learning first (#685)."""
        if self.user is None:
            return {}
        from postulo.jobs import recall

        return {"school-suggestions": recall.schools(self.user)}

    def save(self, commit=True):
        """Link the institution to a company of the person's own, adding one if need be."""
        from . import companies

        entry = super().save(commit=False)
        owner = entry.owner if entry.owner_id else self.user
        # An entry whose institution was not touched keeps its link: its company may have been
        # renamed since, and the old text must not add a second one.
        if entry.company_id and "institution" not in self.changed_data:
            added = False
        else:
            entry.company, added = (
                companies.find_or_add_school(owner, entry.institution) if owner else (None, False)
            )
        self.added_school = entry.company if added else None
        if commit:
            entry.save()
            self.save_m2m()
        return entry


class ProjectForm(ResumeItemForm):
    date_range = ("start_date", "end_date")

    class Meta:
        model = Project
        fields = ("name", "role", "url", "start_date", "end_date", "summary", "highlights", "order")
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
            "highlights": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "start_date": ENTRY_HELP["start_date"],
            "end_date": ENTRY_HELP["end_date"],
            "summary": ENTRY_HELP["summary"],
            "highlights": ENTRY_HELP["highlights"],
            "role": _("What you did on it — “maintainer”, “contributor”, “designer”."),
            "url": _("Where it can be seen. A CV prints it beside the name."),
        }


class SkillGroupForm(ResumeItemForm):
    class Meta:
        model = SkillGroup
        fields = ("name", "order")


class SkillForm(ResumeItemForm):
    class Meta:
        model = Skill
        fields = ("name", "group", "order")
        widgets = {
            # A plain text box, which is what it is with scripts off: the name is matched
            # against the ESCO skills when it is saved (#266). With them on, htmx asks for
            # the classification's names that begin with what has been typed and puts them
            # in the box's `<datalist>`, which `c-field` draws empty for it to fill: fourteen
            # thousand names are too many to send with the page. The box's own value goes
            # with the request, under its own name, and nothing else does.
            "name": forms.TextInput(
                attrs={
                    "list": "skill-suggestions",
                    "autocomplete": "off",
                    "hx-get": reverse_lazy("resume:skill_suggestions"),
                    "hx-trigger": "input changed delay:250ms",
                    "hx-target": "#skill-suggestions",
                    "hx-swap": "innerHTML",
                    "hx-sync": "this:replace",
                }
            ),
        }
        help_texts = {
            "name": _(
                "As a CV should print it. A name that is a skill in the ESCO classification is "
                "recognised as that skill, and the box offers the classification's names as "
                "you type; a skill that is in no classification is not a lesser kind of skill."
            ),
            "group": _(
                "How it is gathered on a CV, which prints each group as “Group: one, two, "
                "three”. Groups are made on the career page."
            ),
        }

    def scope_querysets(self) -> None:
        self.fields["group"].queryset = SkillGroup.objects.for_user(self.user)
        # Every page draws a skill through its group, so one saved without is shown nowhere
        # (#617).
        self.fields["group"].required = True


class CertificationForm(ResumeItemForm):
    date_range = ("issued_on", "expires_on")
    end_before_start_message = _("This is before the date it was issued.")

    class Meta:
        model = Certification
        fields = ("name", "issuer", "issued_on", "expires_on", "credential_url", "order")
        widgets = {
            "issuer": forms.TextInput(attrs={"list": "issuer-suggestions"}),
            "issued_on": DATE_WIDGET,
            "expires_on": DATE_WIDGET,
        }
        help_texts = {
            "issuer": _(
                "Who awarded it, as a CV should print it. It is linked to the company of that "
                "name among yours, and added to your companies if there is none."
            ),
            "expires_on": _("Leave it empty if it does not expire."),
            "credential_url": _("Where somebody can check it. A CV prints it as a link."),
        }

    @property
    def datalists(self) -> dict[str, list[str]]:
        """The person's companies, awarding bodies first and every other one below (#686)."""
        if self.user is None:
            return {}
        from postulo.jobs import recall, roles

        return {
            "issuer-suggestions": recall.companies(
                self.user, including_career=True, role=roles.AWARDING_BODY
            )
        }

    def save(self, commit=True):
        """Link the issuer to a company of the person's own, adding one if need be.

        An entry with no issuer is linked to nothing, and no industry is ever assigned to a
        company added here: a school can be classified from the form it was typed into, a
        vendor or a professional body cannot (#686).
        """
        from . import companies

        entry = super().save(commit=False)
        owner = entry.owner if entry.owner_id else self.user
        entry.company = companies.find_or_add(owner, entry.issuer) if owner else None
        if commit:
            entry.save()
            self.save_m2m()
        return entry


class DrivingLicenceForm(ResumeItemForm):
    """A driving licence: a country, the categories it holds as the codes, two dates.

    The categories are a fieldset of checkboxes, and none implies another. There is no box
    for a number, a photograph or anything else a licence carries, and there never will be:
    see `DrivingLicence` and `docs/THREAT-MODEL.md` (#691).
    """

    date_range = ("first_issued_on", "expires_on")
    end_before_start_message = _("This is before the date it was first issued.")

    country = forms.ChoiceField(
        label=_("Country"),
        required=False,
        choices=[("", _("Not stated")), *phones.country_choices()],
        widget=postal.CountrySelect,
        help_text=_(
            "Filled in, a CV prints it in brackets after the categories, since a British or "
            "a Brazilian B is not an EU B. Leave it blank to leave it off."
        ),
    )
    categories = forms.MultipleChoiceField(
        label=_("Categories"),
        required=False,
        choices=[],
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Tick each category the licence lists: none implies another, so somebody who holds "
            "A ticks A. A CV prints the codes."
        ),
    )

    class Meta:
        model = DrivingLicence
        fields = (
            "country",
            "categories",
            "other_categories",
            "first_issued_on",
            "expires_on",
            "order",
        )
        widgets = {"first_issued_on": DATE_WIDGET, "expires_on": DATE_WIDGET}
        help_texts = {
            "other_categories": _(
                "National letters or classes the list lacks, as the licence prints them. "
                "A CV prints them after the codes."
            ),
            "first_issued_on": _("Not printed on a CV."),
            "expires_on": _("Leave it empty if it does not expire. Not printed on a CV."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categories"].choices = driving.choices()

    def clean_categories(self) -> list[str]:
        return driving.ordered(self.cleaned_data["categories"])

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("categories") and not (cleaned.get("other_categories") or "").strip():
            if "categories" not in self.errors:
                self.add_error(
                    "categories",
                    forms.ValidationError(
                        _("Tick a category, or write the national one below."), code="required"
                    ),
                )
        return cleaned


#: What the language menu says for a language that is not in it: the name is typed.
OTHER_LANGUAGE = "other"


class SpokenLanguageSelect(forms.Select):
    """The menu of spoken languages, each named in the language Postulo is being read in.

    Unlike `LanguageSelect`, an option claims no ``lang``: its words are in the interface
    language, not in the language it names. It carries the flag where there is one, for the
    control `app.js` builds beside it (#301), and none where there is none.
    """

    def create_option(self, name, value, *args, **kwargs):
        from postulo.core.flags import flag_url

        option = super().create_option(name, value, *args, **kwargs)
        code = str(value or "")
        country = languages.flag_country(languages.match(code) or code) if code else ""
        if country and code != OTHER_LANGUAGE:
            option["attrs"]["data-flag"] = flag_url(country)
        return option


class SpokenLanguageField(LanguageChoiceField):
    """A language from the list, *Other*, or any well-formed code a file or an older page
    carries: a code the list does not hold is kept and shown as it is (#689)."""

    def valid_value(self, value):
        return value == OTHER_LANGUAGE or languages.well_formed(value)

    def to_python(self, value):
        value = super().to_python(value)
        return value if value == OTHER_LANGUAGE else languages.tag(value)


class LanguageSkillForm(ResumeItemForm):
    """A spoken language, chosen from the list; *Other* asks for the name instead.

    The menu is the native select without scripts and Basecoat's with them, and the box for
    the name shows only while *Other* is chosen, by the rule the kinds of phone number and
    the form of address already use (`data-if-other`). A chosen language fills `name` in the
    language the career record is written in, so anything that reads a plain name has one.
    """

    code = SpokenLanguageField(label=_("Language"), required=False, widget=SpokenLanguageSelect)

    class Meta:
        model = LanguageSkill
        fields = ("code", "name", "proficiency", "order")
        labels = {"name": _("Name of the language")}
        help_texts = {
            "proficiency": _(
                "The Common European Framework levels. “Not stated” prints nothing at all, "
                "which is the honest answer where you have never been tested — a level is a "
                "claim somebody may test in an interview."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        held = languages.tag(self.instance.code)
        shown = languages.current()
        choices = [("", _("Choose a language"))]
        choices += list(language_names.listing(shown))
        if held and held not in {code for code, _name in choices}:
            choices.append((held, language_names.name(held, shown)))
        choices.append((OTHER_LANGUAGE, _("Other…")))
        self.fields["code"].choices = choices
        if self.instance.pk and not held:
            self.initial["code"] = OTHER_LANGUAGE
        else:
            self.initial["code"] = held

    def clean(self):
        cleaned = super().clean()
        code = cleaned.get("code", "")
        name = (cleaned.get("name") or "").strip()
        if "code" in self.errors:
            return cleaned
        if code and code != OTHER_LANGUAGE:
            record = translatable.record_language_of(self.user)
            cleaned["name"] = language_names.name(code, record)[:100]
        else:
            cleaned["code"] = ""
            if not name:
                self.add_error(
                    "code" if not code else "name",
                    forms.ValidationError(
                        _("Choose a language, or Other and write its name.")
                        if not code
                        else _("Write the name of the language."),
                        code="required",
                    ),
                )
        return cleaned


class HonourForm(ResumeItemForm):
    """A prize or a distinction (#693). Nothing here is checked against anybody."""

    class Meta:
        model = Honour
        fields = ("title", "awarded_by", "awarded_on", "summary", "url", "order")
        widgets = {
            "awarded_by": forms.TextInput(attrs={"list": "awarding-body-suggestions"}),
            "awarded_on": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "title": _(
                "A prize, a scholarship, a “best paper”: recognition, with nothing to check and "
                "no expiry. A credential that may lapse is a certification, and a degree’s "
                "classification, such as “cum laude”, is that education entry’s grade."
            ),
            "awarded_by": _(
                "Who gave it, as a CV should print it. It is linked to the company of that name "
                "among yours, and added to your companies if there is none."
            ),
            "awarded_on": _("Only the year prints on a CV, so the day makes no difference."),
            "summary": ENTRY_HELP["summary"],
            "url": _("Where it can be read about. A CV does not print it."),
        }

    @property
    def datalists(self) -> dict[str, list[str]]:
        """The person's companies, awarding bodies first and every other one below (#693)."""
        if self.user is None:
            return {}
        from postulo.jobs import recall, roles

        return {
            "awarding-body-suggestions": recall.companies(
                self.user, including_career=True, role=roles.AWARDING_BODY
            )
        }

    def save(self, commit=True):
        """Link the giver to a company of the person's own, adding one if need be.

        No industry is ever assigned to a company added here: who gave a prize is not a
        statement about what the giver does (#693, as #686's issuers).
        """
        from . import companies

        entry = super().save(commit=False)
        owner = entry.owner if entry.owner_id else self.user
        entry.company = companies.find_or_add(owner, entry.awarded_by) if owner else None
        if commit:
            entry.save()
            self.save_m2m()
        return entry


class MembershipForm(ResumeItemForm):
    """A body somebody belongs or belonged to (#693)."""

    date_range = ("start_date", "end_date")

    class Meta:
        model = Membership
        fields = ("organisation", "role", "start_date", "end_date", "summary", "url", "order")
        widgets = {
            "organisation": forms.TextInput(attrs={"list": "membership-suggestions"}),
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "organisation": _(
                "The association, society or club, as a CV should print it. It is linked to "
                "the company of that name among yours, and added to your companies if there "
                "is none. A membership of a union, a party or a congregation says something "
                "about you, and is yours to leave off any CV; nothing here asks for one."
            ),
            "role": _("What you are or were in it, if anything: “member”, “treasurer”."),
            "start_date": _("A CV prints only the year, so the day makes no difference."),
            "end_date": _("Leave it empty while it is still going; a CV then prints “since”."),
            "summary": ENTRY_HELP["summary"],
            "url": _("Where it can be read about. A CV does not print it."),
        }

    @property
    def datalists(self) -> dict[str, list[str]]:
        """The person's companies, membership organisations first and every other below (#693)."""
        if self.user is None:
            return {}
        from postulo.jobs import recall, roles

        return {
            "membership-suggestions": recall.companies(
                self.user, including_career=True, role=roles.MEMBERSHIP_ORGANISATION
            )
        }

    def save(self, commit=True):
        """Link the organisation to a company of the person's own, adding one if need be."""
        from . import companies

        entry = super().save(commit=False)
        owner = entry.owner if entry.owner_id else self.user
        entry.company = companies.find_or_add(owner, entry.organisation) if owner else None
        if commit:
            entry.save()
            self.save_m2m()
        return entry


class LinkForm(ResumeItemForm):
    class Meta:
        model = Link
        fields = ("title", "url", "kind", "description", "order")
        help_texts = {
            "kind": _(
                "What sort of thing it is, never who hosts it: a portfolio is a portfolio "
                "wherever it lives."
            ),
            "description": _("A few words about it, where the title does not say enough."),
        }


class PublicationForm(ResumeItemForm):
    """A publication, with the fields its type uses (#687).

    Every field is on the form, in the page, and the ones the type does not use are drawn
    hidden (`type_wrappers`): hidden and not refused, so what was typed under one type is
    still there under the next and is saved with the rest. `app.js` shows and hides them as
    the type is chosen; with scripts off the page is drawn for the type it was opened with,
    and a changed type takes effect when it is saved.

    What BibTeX asks of the type and the entry lacks is a hint (`missing_labels`), never an
    error: somebody may hold nothing but a title.
    """

    class Meta:
        model = Publication
        fields = (
            "entry_type",
            "title",
            "authors",
            "editors",
            "container_title",
            "publisher",
            "institution",
            "location",
            "date",
            "volume",
            "number",
            "pages",
            "edition",
            "series",
            "chapter",
            "doi",
            "url",
            "note",
            "language",
            "cite_key",
            "order",
        )
        widgets = {
            # Read by `app.js`, which shows and hides the fields that follow the type.
            "entry_type": forms.Select(attrs={"data-type-select": ""}),
            "authors": forms.Textarea(attrs={"rows": 3}),
            "editors": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {
            "entry_type": _(
                "What sort of work it is. The fields below follow the type: choosing another "
                "shows the ones it uses, and what you typed under the old one is kept. With "
                "scripts off the change shows once you have saved."
            ),
            "title": _("As it was published."),
            "publisher": _("Who published it: a press, a journal's publisher, a repository."),
            "institution": _("The university, laboratory or body behind it: a thesis's school."),
            "location": _("Where it was published, or the conference's city."),
            "volume": _("The volume, as the publisher numbers it."),
            "number": _("The issue or report number."),
            "pages": _("A range such as 41–52, or one page."),
            "edition": _("The edition or version, as it says on it."),
            "series": _("The series it belongs to, if any."),
            "chapter": _("The chapter's number or name, where you are citing a chapter."),
            "note": _("Anything a reader should know, in a few words."),
            "authors": _("One name per line, as you would write it: “Knuth, Donald E.”."),
            "editors": _("One name per line, as for the authors."),
            "container_title": _("The journal, the book or the proceedings it appeared in."),
            "date": _("A year, a year and month, or a whole date: 2024, 2024-05 or 2024-05-17."),
            "doi": _(
                "Only checked for its shape and linked on a CV as https://doi.org/…; Postulo "
                "never looks it up."
            ),
            "url": _("Where it can be read, where it has no DOI."),
            "language": _("The language the work is written in."),
            "cite_key": _(
                "What a bibliography file would call it. Letters, digits and - _ : . + / only, "
                "one of yours at most; left empty, Postulo makes one. Never printed."
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        from postulo.accounts.forms import LanguageSelect, with_what_is_held
        from postulo.core import languages

        field = self.fields["language"]
        field.required = False
        choices = [("", _("Not stated")), *languages.LANGUAGES]
        field.widget = LanguageSelect(choices=with_what_is_held(choices, self["language"].value()))

    @property
    def current_type(self) -> str:
        """The type the form is drawn for: what was posted, or what the entry has."""
        value = self["entry_type"].value()
        return value if value in publications.TYPES else publications.DEFAULT_TYPE

    @property
    def type_wrappers(self) -> dict[str, dict]:
        """For each field that follows the type: which types use it, and whether it is
        hidden for the one in force."""
        current = set(publications.fields_for(self.current_type))
        return {
            name: {"types": " ".join(publications.types_using(name)), "hidden": name not in current}
            for name in self.fields
            if name in publications.FIELD_ORDER or name in publications.COMMON
        }

    def clean_doi(self) -> str:
        return publications.normalise_doi(self.cleaned_data.get("doi"))

    def clean_cite_key(self) -> str:
        key = self.cleaned_data.get("cite_key", "")
        owner = self.user if self.user is not None else getattr(self.instance, "owner", None)
        if key and owner is not None:
            clash = Publication.objects.for_user(owner).filter(cite_key=key)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError(
                    _("You already have a publication with this key."), code="unique"
                )
        return key

    def missing_labels(self) -> list[str]:
        """What BibTeX asks of this type that the saved entry does not say, by label."""
        lacking = publications.missing_for(
            self.cleaned_data.get("entry_type", ""), self.cleaned_data.get
        )
        return [str(self.fields[name].label or name) for name in lacking if name in self.fields]


class TranslationForm(forms.Form):
    """What one entry says in one other language.

    Built from `translatable.TRANSLATABLE` rather than declared, so the decision about which
    fields may be said differently lives in one place and this screen cannot quietly offer a
    field that decision left out (#131).

    Every box is optional, and that is the feature. A job title is often the only thing
    worth translating; leaving the summary empty means the original prints, which is what
    somebody who left it empty meant.
    """

    def __init__(self, *args, entry, language: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.entry = entry
        self.language = language
        self.originals: dict[str, str] = {}
        for name in translatable.fields_for(entry):
            model_field = entry._meta.get_field(name)
            long = isinstance(model_field, django_models.TextField)
            self.fields[name] = forms.CharField(
                label=model_field.verbose_name,
                required=False,
                widget=(
                    forms.Textarea(attrs={"rows": 4 if name != "highlights" else 6})
                    if long
                    else forms.TextInput()
                ),
            )
            self.originals[name] = str(getattr(entry, name, "") or "")

    def rows(self):
        """Each box beside the text it is a translation of."""
        for name in self.fields:
            yield self[name], self.originals.get(name, "")

    def save(self) -> int:
        """Write what was typed, and blank what was cleared. Returns how many say something.

        A cleared box leaves an empty row rather than deleting it: `translatable.stored_for`
        already reads blank as withdrawn, and a form that deletes rows on save is a form
        that loses somebody's work to a mis-click on a field they never opened.
        """
        from django.contrib.contenttypes.models import ContentType

        from .models import Translation

        content_type = ContentType.objects.get_for_model(self.entry.__class__)
        kept = 0
        for name in self.fields:
            text = (self.cleaned_data.get(name) or "").strip()
            kept += bool(text)
            Translation.objects.update_or_create(
                content_type=content_type,
                object_id=self.entry.pk,
                language=self.language,
                field=name,
                defaults={"text": text, "owner": self.entry.owner},
            )
        return kept


class AddLanguageForm(forms.Form):
    """Which language to start translating an entry into."""

    language = forms.ChoiceField(label=_("Language"), choices=())

    def __init__(self, *args, exclude=(), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from postulo.accounts.forms import LanguageSelect
        from postulo.core import languages

        taken = list(exclude)
        choices = [
            (code, name) for code, name in languages.LANGUAGES if not languages.find(code, taken)
        ]
        self.fields["language"].choices = choices
        self.fields["language"].widget = LanguageSelect(choices=choices)
