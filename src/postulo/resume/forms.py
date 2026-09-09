"""Forms for the career record."""

from __future__ import annotations

from django import forms
from django.db import models as django_models
from django.utils.translation import gettext_lazy as _

from postulo.jobs.forms import OwnerScopedModelForm

from . import translating
from .models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
    Project,
    Skill,
    SkillGroup,
)

DATE_WIDGET = forms.DateInput(attrs={"type": "date"})


class ExperienceForm(OwnerScopedModelForm):
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
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
            "highlights": forms.Textarea(attrs={"rows": 6}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", forms.ValidationError("This is before the start date."))
        return cleaned


class EducationForm(OwnerScopedModelForm):
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
            "highlights",
            "order",
        )
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "highlights": forms.Textarea(attrs={"rows": 4}),
        }


class ProjectForm(OwnerScopedModelForm):
    class Meta:
        model = Project
        fields = ("name", "role", "url", "start_date", "end_date", "summary", "highlights", "order")
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "summary": forms.Textarea(attrs={"rows": 3}),
            "highlights": forms.Textarea(attrs={"rows": 4}),
        }


class SkillGroupForm(OwnerScopedModelForm):
    class Meta:
        model = SkillGroup
        fields = ("name", "order")


class SkillForm(OwnerScopedModelForm):
    class Meta:
        model = Skill
        fields = ("name", "group", "order")

    def scope_querysets(self) -> None:
        self.fields["group"].queryset = SkillGroup.objects.for_user(self.user)


class CertificationForm(OwnerScopedModelForm):
    class Meta:
        model = Certification
        fields = ("name", "issuer", "issued_on", "expires_on", "credential_url", "order")
        widgets = {"issued_on": DATE_WIDGET, "expires_on": DATE_WIDGET}


class LanguageSkillForm(OwnerScopedModelForm):
    class Meta:
        model = LanguageSkill
        fields = ("name", "proficiency", "order")


class LinkForm(OwnerScopedModelForm):
    class Meta:
        model = Link
        fields = ("title", "url", "kind", "description", "order")


class TranslationForm(forms.Form):
    """What one entry says in one other language.

    Built from `translating.TRANSLATABLE` rather than declared, so the decision about which
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
        for name in translating.fields_for(entry):
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

        A cleared box leaves an empty row rather than deleting it: `translating.stored_for`
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

        taken = {translating.normalise(code) for code in exclude}
        choices = [
            (code, name)
            for code, name in languages.LANGUAGES
            if translating.normalise(code) not in taken
        ]
        self.fields["language"].choices = choices
        self.fields["language"].widget = LanguageSelect(choices=choices)
