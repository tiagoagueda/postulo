"""Forms for CVs, cover letters and uploads."""

from __future__ import annotations

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from postulo.jobs.forms import OwnerScopedModelForm
from postulo.resume.models import Link
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

from .models import (
    CV,
    LETTER_STARTERS,
    LETTER_THEMES,
    CoverLetter,
    CVItem,
    LetterKind,
    Theme,
    UploadedDocument,
)

#: Maximum size for an upload, in bytes. Generous for a CV, mean for a video.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def language_choices() -> list[tuple[str, str]]:
    """The languages a document may declare, with blank meaning "whatever I read in".

    A list rather than a text box because the value ends up in the ``lang`` of a PDF that
    gets sent to somebody, and a mistyped tag is worse than none: a screen reader will
    happily read Portuguese with the rules of whatever ``pt_PT`` or ``portuguese`` failed
    to parse as. The choices are the instance's own languages, each under its own name.
    """
    from postulo.core import languages

    return [("", _("Follow your profile")), *languages.LANGUAGES]


def language_widget() -> forms.Select:
    """The same menu the profile uses, so an option says which language it is in."""
    from postulo.accounts.forms import LanguageSelect

    return LanguageSelect(choices=language_choices())


class LanguageChoiceMixin:
    """Turn the model's free-text ``language`` field into a picker."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("language")
        if field is not None:
            field.widget = language_widget()
            field.required = False


class CVForm(LanguageChoiceMixin, OwnerScopedModelForm):
    class Meta:
        model = CV
        fields = ("name", "headline", "summary", "theme", "language", "show_contact_details")
        widgets = {"summary": forms.Textarea(attrs={"rows": 4})}

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if self.user is None:
            return name
        clash = CV.objects.for_user(self.user).filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("You already have a CV with that name."))
        return name


class CVItemForm(OwnerScopedModelForm):
    """Edit one entry's placement on one CV."""

    class Meta:
        model = CVItem
        fields = ("is_included", "override_highlights", "order")
        widgets = {"override_highlights": forms.Textarea(attrs={"rows": 6})}


class AddCVItemsForm(forms.Form):
    """Choose which career entries to put on a CV.

    Presented as one grouped list of checkboxes rather than a picker per kind, because
    building a variant is one decision — "what goes on this one?" — not six.
    """

    def __init__(self, *args, cv: CV, **kwargs):
        super().__init__(*args, **kwargs)
        self.cv = cv
        self.groups = []

        already_on_cv = {(item.content_type_id, item.object_id) for item in cv.items.all()}

        for slug in OVERVIEW_ORDER:
            spec = SECTIONS[slug]
            content_type = ContentType.objects.get_for_model(spec.model)
            available = [
                obj
                for obj in spec.model.objects.for_user(cv.owner)
                if (content_type.id, obj.pk) not in already_on_cv
            ]
            if not available:
                continue

            field_name = f"add_{slug.replace('-', '_')}"
            self.fields[field_name] = forms.MultipleChoiceField(
                label=spec.plural,
                required=False,
                choices=[(obj.pk, str(obj)) for obj in available],
                widget=forms.CheckboxSelectMultiple,
            )
            # The bound field, not its name: a template cannot look a field up by a
            # variable, and inventing a filter to do so would be worse than this.
            self.groups.append(
                {"field": self[field_name], "name": field_name, "label": spec.plural, "slug": slug}
            )

    def selected(self) -> list[tuple[ContentType, int]]:
        """The chosen entries, as content type and primary key pairs."""
        chosen: list[tuple[ContentType, int]] = []
        for group in self.groups:
            spec = SECTIONS[group["slug"]]
            content_type = ContentType.objects.get_for_model(spec.model)
            for raw_pk in self.cleaned_data.get(group["name"], []):
                chosen.append((content_type, int(raw_pk)))
        return chosen


class CoverLetterForm(LanguageChoiceMixin, OwnerScopedModelForm):
    """A letter of any of the four kinds.

    A new letter starts from the kind's own text rather than an empty box: what a
    motivation letter is supposed to look like is not obvious, and a shape on the page
    says it better than help text underneath.
    """

    class Meta:
        model = CoverLetter
        fields = ("name", "kind", "subject", "body", "theme", "is_template", "language")
        widgets = {"body": forms.Textarea(attrs={"rows": 18})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            return
        kind = self.initial.get("kind") or LetterKind.COVER
        self.initial.setdefault("kind", kind)
        self.initial.setdefault("body", str(LETTER_STARTERS.get(kind, "")))
        self.initial.setdefault("theme", LETTER_THEMES.get(kind, Theme.PLAIN))


class UploadedDocumentForm(OwnerScopedModelForm):
    class Meta:
        model = UploadedDocument
        fields = ("title", "kind", "file", "notes", "replaces")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def scope_querysets(self) -> None:
        queryset = UploadedDocument.objects.for_user(self.user)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        self.fields["replaces"].queryset = queryset
        self.fields["replaces"].label = _("Supersedes")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded and uploaded.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                _("That file is larger than %(limit)s MB.")
                % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024)}
            )
        return uploaded

    def save(self, commit: bool = True) -> UploadedDocument:
        document = super().save(commit=False)
        # A new version numbers itself from the one it supersedes, so the history reads
        # in order without anyone having to keep count.
        if document.replaces_id and not self.instance.pk:
            document.version = document.replaces.version + 1
        if commit:
            document.save()
        return document


class SendDocumentsForm(forms.Form):
    """Choose what to send with an application, and freeze it."""

    cv = forms.ModelChoiceField(label=_("CV"), queryset=CV.objects.none(), required=False)
    cover_letter = forms.ModelChoiceField(
        label=_("Letter"), queryset=CoverLetter.objects.none(), required=False
    )
    uploads = forms.ModelMultipleChoiceField(
        label=_("Files you have already"),
        queryset=UploadedDocument.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    links = forms.ModelMultipleChoiceField(
        label=_("Links you pointed them at"),
        queryset=Link.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["cv"].queryset = CV.objects.for_user(user)
        self.fields["cover_letter"].queryset = CoverLetter.objects.for_user(user)
        self.fields["uploads"].queryset = UploadedDocument.objects.for_user(user)
        self.fields["links"].queryset = Link.objects.for_user(user)

    def clean(self):
        cleaned = super().clean()
        chosen = (
            cleaned.get("cv"),
            cleaned.get("cover_letter"),
            cleaned.get("uploads"),
            cleaned.get("links"),
        )
        if not any(chosen):
            raise forms.ValidationError(_("Choose at least one document to record."))
        return cleaned
