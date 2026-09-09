"""Forms for the Server settings sections that write the site's policy row."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from . import site
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

    sso_is_second_factor = PolicyField(
        label=_("Single sign-on counts as the second factor"),
        help_text=_(
            "Yes: somebody who arrives through the identity provider is signed straight in, "
            "without being asked for a code as well. Say yes only if you know that provider "
            "checks identity at least as carefully as Postulo would. It never applies to a "
            "password sign-in, and it removes nobody's authenticator app. A passkey already "
            "counts on its own and needs no setting."
        ),
    )

    email_sign_in = PolicyField(
        label=_("Sign in with a code sent by email"),
        help_text=_(
            "Yes: somebody who has forgotten their password can ask for a code at their "
            "primary address and type it back. A code rather than a link, because mail "
            "scanners follow links and would spend one before the person read it. It is "
            "never a second factor: an authenticator app is still asked for. Offered only "
            "while this instance's mail is actually getting through."
        ),
    )

    class Meta:
        model = SiteSettings
        fields = ("registration_open", "sso_is_second_factor", "email_sign_in")


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


class OfferedLanguagesForm(forms.ModelForm):
    """Which languages this instance offers at all.

    Stored as an empty list when everything is ticked, and that is the point rather than an
    optimisation: an instance that offers all of them keeps offering all of them, including
    a language added in a later release. A list naming every language today would freeze
    the set on the day somebody first saved this form.
    """

    class Meta:
        model = SiteSettings
        fields = ("offered_languages",)

    offered_languages = forms.MultipleChoiceField(
        label=_("Languages this instance offers"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Everything is offered until you narrow it. Tick them all and it stays that "
            "way, so a language added in a later release appears by itself."
        ),
    )

    def __init__(self, *args, **kwargs):
        from postulo.core import languages

        super().__init__(*args, **kwargs)
        self.every = [
            (code, name)
            for code, name in languages.NATIVE_NAMES.items()
            if code != languages.SOURCE
        ]
        self.fields["offered_languages"].choices = self.every
        stored = list(self.instance.offered_languages or [])
        self.fields["offered_languages"].initial = stored or [code for code, _n in self.every]

    def clean_offered_languages(self) -> list[str]:
        chosen = list(self.cleaned_data.get("offered_languages") or [])
        if not chosen:
            raise forms.ValidationError(
                _("At least one language has to be offered, or nobody can read anything.")
            )

        default = (self.instance.default_language or "").strip()
        if default and default not in chosen:
            raise forms.ValidationError(
                _(
                    "%(name)s is what a new account starts in, so it cannot stop being "
                    "offered. Change the default first."
                )
                % {"name": self._name_of(default)}
            )

        # Everything ticked is stored as nothing, so a language added later is offered too.
        return [] if len(chosen) == len(self.every) else chosen

    @staticmethod
    def _name_of(code: str) -> str:
        from postulo.core import languages

        return languages.NATIVE_NAMES.get(code, code)


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


class EmailForm(forms.ModelForm):
    """How this instance sends mail, from the interface, with the environment still winning.

    Three things here are not ordinary form work.

    **A pinned field is refused, not merely made readonly.** The template renders a field
    the environment sets as `readonly`, which is presentation and nothing else: a readonly
    input is still submitted by the browser, and a form can be posted without a browser at
    all. So whatever arrives for a pinned field is dropped here, before `_post_clean` can
    put it on the instance. Readonly is the courtesy; this is the rule.

    **The password is write-only.** It renders blank every time, and blank means "leave
    what is stored alone". Rendering the stored value into a password input would put it in
    the page's HTML for anyone reading over a shoulder, viewing source, or holding a proxy
    log. Clearing one is a separate, deliberate checkbox rather than an empty field, which
    is why blank can safely mean "no change".

    **`readonly` rather than `disabled`.** Django's `disabled=True` would be the tidy way to
    ignore the posted value, and it renders the HTML `disabled` attribute, which takes the
    field out of the tab order and is announced inconsistently by screen readers. A value an
    administrator can read and copy must not become one some of them cannot reach.
    """

    email_security = forms.ChoiceField(
        label=_("Connection security"),
        required=False,
        help_text=_(
            "STARTTLS connects and then upgrades, which is what port 587 expects. TLS from "
            "the first byte is what port 465 expects. They are not interchangeable: point "
            "one at the other's port and each waits for the other to speak."
        ),
    )
    email_password = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Leave blank to keep the one already stored."),
    )
    forget_email_password = forms.BooleanField(
        label=_("Forget the stored password"),
        required=False,
        help_text=_("Nothing is sent to the server as a password until a new one is entered."),
    )

    class Meta:
        model = SiteSettings
        fields = (
            "email_host",
            "email_port",
            "email_username",
            "email_security",
            "email_timeout",
            "email_from",
        )

    #: Prefix for the fields a non-SMTP transport declares, so its `host` cannot collide
    #: with the column of the same name that belongs to SMTP.
    TRANSPORT_PREFIX = "transport__"

    def __init__(self, *args, **kwargs):
        from postulo.core.models import MailSecurity

        super().__init__(*args, **kwargs)
        if "email_security" in self.fields:
            # An empty first choice, because blank means "not set here" for every column on
            # this page and the environment answers for it.
            self.fields["email_security"].choices = [
                ("", _("Not set — the environment decides")),
                *MailSecurity.choices,
            ]
        self._add_transport_fields()
        self.pinned = {
            field: variable
            for field in site.EMAIL_FIELDS
            if (variable := site.overridden_by(field))
        }
        resolved = site.email_settings()
        for field, key in site.EMAIL_FIELDS.items():
            # The effective value, so a pinned field shows what is actually in force rather
            # than an empty box beside a note saying it comes from somewhere else.
            if field in self.pinned and field in self.fields and field != "email_password":
                self.initial[field] = resolved[key]
        if "email_password" in self.pinned:
            # Nothing to type and nothing to forget: the environment holds it.
            del self.fields["email_password"]
            del self.fields["forget_email_password"]
        self.fields["email_host"].widget.attrs.setdefault("placeholder", "smtp.example.org")
        self.fields["email_from"].widget.attrs.setdefault("placeholder", "postulo@example.org")

    def clean(self):
        cleaned = super().clean()
        # `_post_clean` builds the instance from `cleaned_data`, and skips what is not in
        # it, so removing a pinned field here is what stops it being written. The stored
        # value stays as it was, shadowed rather than overwritten -- which is the state the
        # page warns about, and would be a lie if saving quietly changed it.
        for field in self.pinned:
            cleaned.pop(field, None)
            self.errors.pop(field, None)
        self._suggest_the_port(cleaned)
        return cleaned

    @staticmethod
    def _suggest_the_port(cleaned: dict) -> None:
        """Fill in the port each kind of connection is normally offered on.

        Only when the box was left empty. A port somebody typed is never corrected: a relay
        on a port of its own is an ordinary thing for a self-hosted instance to have, and
        the surest way to make a setting page hated is to argue with what was typed into it.
        """
        from postulo.core.models import DEFAULT_MAIL_PORTS

        if "email_port" not in cleaned or "email_security" not in cleaned:
            return
        if cleaned.get("email_port"):
            return
        cleaned["email_port"] = DEFAULT_MAIL_PORTS.get(cleaned.get("email_security"))

    def _add_transport_fields(self) -> None:
        """A chooser when there is a choice, and the chosen transport's own settings.

        SMTP is the exception: its settings are the named columns above, because they came
        first, because each is overridden individually by its own environment variable, and
        because a fresh instance has to be able to send before there is a row to read.
        Anything else declares fields and gets them drawn here, which is what makes the kind
        worth having for somebody whose host blocks outbound SMTP (#104).
        """
        from postulo.notifications import transport as transports
        from postulo.plugins import base
        from postulo.plugins.forms import form_field_for

        installed = transports.available()
        chosen = transports.selected()
        if len(installed) > 1:
            self.fields["email_transport"] = forms.ChoiceField(
                label=_("Mail transport"),
                choices=[(item.name, base.label_of(item)) for item in installed],
                required=False,
                help_text=_("What carries the mail off this machine."),
            )
            self.initial.setdefault(
                "email_transport", getattr(chosen, "name", transports.DEFAULT_TRANSPORT)
            )
        if chosen is None or chosen.name == transports.DEFAULT_TRANSPORT:
            return

        # Not SMTP: its columns mean nothing here, so they are not offered.
        for name in (
            "email_host",
            "email_port",
            "email_username",
            "email_security",
            "email_timeout",
        ):
            self.fields.pop(name, None)
        self.fields.pop("email_password", None)
        self.fields.pop("forget_email_password", None)

        row = self.instance
        stored = dict(row.transport_config or {})
        held = set(row.transport_secrets)
        for spec in chosen.config_fields():
            key = f"{self.TRANSPORT_PREFIX}{spec.name}"
            self.fields[key] = form_field_for(spec, has_value=spec.name in held)
            if not (spec.type == "password" or spec.secret):
                self.initial.setdefault(key, stored.get(spec.name, spec.default))

    def _transport_answers(self) -> tuple[dict, dict]:
        """What was typed for the transport's own fields, split into plain and secret."""
        from postulo.notifications import transport as transports

        chosen = transports.selected()
        if chosen is None or chosen.name == transports.DEFAULT_TRANSPORT:
            return {}, {}
        plain, secret = {}, {}
        for spec in chosen.config_fields():
            key = f"{self.TRANSPORT_PREFIX}{spec.name}"
            if key not in self.cleaned_data:
                continue
            value = self.cleaned_data[key]
            if spec.type == "password" or spec.secret:
                # Blank means "keep what is stored", exactly as it does for the SMTP one.
                if value:
                    secret[spec.name] = value
            else:
                plain[spec.name] = value
        return plain, secret

    def save(self, commit=True):
        row = super().save(commit=False)
        if "email_password" not in self.pinned:
            if self.cleaned_data.get("forget_email_password"):
                row.email_password = ""
            elif self.cleaned_data.get("email_password"):
                row.email_password = self.cleaned_data["email_password"]
        if "email_transport" in self.fields:
            row.email_transport = self.cleaned_data.get("email_transport", "") or ""
        plain, secret = self._transport_answers()
        if plain:
            row.transport_config = {**(row.transport_config or {}), **plain}
        if secret:
            row.transport_secrets = {**row.transport_secrets, **secret}
        if commit:
            row.save()
        return row
