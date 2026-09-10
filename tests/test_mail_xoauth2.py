"""SMTP that Google and Microsoft will still accept (#151).

> i want that the smtp internal plugin (both sides) can handle newer authntification
> protocols like the ones deployed by google or microsoft365

Microsoft disables SMTP AUTH basic authentication by default for existing Exchange Online
tenants at the end of December 2026; afterwards only XOAUTH2 is accepted. On the code before
this, an instance sending through Microsoft 365 loses password resets on that date with no
configuration that fixes it.

What is held here, in the order the issue raised it:

- **the mechanism** -- XOAUTH2 is one SASL string, and getting its encoding wrong is a
  refusal that says nothing useful;
- **the instance's half** -- two grants, because the operator of a server and the owner of a
  mailbox are consenting to different things;
- **a token expires on its own**, and when it does, mail failing has to be *seen* -- by the
  same evidence the recovery lock already reads (#152);
- **the person's half** -- their own consent, through the machinery #148 built and nothing
  had used yet;
- **nothing removes the password**, which is what most instances this is written for use.
"""

from __future__ import annotations

import base64
import time
from unittest import mock

import pytest
from django.core import signing
from django.urls import reverse

from postulo.core import mail, mail_auth, site
from postulo.core.models import SiteSettings
from postulo.plugins import consent

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


class Provider:
    """Stands in for a token endpoint. Records what it was asked, answers what it is told."""

    def __init__(self, answer=None, refuse=None):
        self.answer = answer if answer is not None else {"access_token": "tok", "expires_in": 3600}
        self.refuse = refuse
        self.calls = []

    def __call__(self, token_url, *, client_id, client_secret, form):
        self.calls.append(
            {"url": token_url, "client_id": client_id, "secret": client_secret, **form}
        )
        if self.refuse:
            raise self.refuse
        return dict(self.answer)


def configure(**fields):
    row = SiteSettings.get()
    secrets = fields.pop("secrets", None)
    for name, value in fields.items():
        setattr(row, name, value)
    if secrets is not None:
        row.email_oauth_secrets = secrets
    row.save()
    return row


def microsoft_application(**extra):
    return configure(
        email_auth="xoauth2",
        email_oauth_provider="microsoft",
        email_oauth_grant="application",
        email_oauth_tenant="contoso-tenant",
        email_oauth_client_id="app-id",
        **{"secrets": {"client_secret": "shh"}, **extra},
    )


class FakeServer:
    """Just enough of `smtplib.SMTP` for `auth()` to run without a socket."""

    def __init__(self):
        import smtplib

        self._real = smtplib.SMTP()
        self._real.ehlo_resp = b"mail.example.org"
        self.sent = []
        self.logins = []

        def docmd(command, argument=""):
            self.sent.append((command, argument))
            return 235, b"2.7.0 Accepted"

        self._real.docmd = docmd

    def auth(self, *args, **kwargs):
        return self._real.auth(*args, **kwargs)

    def login(self, username, password):
        self.logins.append((username, password))


# ------------------------------------------------------------------ the mechanism


def test_the_sasl_string_is_the_one_the_providers_document():
    assert mail_auth.sasl_xoauth2("a@example.org", "T") == "user=a@example.org\1auth=Bearer T\1\1"


def test_it_goes_on_the_wire_encoded_once():
    """`smtplib` base64-encodes what the callable returns. Encoding it here as well would
    send it twice encoded, and the refusal that follows says nothing about why.
    """
    server = FakeServer()

    mail_auth.authenticate(server, username="a@example.org", token="T")

    command, argument = server.sent[-1]
    mechanism, encoded = argument.split(" ", 1)
    assert (command, mechanism) == ("AUTH", "XOAUTH2")
    assert base64.b64decode(encoded).decode() == "user=a@example.org\1auth=Bearer T\1\1"


def test_a_token_wins_over_a_password_left_in_the_box():
    """Trying the password first would send a credential the provider is about to stop
    accepting, and fail for the wrong reason."""
    server = FakeServer()

    mail_auth.authenticate(server, username="a@example.org", password="old", token="T")

    assert server.logins == []


