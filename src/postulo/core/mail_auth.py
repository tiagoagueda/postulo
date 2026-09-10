"""How an SMTP session proves who it is — a password, or a token (#151).

Until now there was one answer, and `smtplib.SMTP.login()` was all of it. Microsoft has
published a timeline that ends that: SMTP AUTH basic authentication is **disabled by default
for existing Exchange Online tenants from the end of December 2026**, unavailable by default
for new ones after that, with a final removal to be announced for the second half of 2027.
Afterwards only **XOAUTH2** is accepted. Google reaches the same place by the same mechanism.

So an instance whose operator uses Microsoft 365 for its mail loses the ability to send
verification links and password resets, on a date, with no configuration that fixes it. That
is the reason this exists and the reason it is not a modernisation.

**Nothing here removes username and password.** A self-hosted Postfix, a Mailcow, an
institutional relay, and Gmail with an app password all authenticate exactly as they did.
XOAUTH2 is an addition, offered where the provider needs it, and the default is unchanged so
that an instance which has never opened the Email page behaves exactly as it did.

**The mechanism is a dozen lines and takes no dependency.** XOAUTH2 is one SASL exchange:
a base64 string carrying a username and a bearer token. Reaching for a provider's SDK to do
that would pull a large dependency, tie this to one vendor, and put somebody else's HTTP
client on the path that delivers password-reset mail. `smtplib` already speaks SASL.

**The two providers are presets, not a dependency.** What Postulo needs from a provider is
three URLs and a scope; naming Google and Microsoft here spares an operator looking them up,
and a third is one row in `PROVIDERS`. The honest framing, which the documentation repeats:
this is *somebody's choice of mail provider being supported*, not Postulo depending on one.
An instance with its own mail server needs none of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class MailAuth(TextChoices):
    """How a mail server is told who is connecting.

    Blank keeps the meaning it has in every other column on the Email page: not set from the
    interface, so the environment answers -- and the environment's default is a password,
    which is what every existing instance uses.
    """

    PASSWORD = "password", _("A password, or an app password")
    XOAUTH2 = "xoauth2", _("XOAUTH2, with a token from the provider")


class MailGrant(TextChoices):
    """Which OAuth grant fetches the token, which is a question about who is consenting.

    **Signed in once** is an ordinary authorization-code grant: an operator agrees on a
    consent screen, as the mailbox that will be sending, and the refresh token is kept. It
    needs no administrator of anything and works at both providers.

    **The application sends on its own** is the client-credentials grant, which suits a
    server better -- nobody's session is involved and nothing expires because a person left.
    It needs a tenant administrator, an application registration and a permission scoped to
    one mailbox, which is Microsoft's route. Google's equivalent is a service account with
    domain-wide delegation, a different grant again and one not every operator can create, so
    it is not offered rather than offered and broken.
    """

    MAILBOX = "mailbox", _("Signed in once, as the mailbox that sends")
    APPLICATION = "application", _("The application sends on its own")


@dataclass(frozen=True)
class Provider:
    """One identity provider, as three addresses and a scope.

    A preset rather than a plugin. What varies between providers is small and static, and a
    plugin kind for it would be a contract, a registry and a manifest around two URLs.
    """

    name: str
    label: object
    authorise_url: str
    token_url: str
    #: What is asked for. Kept as the provider writes it, because that is what a person
    #: comparing this against a provider's documentation is going to read.
    scopes: tuple[str, ...]
    #: Whether this provider will let an application send with nobody consenting.
    application_grant: bool = False
    #: Whether the token endpoint wants a tenant in its path.
    needs_tenant: bool = False
    #: The scope a client-credentials grant asks for, where it differs.
    application_scopes: tuple[str, ...] = ()
    #: The host its SMTP is on, offered as a suggestion and never enforced.
    smtp_host: str = ""
    smtp_port: int = 587
    extra: dict = field(default_factory=dict)

    def authorise(self, tenant: str = "") -> str:
        return self.authorise_url.format(tenant=tenant or "common")

    def token(self, tenant: str = "") -> str:
        return self.token_url.format(tenant=tenant or "common")

    def scope_for(self, grant: str) -> str:
        wanted = self.application_scopes if grant == MailGrant.APPLICATION else self.scopes
        return " ".join(wanted or self.scopes)


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        name="google",
        label="Google",
        authorise_url="https://accounts.google.com/o/oauth2/v2/auth",
        # A public endpoint address, not a credential.
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106
        scopes=("https://mail.google.com/",),
        smtp_host="smtp.gmail.com",
        # Google issues a refresh token only when asked offline, and issues one *again* for
        # somebody who has already agreed only when the consent screen is forced.
        extra={"access_type": "offline", "prompt": "consent"},
    ),
    "microsoft": Provider(
        name="microsoft",
        label="Microsoft 365",
        authorise_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",  # noqa: S106
        scopes=(
            "https://outlook.office.com/SMTP.Send",
            # Without it the provider issues no refresh token, and mail stops in an hour.
            "offline_access",
        ),
        application_grant=True,
        needs_tenant=True,
        application_scopes=("https://outlook.office365.com/.default",),
        smtp_host="smtp.office365.com",
    ),
}


def provider_choices() -> list[tuple[str, object]]:
    return [(provider.name, provider.label) for provider in PROVIDERS.values()]


def provider(name: str) -> Provider | None:
    return PROVIDERS.get(name or "")


# ----------------------------------------------------------------- the mechanism


def sasl_xoauth2(username: str, token: str) -> str:
    """The SASL initial response, unencoded.

    `smtplib.SMTP.auth` base64-encodes what the callable returns, the same way it does for
    PLAIN, so returning an already-encoded string here would send it twice encoded and get a
    refusal that says nothing useful.
    """
    return f"user={username}\1auth=Bearer {token}\1\1"


def authenticate(server, *, username: str, password: str = "", token: str = "") -> None:
    """Sign in, whichever way this session is signing in.

    One function so that the connection check and the sending backend cannot disagree about
    it. A token wins where both are present: somebody who has configured XOAUTH2 and left an
    old password in the box means the token, and quietly trying the password first would send
    a credential the provider is about to stop accepting.
    """
    if token:
        server.auth(
            "XOAUTH2",
            lambda challenge=None: sasl_xoauth2(username, token),
            initial_response_ok=True,
        )
    elif username:
        server.login(username, password)


# ------------------------------------------------- the instance's own token

#: How long before an access token expires it is treated as expired. A token that dies in
#: the middle of a send is a failure nobody can explain, and a minute costs nothing. The
#: same margin `plugins/consent.py` uses, for the same reason.
REFRESH_MARGIN = 60


class TokenUnavailable(Exception):
    """No token could be fetched, so this instance cannot authenticate to its mail server."""


def instance_token(config: dict | None = None, *, refresh: bool = True) -> str:
    """A bearer token for the instance's own mailbox, fetched or reused.

    ``config`` is the resolved email settings -- environment first, then the Email page --
    so a variable pinning the provider or the client id is honoured here exactly as it is
    for the host. The tokens themselves are only ever the row's: they are what a provider
    handed back, not something an operator types.

    Cached in the same encrypted store the client secret lives in rather than in Django's
    cache: a bearer token is a credential, and a credential in a cache table is a credential
    in a place this codebase has deliberately kept clear of them.

    **This is what makes a password reset arrive**, so the failure it can produce is named
    rather than left to surface as an SMTP refusal nobody can read. A grant a provider will
    not renew is `TokenUnavailable` with the provider's own words, and the send fails the way
    any other send fails -- which `mail_delivers()` counts (#152).
    """
    import time

    from . import site
    from .models import SiteSettings

    config = config if config is not None else site.email_settings()
    chosen = provider(str(config.get("oauth_provider") or ""))
    if chosen is None:
        raise TokenUnavailable(str(_("No identity provider is chosen for mail.")))

    row = SiteSettings.get()
    try:
        held = row.email_oauth_secrets
    except Exception as error:  # the Fernet key was rotated without the field key
        raise TokenUnavailable(
            str(_("The stored mail credentials cannot be read on this instance."))
        ) from error

    token = str(held.get("access_token") or "")
    expires_at = float(held.get("expires_at") or 0)
    if token and expires_at - REFRESH_MARGIN > time.time():
        return token
    if not refresh:
        return token

    grant = str(config.get("oauth_grant") or MailGrant.MAILBOX)
    if grant == MailGrant.APPLICATION:
        if not chosen.application_grant:
            raise TokenUnavailable(
                str(_("%(provider)s does not let an application send on its own."))
                % {"provider": chosen.label}
            )
        form = {"grant_type": "client_credentials", "scope": chosen.scope_for(grant)}
    else:
        stored_refresh = str(held.get("refresh_token") or "")
        if not stored_refresh:
            raise TokenUnavailable(
                str(_("Nobody has signed in to the mail provider yet. Connect it first."))
            )
        form = {"grant_type": "refresh_token", "refresh_token": stored_refresh}

    from postulo.plugins import consent

    try:
        answer = consent.exchange(
            chosen.token(str(config.get("oauth_tenant") or "")),
            client_id=str(config.get("oauth_client_id") or ""),
            client_secret=str(config.get("oauth_client_secret") or ""),
            form=form,
        )
    except consent.ConsentFailed as error:
        raise TokenUnavailable(str(error)) from error

    keep_tokens(row, answer)
    return str(answer.get("access_token") or "")


def keep_tokens(row, answer: dict) -> None:
    """Store what a provider handed back, keeping a refresh token it did not replace.

    A provider may or may not issue a new refresh token on a renewal; throwing away the old
    one when it does not is the difference between a mail configuration that lasts and one
    that dies quietly some weeks later.
    """
    import time

    try:
        secrets = dict(row.email_oauth_secrets)
    except Exception:
        secrets = {}
    if answer.get("access_token"):
        secrets["access_token"] = str(answer["access_token"])
    if answer.get("refresh_token"):
        secrets["refresh_token"] = str(answer["refresh_token"])
    try:
        lifetime = int(answer.get("expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    secrets["expires_at"] = time.time() + lifetime if lifetime else 0
    row.email_oauth_secrets = secrets
    row.save(update_fields=["email_oauth_secrets_encrypted", "updated_at"])


# ------------------------------------------- signing in once, from the Email page

#: A salt of its own, so a state value made for a person's connection can never be read as
#: one for the instance's mail, or the other way round. Both return to the same address.
CONSENT_SALT = "postulo.mail.consent"


def start_consent(request, config: dict | None = None) -> str:
    """Where to send an administrator to agree, as the mailbox the instance sends from.

    Back through the one callback address the whole instance registers, which is what #148
    argued for and what an operator would otherwise be asked to register twice.
    """
    from urllib.parse import urlencode

    from django.core import signing

    from postulo.plugins import consent

    from . import site

    config = config if config is not None else site.email_settings()
    chosen = provider(str(config.get("oauth_provider") or ""))
    if chosen is None:
        raise TokenUnavailable(str(_("Choose an identity provider and save it first.")))
    client_id = str(config.get("oauth_client_id") or "")
    if not client_id:
        raise TokenUnavailable(
            str(_("Register this instance with the provider first, and save the client ID."))
        )
    state = signing.dumps({"by": request.user.pk}, salt=CONSENT_SALT)
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": consent.callback_url(request),
        "scope": chosen.scope_for(MailGrant.MAILBOX),
        "state": state,
        **chosen.extra,
    }
    return f"{chosen.authorise(str(config.get('oauth_tenant') or ''))}?{urlencode(query)}"


def is_mail_consent(state: str) -> bool:
    """Whether a callback's state was made by `start_consent`, rather than for a connection."""
    from django.core import signing

    from postulo.plugins import consent

    try:
        signing.loads(state, salt=CONSENT_SALT, max_age=consent.STATE_MAX_AGE)
    except signing.BadSignature:
        return False
    return True


def finish_consent(request, code: str, state: str) -> None:
    """Exchange the code and keep the refresh token for the instance's mail.

    The state is checked against the administrator signed in now, and they have to still be
    one: a callback is a request somebody else's page can cause, and the instance's own
    mailbox is the one credential here that sends password resets.
    """
    from django.core import signing

    from postulo.plugins import consent

    from . import site

    try:
        payload = signing.loads(state, salt=CONSENT_SALT, max_age=consent.STATE_MAX_AGE)
    except signing.BadSignature as error:
        raise TokenUnavailable(
            str(_("That reply did not come from a request Postulo made."))
        ) from error
    user = request.user
    if payload.get("by") != getattr(user, "pk", None) or not getattr(user, "is_staff", False):
        raise TokenUnavailable(str(_("Only the administrator who started this can finish it.")))

    config = site.email_settings()
    chosen = provider(str(config.get("oauth_provider") or ""))
    if chosen is None:
        raise TokenUnavailable(str(_("No identity provider is chosen for mail.")))
    try:
        answer = consent.exchange(
            chosen.token(str(config.get("oauth_tenant") or "")),
            client_id=str(config.get("oauth_client_id") or ""),
            client_secret=str(config.get("oauth_client_secret") or ""),
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": consent.callback_url(request),
            },
        )
    except consent.ConsentFailed as error:
        raise TokenUnavailable(str(error)) from error
    if not answer.get("refresh_token"):
        # Without one, mail works for an hour and then stops for good, on a path nobody
        # watches. Refused now rather than discovered at the first password reset.
        raise TokenUnavailable(
            str(
                _(
                    "The provider sent no refresh token, so mail would stop within the hour. "
                    "Check that offline access is allowed for this application."
                )
            )
        )
    from .models import SiteSettings

    keep_tokens(SiteSettings.get(), answer)


def forget_consent() -> None:
    """Drop the tokens. The client secret stays; only the provider can revoke a grant."""
    from .models import SiteSettings

    row = SiteSettings.get()
    try:
        held = dict(row.email_oauth_secrets)
    except Exception:
        held = {}
    for key in ("access_token", "refresh_token", "expires_at"):
        held.pop(key, None)
    row.email_oauth_secrets = held
    row.save(update_fields=["email_oauth_secrets_encrypted", "updated_at"])
