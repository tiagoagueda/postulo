"""Implicit TLS, and the two ways of putting TLS on an SMTP session (#158).

There are two, and they are not interchangeable. **STARTTLS** connects in the clear, says
`EHLO`, and asks the server to upgrade the socket — ports 587 and 25. **Implicit TLS**, or
SMTPS, hands over a certificate before a byte of SMTP is spoken — port 465. Postulo did only
the first, so a provider whose documented settings say 465 could not be configured at all:
the connection sat waiting for a greeting the server would never send, and timed out ten
seconds later saying `timed out`, which is true and useless.

Three things here are the point.

**One control, not two checkboxes.** Django's SMTP backend raises when `use_tls` and
`use_ssl` are both set, and rightly — they are alternatives, not layers. A field each offers
a pair that cannot be saved; one field with three states cannot express it.

**Nobody's setting is rewritten.** The column that held a boolean is carried into the new one
by a migration, and `POSTULO_EMAIL_USE_TLS` goes on meaning what it meant for every `.env`
that sets it.

**A mismatch says which mismatch.** Both directions fail identically — a wait, then a
disconnection — and pointing one at the other's port is the ordinary mistake, not an exotic
one.
"""

from __future__ import annotations

import smtplib
import ssl
from unittest import mock

import pytest
from django.urls import reverse

from postulo.core import mail, site
from postulo.core.models import DEFAULT_MAIL_PORTS, MailSecurity, SiteSettings
from postulo.core.server_forms import EmailForm

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


# ------------------------------------------------------- which socket gets opened


def test_implicit_tls_opens_a_tls_socket_from_the_start():
    """The whole of the bug: there was no code path that could do this."""
    with mock.patch.object(smtplib, "SMTP_SSL") as secure, mock.patch.object(smtplib, "SMTP"):
        mail.check_connection(
            host="mail.example.org", port=465, username="", password="", security="ssl", timeout=5
        )

    assert secure.called, "port 465 needs the handshake before any SMTP"
    assert isinstance(secure.call_args.kwargs["context"], ssl.SSLContext)


def test_starttls_opens_a_plain_socket_and_upgrades_it():
    with mock.patch.object(smtplib, "SMTP") as plain, mock.patch.object(smtplib, "SMTP_SSL"):
        server = plain.return_value.__enter__.return_value
        server.has_extn.return_value = True
        mail.check_connection(
            host="mail.example.org",
            port=587,
            username="",
            password="",
            security="starttls",
            timeout=5,
        )

    assert plain.called
    assert server.starttls.called


def test_no_security_never_upgrades():
    with mock.patch.object(smtplib, "SMTP") as plain, mock.patch.object(smtplib, "SMTP_SSL") as s:
        server = plain.return_value.__enter__.return_value
        mail.check_connection(
            host="mail.example.org", port=25, username="", password="", security="none", timeout=5
        )

    assert plain.called and not s.called
    assert not server.starttls.called


def test_a_server_without_starttls_is_told_so_rather_than_connected_to_in_the_clear():
    with mock.patch.object(smtplib, "SMTP") as plain:
        plain.return_value.__enter__.return_value.has_extn.return_value = False
        with pytest.raises(mail.ConnectionFailed) as raised:
            mail.check_connection(
                host="mail.example.org",
                port=587,
                username="",
                password="",
                security="starttls",
                timeout=5,
            )

    assert "STARTTLS" in str(raised.value)


# ------------------------------------------------ a failure that names the mismatch


def test_plain_settings_against_465_say_what_is_wrong():
    """Ten seconds of `timed out` is the worst version of this."""
    with mock.patch.object(smtplib, "SMTP", side_effect=TimeoutError("timed out")):
        with pytest.raises(mail.ConnectionFailed) as raised:
            mail.check_connection(
                host="ssl0.example.net",
                port=465,
                username="",
                password="",
                security="starttls",
                timeout=1,
            )

    message = str(raised.value)
    assert "465" in message and "first byte" in message
    assert "timed out" in message, "the original is kept; the hint is added in front of it"


