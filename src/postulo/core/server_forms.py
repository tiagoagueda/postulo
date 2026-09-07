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

    class Meta:
        model = SiteSettings
        fields = ("registration_open", "sso_is_second_factor")


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

    email_use_tls = PolicyField(
        label=_("STARTTLS"),
        help_text=_(
            "Yes for a server on port 587 that upgrades the connection after connecting. "
            "Postulo does not yet speak implicit TLS on port 465."
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
            "email_use_tls",
            "email_timeout",
            "email_from",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        return cleaned

    def save(self, commit=True):
        row = super().save(commit=False)
        if "email_password" not in self.pinned:
            if self.cleaned_data.get("forget_email_password"):
                row.email_password = ""
            elif self.cleaned_data.get("email_password"):
                row.email_password = self.cleaned_data["email_password"]
        if commit:
            row.save()
        return row
