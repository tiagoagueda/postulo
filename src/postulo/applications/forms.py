"""Forms for applications, timeline entries and reminders."""

from __future__ import annotations

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import Tag
from postulo.jobs.forms import OwnerScopedModelForm
from postulo.jobs.models import Contact, EmploymentType, RemoteType, SalaryPeriod

from .models import Application, ApplicationEvent, Channel, EventKind, Priority, Reminder, Status


class ApplicationIntakeForm(forms.Form):
    """Everything needed to record a new application, on one page.

    An application is almost always entered while looking at a posting, so splitting
    this into "create a company", then "create a posting", then "create an application"
    would be three forms for a single thought. The company is matched by name and
    created if it is new.
    """

    company_name = forms.CharField(label=_("Company"), max_length=200)
    title = forms.CharField(label=_("Job title"), max_length=250)
    url = forms.URLField(label=_("Posting URL"), max_length=500, required=False)
    location = forms.CharField(label=_("Location"), max_length=200, required=False)
    remote_type = forms.ChoiceField(
        label=_("Working arrangement"),
        choices=[("", "—"), *RemoteType.choices],
        required=False,
    )
    employment_type = forms.ChoiceField(
        label=_("Employment type"),
        choices=[("", "—"), *EmploymentType.choices],
        required=False,
    )
    source = forms.CharField(label=_("Found via"), max_length=120, required=False)

    salary_min = forms.DecimalField(
        label=_("Salary from"), required=False, max_digits=12, decimal_places=2
    )
    salary_max = forms.DecimalField(
        label=_("Salary to"), required=False, max_digits=12, decimal_places=2
    )
    salary_currency = forms.CharField(
        label=_("Currency"), max_length=3, required=False, initial="EUR"
    )
    salary_period = forms.ChoiceField(
        label=_("Period"), choices=SalaryPeriod.choices, required=False, initial=SalaryPeriod.YEAR
    )

    closes_at = forms.DateField(
        label=_("Closing date"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    description = forms.CharField(
        label=_("Description"), required=False, widget=forms.Textarea(attrs={"rows": 8})
    )

    status = forms.ChoiceField(label=_("Status"), choices=Status.choices, initial=Status.APPLIED)
    channel = forms.ChoiceField(
        label=_("Applied through"), choices=[("", "—"), *Channel.choices], required=False
    )
    priority = forms.TypedChoiceField(
        label=_("Priority"), choices=Priority.choices, coerce=int, initial=Priority.NORMAL
    )
    deadline = forms.DateField(
        label=_("Your deadline"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    tags = forms.ModelMultipleChoiceField(
        label=_("Tags"), queryset=Tag.objects.none(), required=False
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.for_user(user)

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("salary_min"), cleaned.get("salary_max")
        if low is not None and high is not None and low > high:
            self.add_error("salary_max", _("The upper figure cannot be below the lower one."))
        return cleaned

    #: Posting fields that are stored as text, and are blank rather than null.
    POSTING_TEXT_FIELDS = (
        "title",
        "url",
        "location",
        "remote_type",
        "employment_type",
        "source",
        "salary_currency",
        "salary_period",
        "description",
    )
    #: Posting fields that are genuinely nullable. An empty string is not a decimal or
    #: a date, so these must stay None rather than being flattened with the rest.
    POSTING_NULLABLE_FIELDS = ("salary_min", "salary_max", "closes_at")

    @property
    def posting_data(self) -> dict:
        data = {key: self.cleaned_data.get(key) or "" for key in self.POSTING_TEXT_FIELDS}
        data.update({key: self.cleaned_data.get(key) for key in self.POSTING_NULLABLE_FIELDS})
        return data

    @property
    def application_data(self) -> dict:
        return {
            "status": self.cleaned_data["status"],
            "channel": self.cleaned_data.get("channel") or "",
            "priority": self.cleaned_data.get("priority") or Priority.NORMAL,
            "deadline": self.cleaned_data.get("deadline"),
        }


class ApplicationForm(OwnerScopedModelForm):
    """Edit an existing application.

    ``status`` appears here for convenience, but the view routes any change through
    ``services.change_status`` so that the timeline records it. Saving it silently
    would leave a status the log cannot explain.
    """

    class Meta:
        model = Application
        fields = ("status", "channel", "priority", "deadline", "contact", "tags")
        widgets = {"deadline": forms.DateInput(attrs={"type": "date"})}

    def scope_querysets(self) -> None:
        self.fields["tags"].queryset = Tag.objects.for_user(self.user)
        contacts = Contact.objects.for_user(self.user).select_related("company")
        if self.instance.pk:
            # The contacts worth offering are the ones at this company.
            contacts = contacts.filter(company=self.instance.posting.company_id)
        self.fields["contact"].queryset = contacts


class StatusChangeForm(forms.Form):
    """The quick status action used from the board and the detail page."""

    status = forms.ChoiceField(label=_("Status"), choices=Status.choices)
    note = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class EventForm(forms.ModelForm):
    """Add an entry to the timeline."""

    class Meta:
        model = ApplicationEvent
        fields = ("kind", "occurred_at", "summary", "body")
        widgets = {
            "occurred_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "body": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["occurred_at"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        self.fields["occurred_at"].initial = timezone.localtime()
        # A status change is the record of something the application did, not something
        # to be typed by hand; offering it here would let the log contradict the field.
        self.fields["kind"].choices = [
            (value, label) for value, label in EventKind.choices if value != EventKind.STATUS_CHANGE
        ]


class ReminderForm(OwnerScopedModelForm):
    class Meta:
        model = Reminder
        fields = ("summary", "due_at", "application")
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
        }

    def scope_querysets(self) -> None:
        self.fields["application"].queryset = Application.objects.for_user(
            self.user
        ).with_display_data()
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]


class TagForm(OwnerScopedModelForm):
    class Meta:
        model = Tag
        fields = ("name", "colour")

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if self.user is None:
            return name
        clash = Tag.objects.for_user(self.user).filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("You already have a tag with that name."))
        return name
