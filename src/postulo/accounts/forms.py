"""Forms for personal details and invitations."""

from __future__ import annotations

import zoneinfo

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import Invite, Profile


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

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)

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
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name

    def save(self, commit: bool = True) -> Profile:
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save(update_fields=["first_name", "last_name"])
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
