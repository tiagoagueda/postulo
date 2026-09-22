"""Forms for applications, timeline entries and reminders."""

from __future__ import annotations

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.csv_import import OFFERED_CURRENCIES
from postulo.core.models import Tag, TagColour, TagIcon
from postulo.jobs import recall
from postulo.jobs.forms import POSTING_HELP, OwnerScopedModelForm
from postulo.jobs.models import (
    Contact,
    EmploymentType,
    JobPosting,
    RemoteType,
    SalaryPeriod,
)

from .models import (
    SYSTEM_EVENT_KINDS,
    Application,
    ApplicationEvent,
    Channel,
    EventKind,
    Interview,
    Offer,
    Priority,
    Reminder,
    Status,
)

#: What the person's own fields say, said once (#205). `ApplicationDetailsForm` declares
#: them as plain fields and `ApplicationForm` is a model form over the same columns, so
#: neither inherits from the other and both read this.
APPLICATION_HELP = {
    "channel": _(
        "The route it went through. The report counts by this, including how many went "
        "through the employment service's own board."
    ),
    "priority": _("Yours, not the employer's. The list can be sorted and narrowed by it."),
    "deadline": _(
        "The date you mean to have sent it by. It is not a reminder — set one of those if "
        "you want telling."
    ),
    "tags": _("Your own words for grouping applications. New ones go in the box below."),
    "contact": _("The person at the company this went through, where there was one."),
}


