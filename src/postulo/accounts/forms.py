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

from postulo.core import languages, phone_field, phone_numbers, phones, web_links

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

    # `autocomplete` names what the field is for (SC 1.3.5, #276): a browser can fill it and
    # speech or cognitive tooling can recognise it. These are about the person; a contact's
    # or a company's fields are about somebody else and carry no token.
    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order = ["first_name", "last_name", "username", "email", "password1", "password2"]
        self.order_fields([name for name in order if name in self.fields])
        with_strength_meter(self)
        # Said here rather than on the field, which allauth builds from a setting (#205).
        if "username" in self.fields:
            self.fields["username"].help_text = _(
                "You can sign in with this or with your address. Nobody else sees it — "
                "Postulo shows people your name."
            )
        if "email" in self.fields:
            self.fields["email"].help_text = _(
                "Where Postulo writes to you, and what a CV prints if you let it show "
                "contact details."
            )

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

    A language that is only partly translated says so beside its name, and one nobody has
    reviewed says that, so nobody is surprised by English in the gaps or by an odd turn of
    phrase. Unreviewed does not mean machine-made: `pt-br` was seeded from `pt-pt` and
    adapted, which is a different provenance and the same warning.
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
    from postulo.core import site

    for code, name in settings.LANGUAGES:
        if not site.offers(code):
            # An administrator has narrowed what this instance offers (#120). Nothing is
            # deleted and nobody's stored choice is rewritten; the language is simply not
            # on the list until it is offered again.
            continue
        row = status.get(code)
        if row is not None and row.get("total", 0) and not row.get("translated", 0):
            # A language whose catalogue nobody has started is not offered. Postulo adds
            # the languages of a phase before their translations exist (#70), and offering
            # somebody their own language only to hand them an English interface is a
            # promise with nothing behind it. The catalogue sits there waiting for a
            # translator, and the language appears the day one starts.
            continue
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
        # Not "machine translation": a variant seeded from a sibling catalogue is not
        # machine-translated, and pt-BR is exactly that. What every language in this
        # group has in common is that no speaker has read it yet, which is the thing
        # worth saying and the only thing that is true of all of them.
        choices.append((_("Awaiting review by a speaker"), drafted))
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

    ``flagged`` makes each option carry its flag's URL as well, for a script that draws the
    chosen language's flag over the closed select -- the telephone field's answer (#88) to
    an ``<option>`` holding text and nothing else, used by *Server settings -> Defaults*
    (#208). Per option because static files are served under a content hash, so there is
    no pattern a script could build one from. Off by default: most language menus have no
    flag beside them and would only carry the weight.
    """

    def __init__(self, attrs=None, choices=(), *, flagged: bool = False):
        super().__init__(attrs, choices)
        self.flagged = flagged

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        code = str(value or "")
        if code:
            option["attrs"]["lang"] = code
            if self.flagged:
                from postulo.core import languages
                from postulo.core.templatetags.postulo import flag_url

                option["attrs"]["data-flag"] = flag_url(languages.flag_country(code))
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

    # `autocomplete` names what the field is for (SC 1.3.5, #276): a browser can fill it and
    # speech or cognitive tooling can recognise it. These are about the person; a contact's
    # or a company's fields are about somebody else and carry no token.
    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )

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

    # `autocomplete` names what the field is for (SC 1.3.5, #276): a browser can fill it and
    # speech or cognitive tooling can recognise it. These are about the person; a contact's
    # or a company's fields are about somebody else and carry no token.
    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
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
        fields = (
            "headline",
            "location",
            "record_language",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The same menu the interface language uses, so an option is written in the language
        # it names. Blank is a real answer here rather than an omission: most people have
        # one career in one language and never think about this again (#131).
        self.fields["record_language"].widget = LanguageSelect(
            choices=[("", _("The language you read Postulo in")), *languages.LANGUAGES]
        )
        self.fields["record_language"].required = False
        self.fields["record_language"].help_text = _(
            "Which language your experience, education and projects are typed in. A CV in "
            "another language shows what you have translated, and says what it could not."
        )
        # One box while *Several telephone numbers* is switched off, and none at all
        # while it is on: the rows are a formset of their own, and two controls writing
        # the same primary row would be two answers to one question.
        self.several_numbers = phone_numbers.several_allowed(getattr(self.instance, "user", None))
        if not self.several_numbers:
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
        # And one box per kind of link whose feature is off, on the same terms (#189).
        web_links.add_single_boxes(self, getattr(self.instance, "user", None), holder=self.instance)
        if self.instance and self.instance.pk:
            if not self.several_numbers:
                primary = phone_numbers.primary_for(self.instance)
                self.fields["phone"].initial = primary.number if primary else ""
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

    @property
    def link_boxes(self) -> list:
        """The one-box-per-kind fields, for the template to lay out where the columns were."""
        return web_links.single_boxes(self)

    def clean_phone(self) -> str:
        typed = (self.cleaned_data.get("phone") or "").strip()
        primary = phone_numbers.primary_for(self.instance) if self.instance.pk else None
        if typed and phone_numbers.taken_elsewhere(
            typed, exclude_pk=primary.pk if primary else None
        ):
            raise forms.ValidationError(phone_numbers.collision_message(self.instance.user))
        return typed

    def save(self, commit: bool = True) -> Profile:
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if commit:
            user.save(update_fields=["first_name", "last_name"])
            self._save_picture(profile)
            if not self.several_numbers:
                phone_numbers.save_only_number(profile, user, self.cleaned_data.get("phone", ""))
            web_links.save_single_boxes(self, profile, user)
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


class AccessibilityForm(forms.ModelForm):
    """Settings → Accessibility: what changes how the interface behaves for somebody who
    needs it to behave differently (#281).

    Three choices today. Two were moved from Appearance, where they were filed for want
    of anywhere else: the order number on a career entry, for somebody who cannot use the
    arrows (#203), and the way out of the single-key shortcuts, which WCAG 2.1.4 asks for
    at level A (#227). The third is the first one filed here on purpose (#289).
    """

    class Meta:
        model = Profile
        fields = ("show_career_order", "keyboard_shortcuts", "nav_underline")
        labels = {
            "show_career_order": _("Show the order number on each career entry"),
            "keyboard_shortcuts": _("Let a single key do something"),
            "nav_underline": _("Underline the page you are on"),
        }
        help_texts = {
            "show_career_order": _(
                "The arrows on Your career move an entry past its neighbour. If you cannot "
                "use them, or would rather type a number, each entry's form shows its place "
                "as a number: lower first."
            ),
            "keyboard_shortcuts": _(
                "On the capture review, “d” discards and moves on and “j” skips; "
                "“/” jumps to the search box anywhere. Turn this off if you dictate to your "
                "computer, or if a key pressed by accident does more than you meant. "
                "Shortcuts that need Ctrl go on working either way."
            ),
            "nav_underline": _(
                "The link for the page you are on is underlined as well as shaded and set "
                "in bolder type. Turn the underline off for a quieter header: the weight "
                "and the shade stay, so the page you are on is never marked by colour "
                "alone. A high-contrast theme underlines it whatever you choose here, "
                "because there the shading is discarded and the underline is not."
            ),
        }


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
        fields = ("theme", "density", "quiet_after_days", "closing_notice_days")
        widgets = {"theme": forms.RadioSelect, "density": forms.RadioSelect}
        labels = {
            "quiet_after_days": _("Consider an application quiet after"),
            "closing_notice_days": _("Warn me before a listing closes by"),
            "density": _("How much room to leave"),
        }
        help_texts = {
            # Under Appearance rather than Accessibility, by the rule #281 set: file a
            # choice by what somebody is looking for, not by what motivated it. A person
            # who wants more rows on a screen looks here (#292).
            "density": _(
                "Comfortable is the default and the roomier of the two. Compact tightens "
                "the space around cards and inside tables so that more fits on a screen; "
                "nothing you can click or tap gets smaller."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Left blank, either threshold goes back to its default rather than refusing to save.
        self.fields["quiet_after_days"].required = False
        self.fields["closing_notice_days"].required = False

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

    @property
    def quiet_days(self) -> int:
        """Whatever is in the box, as a number the template can pluralise against.

        The unit beside the field has to agree with it -- "1 day", "2 days", and in Polish
        a third form again (#225) -- and ``{% blocktranslate count %}`` refuses anything
        that is not a number. A bound form hands back the raw string somebody typed, which
        may be "" or "abc", so the default stands in: the unit is a label, and a label that
        raises while the page is showing a validation error is worse than one that agrees
        with the number the field will fall back to anyway.
        """
        try:
            return int(self["quiet_after_days"].value())
        except (TypeError, ValueError):
            return Profile._meta.get_field("quiet_after_days").default

    @property
    def closing_days(self) -> int:
        """The same, for the notice before a listing closes (#238)."""
        try:
            return int(self["closing_notice_days"].value())
        except (TypeError, ValueError):
            return Profile._meta.get_field("closing_notice_days").default

    def clean_quiet_after_days(self) -> int:
        value = self.cleaned_data.get("quiet_after_days")
        if value is None:
            return Profile._meta.get_field("quiet_after_days").default
        return value

    def clean_closing_notice_days(self) -> int:
        value = self.cleaned_data.get("closing_notice_days")
        if value is None:
            return Profile._meta.get_field("closing_notice_days").default
        return value


class PersonIdentifierForm(forms.ModelForm):
    """One row: a scheme, the value, and a name for it when the scheme is Other."""

    class Meta:
        model = PersonIdentifier
        fields = ("scheme", "value", "label")
        help_texts = {
            "scheme": _("Which register the number belongs to. What goes where, above, says."),
            "value": _("Postulo tidies it into the register's own spelling and checks the shape."),
            "label": _("Only for “Other”: what to call it on a CV."),
        }
        widgets = {
            "value": forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A blank first choice, so an untouched extra row counts as unchanged and is
        # dropped rather than complaining that its value is missing.
        scheme = self.fields["scheme"]
        choices = [("", "—"), *identifiers.choices()]
        scheme.choices = choices
        scheme.required = False
        # Django picks the widget from the model field when it builds the form, and that
        # field deliberately has no choices; the registry's schemes arrive only now, so the
        # select that shows them has to come with them, choices in hand — the field here is
        # a `CharField`, whose `choices` nothing reads from (#298).
        scheme.widget = forms.Select(choices=choices)
        self.fields["value"].required = False

    def clean(self) -> dict:
        data = super().clean()
        if not data.get("scheme") and (data.get("value") or data.get("label")):
            self.add_error("scheme", _("Choose what kind of identifier this is."))
        if data.get("scheme") and not data.get("value"):
            self.add_error("value", _("Type the identifier."))
        # The name means something only for Other, and the display side has always read
        # it only then. A name typed and then given another kind used to be stored,
        # invisible everywhere and still in the export; it is blanked now, and an Other
        # with no name -- which showed as the word "Other", telling nobody anything -- is
        # refused (#284).
        if data.get("scheme") == "other":
            if not (data.get("label") or "").strip():
                self.add_error("label", _("Say what this identifier is."))
        else:
            data["label"] = ""
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
        """The language list, ready to render as rows inside a disclosure.

        A dropdown cannot do what this list needs, and #119 asked for one. An ``<option>``
        may carry ``lang`` and nothing inside it, so a flag placed in the option text is
        read out by a screen reader along with the name — "Greek flag, Ελληνικά" — the name
        has to be marked as being in its own language or it is pronounced with the wrong
        rules entirely (#64), and a symbol saying how the translation was made would sit
        *inside* an element claiming to be Greek while being neither Greek nor a word. Rows
        solve all three, and the ``<details>`` around them answers the part of #119 that was
        really about shape: one line closed, like the time zone beside it.

        What each option carries is the *country* whose flag stands for the language, not
        the flag itself; ``{% flag %}`` turns it into an image. It used to be the emoji,
        which Windows draws as two letters (#88).

        ``state`` is how the translation was made, and the percentage comes out of the name
        rather than staying appended to it: outside the ``lang`` span there is somewhere to
        put it, which is exactly what a dropdown did not have.
        """
        from postulo.core import languages

        current = self["language"].value() or ""
        status = languages.translation_status()
        groups = []
        for label, entries in language_choices()[1:]:
            groups.append(
                {
                    "label": label,
                    "options": [
                        self._language_option(code, name, current, status) for code, name in entries
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
                        "country": "",
                        "selected": not current,
                        "state": "",
                        "percent": None,
                    }
                ],
            },
            *groups,
        ]

    @staticmethod
    def _language_option(code: str, name, current: str, status: dict) -> dict:
        """One row: what it is called, whose flag stands for it, and how it was made."""
        from postulo.core import languages

        row = status.get(code) or {}
        percent = row.get("percent") if row.get("total") else None
        if percent is not None and percent < 95:
            state = "partial"
        elif row.get("drafts"):
            state = "draft"
        else:
            state = "reviewed"
            percent = None
        return {
            "code": code,
            # `language_choices` appends the percentage to the name so that a dropdown has
            # somewhere to show it. Here it does not have to: the name goes inside the span
            # marked as being in that language, and the figure sits outside it in the
            # interface language, where it belongs (#119).
            "name": str(name).partition(" (")[0] if state == "partial" else name,
            "country": languages.flag_country(code),
            "selected": code == current,
            "state": state,
            "percent": percent,
        }

    def language_now(self) -> dict:
        """The row the closed disclosure shows: what is in use right now.

        Closed, this control has to go on answering "what am I using?" — a setting that only
        says so after a click has stopped being a setting page (#119).
        """
        for group in self.language_groups():
            for option in group["options"]:
                if option["selected"]:
                    return option
        return {"code": "", "name": "", "country": "", "state": "", "percent": None}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both are rebuilt here rather than taken from the model, so the sentences on
        # `Meta.help_texts` would be thrown away with the fields; they are set here (#205).
        self.fields["language"] = forms.ChoiceField(
            label=_("Language"),
            choices=language_choices,
            required=False,
            widget=forms.RadioSelect,
            help_text=_(
                "What Postulo is in for you, and nothing else. What a CV or a letter is "
                "written in is that document's own setting."
            ),
        )
        self.fields["time_zone"] = forms.ChoiceField(
            label=_("Time zone"),
            choices=time_zone_choices,
            required=False,
            help_text=_("Every date and time on these pages is shown in it."),
        )


class AccountForm(forms.ModelForm):
    """Settings → Account: the username. Addresses and the password have allauth's pages."""

    class Meta:
        model = get_user_model()
        fields = ("username",)
        labels = {"username": _("Username")}
        widgets = {"username": forms.TextInput(attrs={"autocomplete": "username"})}
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


def language_row(code: str, name, *, current: str = "", status: dict | None = None) -> dict:
    """One language, ready for a row that shows its flag and how it was made.

    What the locale picker's rows are built from, and since #208 what *Server settings ->
    Defaults* builds its rows from too, so the same list looks the same on both pages:
    `code`, `name`, the `country` whose flag stands for it (blank where none is right),
    `state` and `percent`.
    """
    from postulo.core.languages import translation_status

    if status is None:
        status = translation_status()
    return LocaleForm._language_option(code, name, current, status)
