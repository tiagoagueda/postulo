"""Forms for the career record."""

from __future__ import annotations

from django import forms

from postulo.jobs.forms import OwnerScopedModelForm

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
