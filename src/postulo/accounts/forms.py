"""Forms for personal details and invitations."""

from __future__ import annotations

import zoneinfo

from allauth.account.adapter import get_adapter
from allauth.account.forms import SignupForm as AllauthSignupForm
from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import Invite, Profile


class SignupForm(AllauthSignupForm):
    """allauth's signup form, plus the two names.

    allauth builds username, email and password fields from ``ACCOUNT_SIGNUP_FIELDS``;
    the name is Postulo's requirement, so it is added here and saved onto the user in
    the same step. Fields are reordered so the form reads as a person would fill it in.
    """

    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order = ["first_name", "last_name", "username", "email", "password1", "password2"]
        self.order_fields([name for name in order if name in self.fields])

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.save(update_fields=["first_name", "last_name"])
        return user


def language_choices() -> list[tuple[str, str]]:
    """Languages this instance offers, plus an option to follow the browser."""
    return [("", _("Use the instance default"))] + [
        (code, name) for code, name in settings.LANGUAGES
    ]


def time_zone_choices() -> list[tuple[str, str]]:
    """Every IANA zone this machine knows about.

    Built at render time rather than declared on the model, so that a time zone
    database update does not generate a migration.
    """
    return [("", _("Use the instance default"))] + [
        (name, name.replace("_", " ")) for name in sorted(zoneinfo.available_timezones())
    ]


class ProfileForm(forms.ModelForm):
    """Personal details, contact block, and interface preferences.

    The name lives on the user model but belongs on this page: it is the name that will
    be printed at the top of a CV, and nobody thinks of it as an account setting.
    """

    username = forms.CharField(
        label=_("Username"),
        max_length=32,
        help_text=_(
            "What you sign in with. Lowercase letters, digits, dots, underscores, hyphens."
        ),
    )
    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)

    class Meta:
        model = Profile
        fields = (
            "headline",
            "phone",
            "location",
            "website",
            "linkedin_url",
            "source_repo_url",
            "language",
            "time_zone",
            "theme",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"] = forms.ChoiceField(
            label=_("Language"), choices=language_choices, required=False
        )
        self.fields["time_zone"] = forms.ChoiceField(
            label=_("Time zone"), choices=time_zone_choices, required=False
        )
        if self.instance and self.instance.pk:
            self.fields["username"].initial = self.instance.user.username
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name

    def clean_username(self) -> str:
        """The same rules as at signup, unless the username is simply unchanged."""
        username = self.cleaned_data["username"].strip().casefold()
        current = self.instance.user.username if self.instance and self.instance.pk else ""
        if username == current:
            return username
        return get_adapter().clean_username(username)

    def save(self, commit: bool = True) -> Profile:
        profile = super().save(commit=commit)
        user = profile.user
        user.username = self.cleaned_data["username"]
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if commit:
            user.save(update_fields=["username", "first_name", "last_name"])
        return profile


class InviteForm(forms.ModelForm):
    """Create an invitation to this instance."""

    class Meta:
        model = Invite
        fields = ("email", "note")
        labels = {
            "email": _("Email address (optional)"),
            "note": _("Note (optional)"),
        }
