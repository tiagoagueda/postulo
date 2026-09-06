"""Forms for personal details and invitations."""

from __future__ import annotations

import zoneinfo

from allauth.account.adapter import get_adapter
from allauth.account.forms import ChangePasswordForm as AllauthChangePasswordForm
from allauth.account.forms import ResetPasswordKeyForm as AllauthResetPasswordKeyForm
from allauth.account.forms import SetPasswordForm as AllauthSetPasswordForm
from allauth.account.forms import SignupForm as AllauthSignupForm
from allauth.socialaccount.forms import SignupForm as AllauthSocialSignupForm
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from . import avatars
from .models import Invite, Profile


def with_strength_meter(form: forms.Form) -> None:
    """Mark the field where a password is chosen, so the browser draws a meter under it.

    The estimate runs client-side (zxcvbn) and the password never leaves the browser
    before the form is submitted; with scripts off the field is an ordinary field and
    Django's rules, listed beneath it, still decide.
    """
    field = form.fields.get("password1")
    if field is not None:
        field.widget.attrs["data-password-meter"] = "true"


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
        with_strength_meter(self)

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.save(update_fields=["first_name", "last_name"])
        return user


class ChangePasswordForm(AllauthChangePasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with_strength_meter(self)


class SetPasswordForm(AllauthSetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with_strength_meter(self)


class ResetPasswordKeyForm(AllauthResetPasswordKeyForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with_strength_meter(self)


def language_choices() -> list[tuple[str, str]]:
    """Languages this instance offers, plus an option to follow the browser.

    A language that is only partly translated says so beside its name, and one whose
    translation is a machine-assisted draft nobody has reviewed says that, so nobody is
    surprised by English in the gaps or by an odd turn of phrase.
    """
    from postulo.core.languages import translation_status

    status = translation_status()
    choices = [("", _("Use the instance default"))]
    for code, name in settings.LANGUAGES:
        row = status.get(code)
        if row is None or row.get("total", 0) == 0:
            choices.append((code, name))
        elif row.get("percent", 0) < 95:
            choices.append(
                (
                    code,
                    _("%(language)s — %(percent)s%% translated")
                    % {"language": name, "percent": row["percent"]},
                )
            )
        elif row.get("drafts", 0):
            choices.append(
                (
                    code,
                    _("%(language)s — machine translation, awaiting review") % {"language": name},
                )
            )
        else:
            choices.append((code, name))
    return choices


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
    # A plain FileField, not an ImageField: the size and type are checked before anything
    # is decoded, and the decoding is done once, by the same code that stores the result.
    picture = forms.FileField(
        label=_("Upload a picture"),
        required=False,
        help_text=_(
            "PNG, JPEG, WebP or GIF up to 5 MB. It is cut to a square and stripped of "
            "anything the file knew about where it was taken."
        ),
    )
    remove_picture = forms.BooleanField(label=_("Remove the uploaded picture"), required=False)
    use_gravatar = forms.BooleanField(
        label=_("Use my Gravatar"),
        required=False,
        help_text=_(
            "Postulo fetches the picture for your primary address from gravatar.com once, "
            "keeps a copy, and shows that. Nothing is fetched while pages are viewed. Untick "
            "it and the copy is deleted."
        ),
    )

    class Meta:
        model = Profile
        fields = ("headline", "phone", "location", "website", "linkedin_url", "source_repo_url")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["use_gravatar"].initial = self.instance.use_gravatar
            if not self.instance.avatar:
                del self.fields["remove_picture"]

    def clean_picture(self):
        upload = self.cleaned_data.get("picture")
        if not upload:
            return upload
        if upload.size > avatars.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(_("That picture is over 5 MB. A smaller one, please."))
        content_type = getattr(upload, "content_type", "") or ""
        if content_type not in avatars.ALLOWED_CONTENT_TYPES:
            raise forms.ValidationError(_("Use a PNG, JPEG, WebP or GIF."))
        try:
            self._processed_picture = avatars.process(upload.read())
        except avatars.UnusableImage as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

    def save(self, commit: bool = True) -> Profile:
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if commit:
            user.save(update_fields=["first_name", "last_name"])
            self._save_picture(profile)
        return profile

    #: How the Gravatar fetch went, for the view to word its message: found, none, error, "".
    gravatar_outcome: str = ""

    def _save_picture(self, profile: Profile) -> None:
        processed = getattr(self, "_processed_picture", None)
        if processed is not None:
            avatars.store(profile, "avatar", processed, "avatar")
            profile.save(update_fields=["avatar", "updated_at"])
        elif self.cleaned_data.get("remove_picture"):
            avatars.remove_upload(profile)

        wanted = bool(self.cleaned_data.get("use_gravatar"))
        if wanted != profile.use_gravatar:
            profile.use_gravatar = wanted
            profile.save(update_fields=["use_gravatar", "updated_at"])
            if wanted:
                self.gravatar_outcome = avatars.fetch_gravatar(profile)
            else:
                avatars.forget_gravatar(profile)


class AppearanceForm(forms.ModelForm):
    """Settings → Appearance: the theme, and how the dashboard behaves."""

    class Meta:
        model = Profile
        fields = ("theme", "quiet_after_days")
        widgets = {"theme": forms.RadioSelect}
        labels = {"quiet_after_days": _("Consider an application quiet after")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Left blank, the threshold goes back to the default rather than refusing to save.
        self.fields["quiet_after_days"].required = False

    def clean_quiet_after_days(self) -> int:
        value = self.cleaned_data.get("quiet_after_days")
        if value is None:
            return Profile._meta.get_field("quiet_after_days").default
        return value


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