def test_implicit_tls_against_587_says_the_other_thing():
    with mock.patch.object(smtplib, "SMTP_SSL", side_effect=TimeoutError("timed out")):
        with pytest.raises(mail.ConnectionFailed) as raised:
            mail.check_connection(
                host="mail.example.org",
                port=587,
                username="",
                password="",
                security="ssl",
                timeout=1,
            )

    assert "STARTTLS" in str(raised.value)


def test_an_unconventional_port_is_not_second_guessed():
    """A relay on a port of its own is ordinary for a self-hosted instance."""
    with mock.patch.object(smtplib, "SMTP", side_effect=TimeoutError("timed out")):
        with pytest.raises(mail.ConnectionFailed) as raised:
            mail.check_connection(
                host="mail.example.org",
                port=2525,
                username="",
                password="",
                security="starttls",
                timeout=1,
            )

    assert str(raised.value) == "TimeoutError: timed out", "nothing to say, so it says nothing"


# ------------------------------------------------------------ what reaches Django


def test_the_send_path_sets_use_ssl_and_never_both():
    from postulo.notifications.smtp import SMTPTransport

    with mock.patch("postulo.notifications.smtp.EmailBackend") as backend:
        SMTPTransport().deliver([], {"host": "h", "port": 465, "security": "ssl"})

    kwargs = backend.call_args.kwargs
    assert kwargs["use_ssl"] is True and kwargs["use_tls"] is False


def test_the_send_path_sets_use_tls_for_starttls():
    from postulo.notifications.smtp import SMTPTransport

    with mock.patch("postulo.notifications.smtp.EmailBackend") as backend:
        SMTPTransport().deliver([], {"host": "h", "port": 587, "security": "starttls"})

    kwargs = backend.call_args.kwargs
    assert kwargs["use_tls"] is True and kwargs["use_ssl"] is False


def test_neither_flag_is_set_without_a_choice():
    from postulo.notifications.smtp import SMTPTransport

    with mock.patch("postulo.notifications.smtp.EmailBackend") as backend:
        SMTPTransport().deliver([], {"host": "h", "port": 25, "security": "none"})

    kwargs = backend.call_args.kwargs
    assert kwargs["use_tls"] is False and kwargs["use_ssl"] is False


# --------------------------------------------------------- the environment still wins


def test_the_older_boolean_still_means_what_it_meant(settings, monkeypatch):
    """Every existing `.env` sets it, and none of them can be edited from here."""
    monkeypatch.setenv("POSTULO_EMAIL_USE_TLS", "true")
    settings.POSTULO_EMAIL_SECURITY = "starttls"
    SiteSettings.objects.update_or_create(pk=1, defaults={"email_security": "ssl"})

    assert site.overridden_by("email_security") == "POSTULO_EMAIL_USE_TLS"
    assert site.email_settings()["security"] == "starttls", "the environment wins, as ever"


def test_the_newer_variable_wins_where_both_are_given(monkeypatch):
    monkeypatch.setenv("POSTULO_EMAIL_USE_TLS", "true")
    monkeypatch.setenv("POSTULO_EMAIL_SECURITY", "ssl")

    assert site.overridden_by("email_security") == "POSTULO_EMAIL_SECURITY"


def test_nothing_in_the_environment_leaves_the_stored_choice_alone(settings):
    SiteSettings.objects.update_or_create(pk=1, defaults={"email_security": "ssl"})

    assert site.overridden_by("email_security") is None
    assert site.email_settings()["security"] == "ssl"


def test_every_pinning_variable_is_still_reachable_as_a_flat_list():
    """A field naming two must not break anything that iterates the whole set."""
    variables = site.env_variables()

    assert "POSTULO_EMAIL_SECURITY" in variables
    assert "POSTULO_EMAIL_USE_TLS" in variables
    assert all(isinstance(name, str) for name in variables)


