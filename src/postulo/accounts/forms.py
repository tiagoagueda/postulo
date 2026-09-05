"""Forms for personal details and invitations."""

from __future__ import annotations

import zoneinfo

from allauth.account.adapter import get_adapter
from allauth.account.forms import SignupForm as AllauthSignupForm
from allauth.socialaccount.forms import SignupForm as AllauthSocialSignupForm
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
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


class SocialSignupForm(AllauthSocialSignupForm):
    """The form single sign-on falls back to when the provider's claims were not enough.

    Usually the claims carry a name and an address and no form is shown at all; this one
    appears when something is missing, and asks for the same things a direct signup does.
    """

    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order = ["first_name", "last_name", "username", "email"]
        self.order_fields([name for name in order if name in self.fields])

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.save(update_fields=["first_name", "last_name"])
        return user


class ProfileForm(forms.ModelForm):
    """Your details: the name and the contact block, which is what documents print.

    The name lives on the user model but belongs on this page: it is the name that will
    be printed at the top of a CV, and nobody thinks of it as an account setting. How
    Postulo behaves for the person — theme, language, username, addresses — is Settings.
    """

    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)

    class Meta:
        model = Profile
        fields = ("headline", "phone", "location", "website", "linkedin_url", "source_repo_url")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name

    def save(self, commit: bool = True) -> Profile:
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if commit:
            user.save(update_fields=["first_name", "last_name"])
        return profile


class AppearanceForm(forms.ModelForm):
    """Settings → Appearance. The explicit version of the switch in the header."""

    class Meta:
        model = Profile
        fields = ("theme",)
        widgets = {"theme": forms.RadioSelect}


class LocaleForm(forms.ModelForm):
    """Settings → Language and time."""

    class Meta:
        model = Profile
        fields = ("language", "time_zone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"] = forms.ChoiceField(
            label=_("Language"), choices=language_choices, required=False
        )
        self.fields["time_zone"] = forms.ChoiceField(
            label=_("Time zone"), choices=time_zone_choices, required=False
        )


class AccountForm(forms.ModelForm):
    """Settings → Account: the username. Addresses and the password have allauth's pages."""

    class Meta:
        model = get_user_model()
        fields = ("username",)
        labels = {"username": _("Username")}
        help_texts = {
            "username": _(
                "What you sign in with. Lowercase letters, digits, dots, underscores, hyphens."
            )
        }

    def clean_username(self) -> str:
        """The same rules as at signup, unless the username is simply unchanged."""
        username = self.cleaned_data["username"].strip().casefold()
        if username == self.instance.username:
            return username
        return get_adapter().clean_username(username)


class InviteForm(forms.ModelForm):
    """Create an invitation to this instance."""

    class Meta:
        model = Invite
        fields = ("email", "note")
        labels = {
            "email": _("Email address (optional)"),
            "note": _("Note (optional)"),
        }
