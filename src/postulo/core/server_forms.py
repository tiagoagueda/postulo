"""Forms for the Server settings sections that write the site's policy row."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SiteSettings

#: A nullable boolean as three words, so "never set from here" stays a real state.
POLICY_CHOICES = [
    ("", _("Not set here — the environment or the default applies")),
    ("true", _("Yes")),
    ("false", _("No")),
]


class PolicyField(forms.TypedChoiceField):
    """Three states: unset, yes, no."""

    def __init__(self, **kwargs):
        kwargs.setdefault("choices", POLICY_CHOICES)
        kwargs.setdefault("required", False)
        kwargs.setdefault("coerce", self._coerce)
        kwargs.setdefault("empty_value", None)
        super().__init__(**kwargs)

    @staticmethod
    def _coerce(value: str):
        return value == "true"

    def prepare_value(self, value):
        if value is None or value == "":
            return ""
        return "true" if value else "false"


class SignInForm(forms.ModelForm):
    registration_open = PolicyField(
        label=_("Registration open"),
        help_text=_(
            "Yes: anyone who finds the address may create an account. No: invitation only. "
            "An empty instance always offers the sign-up form, so somebody can become the "
            "first account."
        ),
    )

    class Meta:
        model = SiteSettings
        fields = ("registration_open",)


class CaptureForm(forms.ModelForm):
    capture_ignore_robots = PolicyField(
        label=_("Ignore robots.txt when capturing"),
        help_text=_(
            "Postulo fetches one page a person asked for, and honours a site's robots.txt "
            "by default. Say yes only if you have decided that courtesy does not apply here."
        ),
    )

    class Meta:
        model = SiteSettings
        fields = ("capture_ignore_robots",)


class DefaultsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ("instance_name", "tagline", "default_language", "default_time_zone")

    def __init__(self, *args, **kwargs):
        from postulo.accounts.forms import language_choices, time_zone_choices

        super().__init__(*args, **kwargs)
        self.fields["default_language"] = forms.ChoiceField(
            label=_("Language for new accounts"),
            choices=language_choices,
            required=False,
            help_text=_("What a new account starts with. Each person can change theirs."),
        )
        self.fields["default_time_zone"] = forms.ChoiceField(
            label=_("Time zone for new accounts"),
            choices=time_zone_choices,
            required=False,
        )


class TestEmailForm(forms.Form):
    to = forms.EmailField(label=_("Send a test message to"))