# --------------------------------------------------------------------- the form


def test_the_form_offers_three_states_and_an_empty_one():
    form = EmailForm(instance=SiteSettings.get())

    offered = [value for value, _label in form.fields["email_security"].choices]
    assert offered == ["", "none", "starttls", "ssl"]


def test_an_empty_port_takes_the_one_that_choice_normally_uses():
    form = EmailForm(
        data={
            "email_host": "mail.example.org",
            "email_port": "",
            "email_username": "",
            "email_security": "ssl",
            "email_timeout": "10",
            "email_from": "postulo@example.org",
        },
        instance=SiteSettings.get(),
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email_port"] == 465 == DEFAULT_MAIL_PORTS[MailSecurity.SSL]


def test_a_typed_port_is_never_corrected():
    form = EmailForm(
        data={
            "email_host": "mail.example.org",
            "email_port": "2525",
            "email_username": "",
            "email_security": "ssl",
            "email_timeout": "10",
            "email_from": "postulo@example.org",
        },
        instance=SiteSettings.get(),
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email_port"] == 2525


def test_the_page_saves_the_choice(client, admin):
    client.force_login(admin)

    client.post(
        reverse("server:email"),
        {
            "email_host": "ssl0.example.net",
            "email_port": "465",
            "email_username": "somebody@example.org",
            "email_security": "ssl",
            "email_timeout": "10",
            "email_from": "postulo@example.org",
        },
    )

    assert SiteSettings.get().email_security == "ssl"


def test_the_page_names_the_choice_rather_than_showing_a_code(client, admin):
    SiteSettings.objects.update_or_create(pk=1, defaults={"email_security": "ssl"})
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "TLS from the first byte" in html
    assert ">ssl<" not in html, "a code somebody has to look up"


def test_the_connection_test_carries_what_is_on_screen(client, admin, monkeypatch):
    seen = {}
    monkeypatch.setattr(mail, "check_connection", lambda **kw: seen.update(kw) or "ok")
    client.force_login(admin)

    client.post(
        reverse("server:email_connection_test"),
        {
            "email_host": "ssl0.example.net",
            "email_port": "465",
            "email_username": "",
            "email_security": "ssl",
            "email_timeout": "10",
        },
    )

    assert seen["security"] == "ssl"


# ------------------------------------------------------------------ the migration


def test_the_migration_adds_before_it_removes_and_carries_in_between():
    """Order is what stops it losing everybody's setting.

    Django's autodetector wanted the drop first, which is correct as a schema change: an
    instance running STARTTLS on 587 would come back saying nothing was chosen, fall through
    to the environment, and start deciding by a variable most people have never set.
    """
    import importlib

    module = importlib.import_module("postulo.core.migrations.0011_mail_security")
    operations = module.Migration.operations

    assert [type(op).__name__ for op in operations] == [
        "AddField",
        "RunPython",
        "RemoveField",
    ]
    assert operations[1].reverse_code is not None, "and it can be undone"


def test_the_carry_maps_every_state_of_the_old_column():
    """None kept its meaning: nothing chosen here, so the environment answers."""
    import importlib

    module = importlib.import_module("postulo.core.migrations.0011_mail_security")
    seen = {}

    class FakeQuerySet:
        def __init__(self, key):
            self.key = key

        def update(self, **kwargs):
            seen[self.key] = kwargs

    class FakeManager:
        def filter(self, **kwargs):
            return FakeQuerySet(next(iter(kwargs.values())))

    class FakeApps:
        @staticmethod
        def get_model(app, model):
            return type("SiteSettings", (), {"objects": FakeManager()})

    module.carry_forward(FakeApps, None)

    assert seen[True] == {"email_security": "starttls"}
    assert seen[False] == {"email_security": "none"}
    assert None not in seen, "nothing chosen stays nothing chosen"
