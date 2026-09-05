import datetime as dt

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import SCOPES

EXPIRY_CHOICES = [
    ("", _("Never")),
    ("30", _("In 30 days")),
    ("90", _("In 90 days")),
    ("365", _("In a year")),
]


class ApiTokenForm(forms.Form):
    """Name it, say what it may do, and how long for."""

    name = forms.CharField(
        label=_("Name"),
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Firefox on the laptop"}),
        help_text=_("Which device or tool this is for."),
    )
    scopes = forms.MultipleChoiceField(
        label=_("What it may do"),
        choices=[(key, label) for key, label in SCOPES.items()],
        widget=forms.CheckboxSelectMultiple,
        initial=["captures"],
    )
    expires = forms.ChoiceField(label=_("Expires"), choices=EXPIRY_CHOICES, required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def expires_at(self) -> dt.datetime | None:
        days = self.cleaned_data.get("expires")
        if not days:
            return None
        return timezone.now() + dt.timedelta(days=int(days))
