"""Forms for personal details and invitations."""

from __future__ import annotations

import zoneinfo

from allauth.account.adapter import get_adapter
from allauth.account.forms import ChangePasswordForm as AllauthChangePasswordForm
from allauth.account.forms import LoginForm as AllauthLoginForm
from allauth.account.forms import ResetPasswordKeyForm as AllauthResetPasswordKeyForm
from allauth.account.forms import SetPasswordForm as AllauthSetPasswordForm
from allauth.account.forms import SignupForm as AllauthSignupForm
from allauth.mfa.webauthn.forms import AddWebAuthnForm as AllauthAddWebAuthnForm
from allauth.socialaccount.forms import SignupForm as AllauthSocialSignupForm
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from postulo.core import phone_field, phones

from . import avatars, identifiers
from .models import Invite, PersonIdentifier, Profile


def with_strength_meter(form: forms.Form) -> None:
    """Mark the field where a password is chosen, so the browser draws a meter under it.

    The estimate runs client-side (zxcvbn) and the password never leaves the browser
    before the form is submitted; with scripts off the field is an ordinary field and
    Django's rules, listed beneath it, still decide.
    """
    field = form.fields.get("password1")
    if field is not None:
        field.widget.attrs["data-password-meter"] = "true"


class LoginForm(AllauthLoginForm):
    """The sign-in form, in Postulo's words.

    Either a username or an email address signs in here, and allauth names that field
    "Login" because it cannot know which. It also labels the checkbox "Remember Me",
    which is title case in a project that writes sentence case everywhere else. Both are
    a sentence a person reads at the door, so both are worth saying properly.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["login"].label = _("Username or email")
        if "remember" in self.fields:
            self.fields["remember"].label = _("Stay signed in on this device")


class AddPasskeyForm(AllauthAddWebAuthnForm):
    """Naming a passkey, and choosing whether it can sign you in on its own.

    allauth labels that choice *Passwordless*, with a sentence about biometrics and PIN
    protection. It is the most consequential switch on the page and the label says nothing
    about what turning it off would mean — which is that the key becomes a second step
    after a password rather than a way in, and that it will not appear on the sign-in page
    at all.

    So it is named for what it does, described in terms of what happens either way, and it
    starts on. Somebody adding a passkey is almost always trying to stop typing a password.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        name = self.fields.get("name")
        if name is not None:
            name.label = _("What to call it")
            name.help_text = _(
                "For you, so you can tell one from another later: “work laptop”, “phone”."
            )
        field = self.fields.get("passwordless")
        if field is not None:
            field.label = _("Let this passkey sign me in on its own")
            field.help_text = _(
                "On, it replaces the password entirely and your device asks for a "
                "fingerprint, your face or a PIN instead. Off, it is only a second step "
                "after the password, and it will not appear on the sign-in page."
            )
            field.initial = True


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
    Each language is named in itself, which is the only way somebody who cannot read the
    current one will recognise theirs. That is also what makes the ``lang`` attribute
    matter here more than anywhere else in Postulo: without it a screen reader pronounces
    every entry with the rules of the interface language, and "Ελληνικά" read as English
    is not a word — it is a string of letters, or nothing at all. So how well translated a
    language is has been moved out of the option text and into the group it sits in: the
    option holds the name and nothing else, and can therefore be marked as being in that
    language, while the words about it stay in the language they are actually written in.
    """
    from postulo.core.languages import translation_status

    status = translation_status()
    reviewed: list[tuple[str, str]] = []
    drafted: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    for code, name in settings.LANGUAGES:
        row = status.get(code)
        if row is None or row.get("total", 0) == 0:
            reviewed.append((code, name))
        elif row.get("percent", 0) < 95:
            # A bare percentage carries no language of its own, so it can stay beside the
            # name without putting English inside an option marked as something else.
            partial.append((code, f"{name} ({row['percent']}%)"))
        elif row.get("drafts", 0):
            drafted.append((code, name))
        else:
            reviewed.append((code, name))

    choices: list = [("", _("Use the instance default"))]
    if reviewed:
        choices.append((_("Reviewed by a speaker"), reviewed))
    if drafted:
        choices.append((_("Machine translation, awaiting review"), drafted))
    if partial:
        choices.append((_("Partly translated"), partial))
    return choices


class LanguageSelect(forms.Select):
    """A language menu whose options say which language each of them is in.

    WCAG 2.2 calls this Language of Parts (3.1.2, level AA). A ``<select>`` allows ``lang``
    on an ``<option>`` and nothing inside one, which is why the state of a translation had
    to move to the group label: the option text has to be wholly in the language it claims.

    Right-to-left languages will want ``dir`` alongside this; that belongs with the rest of
    the layout work rather than here.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        code = str(value or "")
        if code:
            option["attrs"]["lang"] = code
        return option


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
        self.fields["phone"] = phone_field.PhoneField(
            label=_("Phone"),
            required=False,
            default_country=phones.default_country(
                getattr(self.instance, "language", "") or settings.LANGUAGE_CODE
            ),
            help_text=_(
                "Kept in the international form, so it can be dialled from anywhere. A "
                "number that already starts with + is taken as it is."
            ),
        )
        if self.instance and self.instance.pk:
            self.fields["phone"].initial = self.instance.phone
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
    """Settings → Appearance: the theme, the navigation, and how the dashboard behaves."""

    navigation = forms.MultipleChoiceField(
        label=_("Show in the navigation"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Everything here is reachable another way, so leaving one out takes nothing "
            "away. The Postulo wordmark always goes to the dashboard."
        ),
    )

    class Meta:
        model = Profile
        fields = ("theme", "quiet_after_days")
        widgets = {"theme": forms.RadioSelect}
        labels = {"quiet_after_days": _("Consider an application quiet after")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Left blank, the threshold goes back to the default rather than refusing to save.
        self.fields["quiet_after_days"].required = False

        from postulo.core import navigation

        self.fields["navigation"].choices = navigation.choices()
        hidden = set(self.instance.hidden_nav_items or []) if self.instance.pk else set()
        self.initial.setdefault(
            "navigation", [key for key in navigation.HIDEABLE if key not in hidden]
        )

    def save(self, commit: bool = True) -> Profile:
        """The form asks what to show; the profile records what to hide.

        Storing the hidden ones rather than the shown ones is what makes a new item
        appear for everybody who has not decided about it, which is the behaviour a
        person expects of an upgrade.
        """
        from postulo.core import navigation

        profile = super().save(commit=False)
        shown = set(self.cleaned_data.get("navigation") or [])
        profile.hidden_nav_items = [key for key in navigation.HIDEABLE if key not in shown]
        if commit:
            profile.save()
        return profile

    def clean_quiet_after_days(self) -> int:
        value = self.cleaned_data.get("quiet_after_days")
        if value is None:
            return Profile._meta.get_field("quiet_after_days").default
        return value


class PersonIdentifierForm(forms.ModelForm):
    """One row: a scheme, the value, and a name for it when the scheme is Other."""

    class Meta:
        model = PersonIdentifier
        fields = ("scheme", "value", "label")
        widgets = {
            "value": forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A blank first choice, so an untouched extra row counts as unchanged and is
        # dropped rather than complaining that its value is missing.
        self.fields["scheme"].choices = [("", "—"), *identifiers.CHOICES]
        self.fields["scheme"].required = False
        self.fields["value"].required = False

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("scheme") and (data.get("value") or data.get("label")):
            self.add_error("scheme", _("Choose what kind of identifier this is."))
        if data.get("scheme") and not data.get("value"):
            self.add_error("value", _("Type the identifier."))
        return data


class BasePersonIdentifierFormSet(forms.BaseInlineFormSet):
    """The rows together: one of each kind, and nothing listed twice."""

    def clean(self) -> None:
        super().clean()
        seen_schemes: set[str] = set()
        seen_values: set[tuple[str, str]] = set()
        for form in self.forms:
            if not form.is_valid() or not form.has_changed() or form.cleaned_data.get("DELETE"):
                continue
            scheme = form.cleaned_data.get("scheme")
            value = form.instance.value  # normalised by the model's clean()
            if not scheme or not value:
                continue
            if scheme != identifiers.OTHER:
                if scheme in seen_schemes:
                    form.add_error("scheme", _("This kind of identifier is already listed."))
                seen_schemes.add(scheme)
            if (scheme, value) in seen_values:
                form.add_error("value", _("This identifier is already listed."))
            seen_values.add((scheme, value))


PersonIdentifierFormSet = forms.inlineformset_factory(
    Profile,
    PersonIdentifier,
    form=PersonIdentifierForm,
    formset=BasePersonIdentifierFormSet,
    extra=1,
    can_delete=True,
)


class LocaleForm(forms.ModelForm):
    """Settings → Language and time."""

    class Meta:
        model = Profile
        fields = ("language", "time_zone")

    def language_groups(self) -> list[dict]:
        """The language list, ready to render as rows rather than as a dropdown.

        A dropdown cannot do what this list needs. An ``<option>`` may carry ``lang`` and
        nothing inside it, so a flag placed in the option text is read out by a screen
        reader along with the name — "Greek flag, Ελληνικά" — and the name has to be
        marked as being in its own language or it is pronounced with the wrong rules
        entirely (#64). Rows solve both: the flag is hidden from the accessibility tree
        because the name beside it already says what it is, and the name carries its own
        ``lang``.

        Twenty-four rows also read better than a dropdown of twenty-four: somebody
        looking for their language sees all of them at once.
        """
        from postulo.core import languages

        current = self["language"].value() or ""
        groups = []
        for label, entries in language_choices()[1:]:
            groups.append(
                {
                    "label": label,
                    "options": [
                        {
                            "code": code,
                            "name": name,
                            "flag": languages.flag(code),
                            "selected": code == current,
                        }
                        for code, name in entries
                    ],
                }
            )
        default = language_choices()[0]
        return [
            {
                "label": "",
                "options": [
                    {
                        "code": "",
                        "name": default[1],
                        "flag": "",
                        "selected": not current,
                    }
                ],
            },
            *groups,
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["language"] = forms.ChoiceField(
            label=_("Language"),
            choices=language_choices,
            required=False,
            widget=forms.RadioSelect,
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