class UserAwareForm(forms.Form):
    """A form that knows whose it is, so owner-scoped choices can be built."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class PostingIntakeForm(UserAwareForm):
    """The posting half of intake: a company by name, and what the listing says.

    On its own it adds a listing and reviews a capture; :class:`ApplicationIntakeForm`
    adds the person's side to it. Only the company and the title are required — a
    listing can be thin, an application cannot. The company is matched by name and
    created if it is new.
    """

    #: The three boxes that are typed every single time, and until #261 offered nothing.
    #: `autocomplete="off"` turns off the *browser's* memory of what was typed into a box
    #: with this name on any site, which is a different list and a worse one: it is not
    #: scoped to this account, it carries whatever was typed into a box called "location"
    #: somewhere else, and it hides the list that is actually about this person's records.
    company_name = forms.CharField(
        label=_("Company"),
        max_length=200,
        widget=forms.TextInput(attrs={"list": "company-suggestions", "autocomplete": "off"}),
    )
    title = forms.CharField(label=_("Job title"), max_length=250)
    url = forms.URLField(
        label=_("Posting URL"),
        max_length=500,
        required=False,
        help_text=POSTING_HELP["url"],
    )
    location = forms.CharField(
        label=_("Location"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"list": "location-suggestions", "autocomplete": "off"}),
        help_text=POSTING_HELP["location"],
    )
    remote_type = forms.ChoiceField(
        label=_("Working arrangement"),
        choices=[("", "—"), *RemoteType.choices],
        required=False,
        help_text=POSTING_HELP["remote_type"],
    )
    employment_type = forms.ChoiceField(
        label=_("Employment type"),
        choices=[("", "—"), *EmploymentType.choices],
        required=False,
        help_text=POSTING_HELP["employment_type"],
    )
    source = forms.CharField(
        label=_("Found via"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"list": "source-suggestions", "autocomplete": "off"}),
        # The same sentence the model carries, so the two forms that ask for this read
        # alike. `JobPostingForm` takes it from the model without being told.
        help_text=JobPosting._meta.get_field("source").help_text,
    )

    salary_min = forms.DecimalField(
        label=_("Salary from"),
        required=False,
        max_digits=12,
        decimal_places=2,
        help_text=POSTING_HELP["salary_min"],
    )
    salary_max = forms.DecimalField(
        label=_("Salary to"),
        required=False,
        max_digits=12,
        decimal_places=2,
        help_text=POSTING_HELP["salary_max"],
    )
    salary_currency = forms.CharField(
        label=_("Currency"),
        max_length=3,
        required=False,
        initial="EUR",
        help_text=POSTING_HELP["salary_currency"],
    )
    salary_period = forms.ChoiceField(
        label=_("Period"),
        choices=SalaryPeriod.choices,
        required=False,
        initial=SalaryPeriod.YEAR,
        help_text=POSTING_HELP["salary_period"],
    )

    @property
    def datalists(self) -> dict[str, list[str]]:
        """What each `<datalist>` on this form offers: ``id -> values`` (#261).

        Read by `c-field`, which draws the list beside any widget carrying a ``list``
        attribute, so the three templates that render this form -- intake, capture review
        and the listing form -- get it without any of them knowing.

        Built from *this person's* records and nobody else's. A form with no user attached
        offers nothing rather than everything, which is the safe way round: every route
        here passes one, and a route that forgets should suggest nothing rather than leak.
        """
        if self.user is None:
            return {}
        return {
            "company-suggestions": recall.companies(self.user),
            "location-suggestions": recall.locations(self.user),
            "source-suggestions": recall.sources(self.user),
        }

    closes_at = forms.DateField(
        label=_("Closing date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=POSTING_HELP["closes_at"],
    )
    description = forms.CharField(
        label=_("Description"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=POSTING_HELP["description"],
    )

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


class ApplicationDetailsForm(UserAwareForm):
    """The person's side of an application: status, channel, priority, deadline, tags.

    On its own it is the *Apply* step for a listing that already exists.
    """

    status = forms.ChoiceField(label=_("Status"), choices=Status.choices, initial=Status.APPLIED)
    channel = forms.ChoiceField(
        label=_("Applied through"),
        choices=[("", "—"), *Channel.choices],
        required=False,
        help_text=APPLICATION_HELP["channel"],
    )
    priority = forms.TypedChoiceField(
        label=_("Priority"),
        choices=Priority.choices,
        coerce=int,
        initial=Priority.NORMAL,
        help_text=APPLICATION_HELP["priority"],
    )
    applied_on = forms.DateField(
        label=_("Applied on"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_(
            "When you actually sent it. Left empty it is today, which is wrong for a search "
            "already under way — and reply times, the months and the report are measured "
            "from this date."
        ),
    )
    deadline = forms.DateField(
        label=_("Your deadline"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=APPLICATION_HELP["deadline"],
    )
    tags = forms.ModelMultipleChoiceField(
        label=_("Tags"),
        queryset=Tag.objects.none(),
        required=False,
        help_text=APPLICATION_HELP["tags"],
    )
    new_tags = forms.CharField(
        label=_("New tags"),
        required=False,
        help_text=_("Separate them with commas. A tag you have already keeps its colour."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.for_user(self.user)

    def chosen_tags(self) -> list:
        """The tags this form asked for: the ones ticked, plus any typed that did not exist.

        A control that offers to add a label somebody has not used before needs somewhere
        for the label to land, and `Tag.named` is where -- matched by slug, so *Remote* and
        *remote* stay one tag (#139).
        """
        typed = Tag.named(self.user, Tag.split(self.cleaned_data.get("new_tags", "")))
        chosen = list(self.cleaned_data.get("tags") or [])
        return chosen + [tag for tag in typed if tag not in chosen]

    @property
    def application_data(self) -> dict:
        from .services import moment_for

        return {
            "status": self.cleaned_data["status"],
            "channel": self.cleaned_data.get("channel") or "",
            "priority": self.cleaned_data.get("priority") or Priority.NORMAL,
            "deadline": self.cleaned_data.get("deadline"),
            # Carried through `apply_to_listing`, which takes it out again and hands it to the
            # status change, so the date, the stamp and the timeline entry all agree (#222).
            "applied_at": moment_for(self.cleaned_data.get("applied_on")),
        }


class ApplicationIntakeForm(PostingIntakeForm, ApplicationDetailsForm):
    """Everything needed to record a new application, on one page.

    An application is almost always entered while looking at a posting, so splitting
    this into "create a company", then "create a posting", then "create an application"
    would be three forms for a single thought. Underneath, it is a listing and then an
    application for it, exactly as if the two steps had been taken apart.
    """


class ApplicationForm(OwnerScopedModelForm):
    """Edit an existing application.

    ``status`` appears here for convenience, but the view routes any change through
    ``services.change_status`` so that the timeline records it. Saving it silently
    would leave a status the log cannot explain.
    """

    new_tags = forms.CharField(
        label=_("New tags"),
        required=False,
        help_text=_("Separate them with commas. A tag you have already keeps its colour."),
    )

    class Meta:
        model = Application
        fields = ("status", "channel", "priority", "deadline", "department", "contact", "tags")
        widgets = {"deadline": forms.DateInput(attrs={"type": "date"})}
        help_texts = APPLICATION_HELP

    def save(self, commit: bool = True):
        """Save, then add any tag that was typed rather than ticked.

        After ``save_m2m``, because Django writes the ticked ones there and adding to the
        set before it would be overwritten by it (#139).
        """
        application = super().save(commit=commit)
        if commit:
            self._add_typed_tags(application)
        else:
            saved_m2m = self.save_m2m

            def save_m2m():
                saved_m2m()
                self._add_typed_tags(application)

            self.save_m2m = save_m2m
        return application

    def _add_typed_tags(self, application) -> None:
        typed = Tag.named(self.user, Tag.split(self.cleaned_data.get("new_tags", "")))
        if typed:
            application.tags.add(*typed)

    def scope_querysets(self) -> None:
        from postulo.jobs import structure

        self.fields["tags"].queryset = Tag.objects.for_user(self.user)
        contacts = Contact.objects.for_user(self.user).select_related("company")
        if self.instance.pk:
            # The contacts worth offering are the ones at this company.
            contacts = contacts.filter(company=self.instance.posting.company_id)
        self.fields["contact"].queryset = contacts

        # Which part of the employer this was aimed at (#138). Offered only where the
        # feature is on *and* there is something to choose: a picker of one empty option is
        # a control asking to be ignored. Not offered is not the same as cleared -- a
        # department already named stays on the row and comes back with the feature.
        company = self.instance.posting.company if self.instance.pk else None
        departments = structure.departments_for(company, self.user)
        if not departments.exists():
            del self.fields["department"]
        else:
            self.fields["department"].queryset = departments
            self.fields["department"].empty_label = _("The employer as a whole")


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
        help_texts = {
            "kind": _(
                "What sort of thing it was. A status change and an interview write their "
                "own entries, so neither is offered here."
            ),
            "occurred_at": _("When it happened, which is not always when you write it down."),
            "summary": _("The line the timeline shows."),
            "body": _("Anything longer worth keeping with it. It sits under the line."),
        }
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
        # A status change or a scheduling is the record of something the application did,
        # not something to be typed by hand; offering them here would let the log
        # contradict the field or the interview they describe.
        self.fields["kind"].choices = [
            (value, label) for value, label in EventKind.choices if value not in SYSTEM_EVENT_KINDS
        ]


class ReminderForm(OwnerScopedModelForm):
    class Meta:
        model = Reminder
        fields = ("summary", "due_at", "application")
        help_texts = {
            "summary": _("What to do. It is what the reminder says when it falls due."),
            "due_at": _(
                "In your time zone. Any notifier you have switched on is told at that moment."
            ),
            "application": _("Which one it is about. Leave it empty for a reminder about none."),
        }
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
        }

    def scope_querysets(self) -> None:
        self.fields["application"].queryset = Application.objects.for_user(
            self.user
        ).with_display_data()
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]


DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


class InterviewForm(OwnerScopedModelForm):
    """Schedule an interview — or record one that already happened, from the same form."""

    remind = forms.BooleanField(
        label=_("Remind me the day before"),
        required=False,
        initial=True,
        help_text=_("A reminder falls due a day ahead, when there is a day ahead."),
    )

    class Meta:
        model = Interview
        fields = ("kind", "starts_at", "ends_at", "location", "contacts", "notes")
        help_texts = {
            "starts_at": _(
                "In your time zone. The calendar file Postulo makes writes it in UTC, so it "
                "lands at the right moment wherever it is opened."
            ),
            "contacts": _(
                "Who you are meeting. Only the people you have recorded at this company are "
                "offered."
            ),
            "notes": _("Yours, to prepare with. Nothing here is sent to anybody."),
            "ends_at": _("Left blank, it lasts an hour."),
        }
        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "contacts": forms.CheckboxSelectMultiple,
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, application=None, **kwargs):
        instance = kwargs.get("instance")
        self.application = application or (instance.application if instance is not None else None)
        super().__init__(*args, **kwargs)
        self.fields["ends_at"].required = False
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = DATETIME_INPUT_FORMATS
        if self.instance.pk:
            # The reminder was made, or not, when it was scheduled; moving the interview
            # moves it.
            del self.fields["remind"]

    def scope_querysets(self) -> None:
        contacts = Contact.objects.for_user(self.user)
        if self.application is not None:
            # The people worth offering are the ones at this company.
            contacts = contacts.filter(company=self.application.posting.company_id)
        self.fields["contacts"].queryset = contacts

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", _("It cannot end before it starts."))
        return cleaned


class TagForm(OwnerScopedModelForm):
    """A tag's word, and the two things that make it findable on a crowded board (#285).

    Radio groups rather than two selects. A select is right when the options are a long list
    of words and wrong when they are seven colours and thirteen pictures: `Violet` and
    `graduation-cap` read out in a dropdown tell somebody the name of the thing they are
    choosing and nothing about what they will get, and they cannot be compared without
    opening the list seven times. Laid out, they are all on screen at once and each one shows
    itself.

    The choices are set here rather than left to the model field because a `ModelForm` adds
    its own blank option to a field that allows one, and *both* of these already say what
    empty means -- grey for a colour, "No icon" for an icon. A second empty row labelled
    `---------` would be a third answer to a question with two.
    """

    class Meta:
        model = Tag
        fields = ("name", "colour", "icon")
        widgets = {
            "colour": forms.RadioSelect,
            "icon": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["colour"].choices = TagColour.choices
        self.fields["icon"].choices = TagIcon.choices

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


class OfferForm(OwnerScopedModelForm):
    """What was offered, in the terms it arrived in (#237).

    Every field but the money is text, deliberately: a bonus is "10% on target", equity is
    "0.05% over four years with a one-year cliff", benefits are a paragraph, and a form that
    tried to structure those would refuse the offer as it was actually written. The base
    pay is a number, a currency and a period, because that is the one thing two offers are
    compared on and it has to be brought to a year to be.
    """

    #: The currency box suggests the codes an advert actually carries, and takes any code
    #: that is one (#224's validator); a select of the world's currencies would be a worse
    #: question than a short list beside a box that accepts the rest.
    datalists = {"offer-currencies": OFFERED_CURRENCIES}

    class Meta:
        model = Offer
        fields = (
            "base_amount",
            "currency",
            "period",
            "variable_pay",
            "equity",
            "benefits",
            "location",
            "holidays",
            "starts_on",
            "answer_by",
            "notes",
        )
        widgets = {
            "currency": forms.TextInput(attrs={"list": "offer-currencies", "maxlength": 3}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "answer_by": forms.DateInput(attrs={"type": "date"}),
            "variable_pay": forms.Textarea(attrs={"rows": 2}),
            "equity": forms.Textarea(attrs={"rows": 2}),
            "benefits": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "base_amount": _("The figure as they wrote it, before tax."),
            "period": _("What the figure covers. Offers are compared by the year."),
            "equity": _("As it was written: a percentage, a number of options, the vesting."),
            "holidays": _("Days a year, if it was stated."),
            "starts_on": _("The start date they proposed, if one was."),
            "notes": _("Anything else worth remembering about it: who made it, what was said."),
        }

    def clean_currency(self) -> str:
        return (self.cleaned_data.get("currency") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("base_amount") is not None and not cleaned.get("currency"):
            self.add_error("currency", _("Say which currency the amount is in."))
        return cleaned