def test_a_password_still_signs_in_the_way_it_always_did():
    server = FakeServer()

    mail_auth.authenticate(server, username="a@example.org", password="secret")

    assert server.logins == [("a@example.org", "secret")]
    assert server.sent == []


def test_the_connection_check_signs_in_with_a_token_when_given_one(monkeypatch):
    import ipaddress

    from postulo.core import destinations

    monkeypatch.setattr(
        destinations, "addresses_for", lambda host: [ipaddress.ip_address("93.184.216.34")]
    )
    with mock.patch.object(destinations, "PinnedSMTP") as plain:
        server = plain.return_value.__enter__.return_value
        report = mail.check_connection(
            host="smtp.office365.com",
            port=587,
            username="postulo@contoso.example",
            password="",
            security="starttls",
            timeout=10,
            token="T",
        )

    server.auth.assert_called_once()
    assert server.auth.call_args.args[0] == "XOAUTH2"
    server.login.assert_not_called()
    assert "with a token" in report


def test_the_backend_opens_without_a_password_and_signs_in_with_the_token(monkeypatch):
    """Django authenticates inside `open()`, and only with a password, before it publishes
    the connection -- so a token session is opened with none and signed in straight after.
    """
    from django.core.mail.backends.smtp import EmailBackend

    fake = mock.MagicMock()

    def opened(self):
        self.connection = fake
        return True

    monkeypatch.setattr(EmailBackend, "open", opened)
    monkeypatch.setattr(
        "postulo.core.destinations.approve", lambda host, allow_private=False: "93.184.216.34"
    )
    backend = mail.GuardedBackend(
        alias="default",
        host="smtp.office365.com",
        port=587,
        username="postulo@contoso.example",
        password="an-old-one",
        use_tls=True,
        oauth_token="T",
    )

    assert backend.password == "", "Django must not try the password first"
    assert backend.open() is True
    fake.auth.assert_called_once()
    assert fake.auth.call_args.args[0] == "XOAUTH2"


def test_a_refused_token_closes_the_session_rather_than_leaving_it_open(monkeypatch):
    import smtplib

    from django.core.mail.backends.smtp import EmailBackend

    fake = mock.MagicMock()
    fake.auth.side_effect = smtplib.SMTPAuthenticationError(
        535, b"5.7.3 Authentication unsuccessful"
    )

    def opened(self):
        self.connection = fake
        return True

    monkeypatch.setattr(EmailBackend, "open", opened)
    monkeypatch.setattr(
        "postulo.core.destinations.approve", lambda host, allow_private=False: "93.184.216.34"
    )
    backend = mail.GuardedBackend(
        alias="default", host="smtp.office365.com", port=587, username="p", oauth_token="T"
    )

    with pytest.raises(smtplib.SMTPAuthenticationError):
        backend.open()
    assert backend.connection is None


# ------------------------------------------------------------ nothing is removed


def test_an_instance_that_never_opened_the_page_still_signs_in_with_a_password():
    assert site.email_settings()["auth"] == "password"


def test_the_transport_asks_for_no_token_when_signing_in_by_password():
    from postulo.plugins.smtp import _token

    assert _token({"auth": "password"}) == ""
    assert _token({}) == ""


# --------------------------------------------------------- the instance's two grants


def test_the_application_grant_asks_microsoft_for_the_default_scope(monkeypatch):
    """Client credentials: nobody's session involved, the server's own route."""
    provider = Provider()
    monkeypatch.setattr(consent, "exchange", provider)
    microsoft_application()

    token = mail_auth.instance_token()

    assert token == "tok"
    call = provider.calls[0]
    assert call["grant_type"] == "client_credentials"
    assert call["scope"] == "https://outlook.office365.com/.default"
    assert "contoso-tenant" in call["url"]
    assert (call["client_id"], call["secret"]) == ("app-id", "shh")


def test_a_token_still_good_is_reused_rather_than_fetched_again(monkeypatch):
    """One HTTPS round trip per message would be a second outbound request on the path that
    delivers password resets, for nothing."""
    provider = Provider()
    monkeypatch.setattr(consent, "exchange", provider)
    microsoft_application()

    mail_auth.instance_token()
    mail_auth.instance_token()

    assert len(provider.calls) == 1


def test_a_token_about_to_expire_is_renewed_first(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(consent, "exchange", provider)
    microsoft_application(
        secrets={"client_secret": "shh", "access_token": "old", "expires_at": time.time() + 30}
    )

    assert mail_auth.instance_token() == "tok", "thirty seconds is inside the margin"


def test_the_token_is_kept_encrypted_rather_than_in_a_column_or_a_cache(monkeypatch):
    monkeypatch.setattr(consent, "exchange", Provider())
    microsoft_application()

    mail_auth.instance_token()

    row = SiteSettings.get()
    assert "tok" not in row.email_oauth_secrets_encrypted
    assert row.email_oauth_secrets["access_token"] == "tok"
    assert row.email_oauth_secrets["client_secret"] == "shh", "and nothing else was lost"


def test_the_mailbox_grant_renews_with_the_refresh_token(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(consent, "exchange", provider)
    configure(
        email_auth="xoauth2",
        email_oauth_provider="google",
        email_oauth_grant="mailbox",
        email_oauth_client_id="app-id",
        secrets={"client_secret": "shh", "refresh_token": "keep-me"},
    )

    mail_auth.instance_token()

    assert provider.calls[0]["grant_type"] == "refresh_token"
    assert provider.calls[0]["refresh_token"] == "keep-me"


def test_a_refresh_token_the_provider_did_not_replace_is_kept(monkeypatch):
    """Throwing it away is the difference between mail that lasts and mail that stops some
    weeks later, quietly."""
    monkeypatch.setattr(consent, "exchange", Provider({"access_token": "new", "expires_in": 60}))
    configure(
        email_auth="xoauth2",
        email_oauth_provider="google",
        email_oauth_client_id="app-id",
        secrets={"client_secret": "shh", "refresh_token": "keep-me"},
    )

    mail_auth.instance_token()

    assert SiteSettings.get().email_oauth_secrets["refresh_token"] == "keep-me"


def test_nobody_having_signed_in_is_said_in_words(monkeypatch):
    monkeypatch.setattr(consent, "exchange", Provider())
    configure(
        email_auth="xoauth2",
        email_oauth_provider="google",
        email_oauth_grant="mailbox",
        email_oauth_client_id="app-id",
    )

    with pytest.raises(mail_auth.TokenUnavailable, match="Nobody has signed in"):
        mail_auth.instance_token()


def test_google_has_no_application_grant_and_says_so(monkeypatch):
    """Its equivalent is a service account, a different grant again. Offered and broken
    would fail at the first password reset, which is the worst moment there is."""
    monkeypatch.setattr(consent, "exchange", Provider())
    configure(email_auth="xoauth2", email_oauth_provider="google", email_oauth_grant="application")

    with pytest.raises(mail_auth.TokenUnavailable, match="does not let an application"):
        mail_auth.instance_token()


def test_the_page_refuses_that_pair_before_it_is_saved():
    from postulo.core.server_forms import EmailForm

    form = EmailForm(
        data={
            "email_host": "smtp.gmail.com",
            "email_port": "587",
            "email_security": "starttls",
            "email_timeout": "10",
            "email_auth": "xoauth2",
            "email_oauth_provider": "google",
            "email_oauth_grant": "application",
        },
        instance=SiteSettings.get(),
    )

    assert not form.is_valid()
    assert "email_oauth_grant" in form.errors


def test_the_environment_can_pin_all_of_it(monkeypatch, settings):
    """A container operator sets these in a file, the way they set the host."""
    monkeypatch.setenv("POSTULO_EMAIL_AUTH", "xoauth2")
    monkeypatch.setenv("POSTULO_EMAIL_OAUTH_PROVIDER", "microsoft")
    settings.POSTULO_EMAIL_AUTH = "xoauth2"
    settings.POSTULO_EMAIL_OAUTH_PROVIDER = "microsoft"
    configure(email_auth="password", email_oauth_provider="google")

    resolved = site.email_settings()

    assert (resolved["auth"], resolved["oauth_provider"]) == ("xoauth2", "microsoft")


# ------------------------------------------ a token expires on its own, visibly


def test_a_grant_the_provider_will_not_renew_is_a_mail_failure_the_lock_can_see(monkeypatch):
    """A password does not expire and a refresh token can. When it does, the send fails --
    and it has to fail *where the recovery lock reads*, or the lock goes on counting email as
    a way back into an account after it has stopped being one (#152).
    """
    from django.core import mail as django_mail

    monkeypatch.setattr(
        consent,
        "exchange",
        Provider(refuse=consent.ConsentWithdrawn("The provider will not renew this.")),
    )
    configure(
        email_host="smtp.gmail.com",
        email_auth="xoauth2",
        email_oauth_provider="google",
        email_oauth_client_id="app-id",
        secrets={"client_secret": "shh", "refresh_token": "revoked"},
    )
    before = SiteSettings.get().mail_failures

    with (
        mock.patch.object(django_mail, "get_connection", wraps=django_mail.get_connection),
        pytest.raises(mail_auth.TokenUnavailable),
    ):
        from postulo.notifications.transport import PluggableBackend

        PluggableBackend(alias="default").send_messages(
            [django_mail.EmailMessage("s", "b", "p@example.org", ["x@example.org"])]
        )

    row = SiteSettings.get()
    assert row.mail_failures == before + 1
    assert "will not renew" in row.mail_last_error


# ------------------------------------------------------ signing in once, as the mailbox


def mailbox(provider_name="google", **extra):
    return configure(
        email_auth="xoauth2",
        email_oauth_provider=provider_name,
        email_oauth_grant="mailbox",
        email_oauth_client_id="app-id",
        **{"secrets": {"client_secret": "shh"}, **extra},
    )


def test_signing_in_starts_at_the_provider_with_the_one_registered_callback(client, admin):
    """One address for the whole instance, the connections one, so an operator registers it
    once rather than once per half (#148)."""
    mailbox()
    client.force_login(admin)

    response = client.post(reverse("server:email_consent"))

    where = response["Location"]
    assert where.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=app-id" in where
    assert "access_type=offline" in where, "or Google issues no refresh token at all"
    assert "connections%2Fconsent%2F" in where or "consent" in where


def test_microsoft_is_asked_for_offline_access_or_mail_stops_in_an_hour(client, admin):
    mailbox("microsoft", email_oauth_tenant="contoso-tenant")
    client.force_login(admin)

    where = client.post(reverse("server:email_consent"))["Location"]

    assert "contoso-tenant" in where
    assert "offline_access" in where


def _state_for(user):
    return signing.dumps({"by": user.pk}, salt=mail_auth.CONSENT_SALT)


def test_the_provider_coming_back_keeps_the_refresh_token(client, admin, monkeypatch):
    monkeypatch.setattr(
        consent, "exchange", Provider({"access_token": "a", "refresh_token": "r", "expires_in": 60})
    )
    mailbox()
    client.force_login(admin)

    response = client.get(
        reverse("connections:consent_callback"), {"code": "c", "state": _state_for(admin)}
    )

    assert response["Location"] == reverse("server:email")
    assert SiteSettings.get().email_oauth_secrets["refresh_token"] == "r"


def test_no_refresh_token_is_refused_now_rather_than_discovered_in_an_hour(
    client, admin, monkeypatch
):
    monkeypatch.setattr(consent, "exchange", Provider({"access_token": "a", "expires_in": 60}))
    mailbox()
    client.force_login(admin)

    client.get(reverse("connections:consent_callback"), {"code": "c", "state": _state_for(admin)})

    assert not SiteSettings.get().has_email_consent


def test_somebody_who_is_not_an_administrator_cannot_finish_it(client, user, admin, monkeypatch):
    """A callback is a request somebody else's page can cause, and this is the credential
    that sends password resets."""
    provider = Provider({"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(consent, "exchange", provider)
    mailbox()
    client.force_login(user)

    client.get(reverse("connections:consent_callback"), {"code": "c", "state": _state_for(user)})

    assert provider.calls == []
    assert not SiteSettings.get().has_email_consent


def test_an_administrator_cannot_finish_one_another_started(
    client, admin, django_user_model, monkeypatch
):
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="x-long-password-9", is_staff=True
    )
    provider = Provider({"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(consent, "exchange", provider)
    mailbox()
    client.force_login(other)

    client.get(reverse("connections:consent_callback"), {"code": "c", "state": _state_for(admin)})

    assert provider.calls == []


def test_a_connections_state_is_never_read_as_the_instances(admin):
    """Two flows, one address; a salt each, so neither can be passed off as the other."""
    connection_state = signing.dumps({"connection": 1, "owner": admin.pk})

    assert not mail_auth.is_mail_consent(connection_state)
    assert mail_auth.is_mail_consent(_state_for(admin))


def test_forgetting_the_grant_keeps_the_client_secret(client, admin):
    mailbox(secrets={"client_secret": "shh", "refresh_token": "r", "access_token": "a"})
    client.force_login(admin)

    client.post(reverse("server:email_consent"), {"forget": "1"})

    held = SiteSettings.get().email_oauth_secrets
    assert held == {"client_secret": "shh"}


# ----------------------------------------------------------- the Email page itself


def test_the_client_secret_is_write_only(client, admin):
    mailbox()
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "shh" not in html
    assert "A client secret is stored" in html


def test_a_blank_client_secret_keeps_the_one_stored(admin):
    from postulo.core.server_forms import EmailForm

    mailbox()
    form = EmailForm(
        data={
            "email_host": "smtp.gmail.com",
            "email_port": "587",
            "email_security": "starttls",
            "email_timeout": "10",
            "email_auth": "xoauth2",
            "email_oauth_provider": "google",
            "email_oauth_grant": "mailbox",
            "email_oauth_client_id": "app-id",
            "email_oauth_client_secret": "",
        },
        instance=SiteSettings.get(),
    )
    assert form.is_valid(), form.errors
    form.save()

    assert SiteSettings.get().email_oauth_secrets["client_secret"] == "shh"


def test_the_page_shows_the_address_to_register(client, admin):
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert reverse("connections:consent_callback") in html


def test_the_sign_in_button_appears_only_for_the_mailbox_grant(client, admin):
    client.force_login(admin)
    assert "data-mail-consent" not in client.get(reverse("server:email")).content.decode()

    mailbox()
    assert "data-mail-consent" in client.get(reverse("server:email")).content.decode()


# ------------------------------------------------------------- the person's half


def test_a_persons_own_server_needs_no_consent():
    from postulo.plugins.own_mail import OwnMail

    assert consent.wanted_by(OwnMail(), {"host": "mail.example.org"}) is None


def test_choosing_a_provider_asks_that_provider():
    from postulo.plugins.own_mail import OwnMail

    wanted = consent.wanted_by(OwnMail(), {"sign_in": "microsoft", "tenant": "contoso"})

    assert "contoso" in wanted.authorise_url
    assert "offline_access" in wanted.scopes


def test_a_plugin_whose_needs_consent_takes_nothing_is_asked_as_before():
    class Old:
        def needs_consent(self):
            return consent.Consent(authorise_url="a", token_url="t", scopes=("s",))

    assert consent.wanted_by(Old(), {"anything": 1}).token_url == "t"


def test_a_persons_mail_leaves_with_a_fresh_token(user, monkeypatch):
    """Renewed on the way out, where the connection is to hand, and handed to the backend."""
    from postulo.core import correspondence
    from postulo.plugins.models import Connection

    connection = Connection.objects.create(
        owner=user,
        kind="outbox",
        plugin="own-mail",
        label="Mine",
        config={
            "from_address": "me@example.org",
            "host": "smtp.office365.com",
            "sign_in": "microsoft",
            "client_id": "app-id",
            "username": "me@example.org",
        },
    )
    connection.secrets = {"client_secret": "shh", consent.REFRESH_TOKEN: "r"}
    connection.save()
    monkeypatch.setattr(
        consent, "exchange", Provider({"access_token": "fresh", "expires_in": 3600})
    )
    monkeypatch.setattr(correspondence, "connection_for", lambda person: connection)

    seen = {}

    class Backend:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def send_messages(self, messages):
            return len(messages)

    monkeypatch.setattr("postulo.core.mail.GuardedBackend", Backend)
    from django.core.mail import EmailMessage

    correspondence.send(user, EmailMessage("s", "b", "", ["x@example.org"]))

    assert seen["oauth_token"] == "fresh"
    assert seen["password"] == ""
