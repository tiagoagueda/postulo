"""Configuring SMTP from the interface, with the environment still winning (#84).

Four things here are worth more than a form test, and each has one below.

The **environment wins and cannot be written over**, whatever a request says. A readonly
input is presentation: a readonly field is still submitted by the browser, and a form can
be posted without a browser at all.

The **password is encrypted at rest**, under the same key as a plugin connection's secrets,
and is never rendered — not the value, not its length.

The settings are **read when a message is sent**, not when the process starts. That is the
whole reason a page can exist at all: `MAILERS` is built once at import, so a configuration
frozen there would mean the page saves, says so, and changes nothing until a restart.

And **removing a variable hands over silently**, which is why the page says so while both
exist.
"""

from __future__ import annotations

import pytest
from django.core import mail as django_mail
from django.urls import reverse

from postulo.core import mail as postulo_mail
from postulo.core import site
from postulo.core.models import SiteSettings
from postulo.plugins import secrets

pytestmark = pytest.mark.django_db

STORED = {
    "email_host": "smtp.stored.example",
    "email_port": "2525",
    "email_username": "stored-user",
    "email_use_tls": "true",
    "email_timeout": "20",
    "email_from": "stored@example.org",
}


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


# ------------------------------------------------------------------ saving and resolving


def test_settings_saved_here_are_the_ones_used(client, admin):
    client.force_login(admin)

    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})

    resolved = site.email_settings()
    assert resolved["host"] == "smtp.stored.example"
    assert resolved["port"] == 2525
    assert resolved["username"] == "stored-user"
    assert resolved["password"] == "hunter2-and-then-some"
    assert resolved["use_tls"] is True
    assert resolved["timeout"] == 20
    assert resolved["from_address"] == "stored@example.org"


def test_nothing_stored_means_the_environment_still_decides(settings):
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"
    settings.POSTULO_EMAIL_PORT = 587
    settings.DEFAULT_FROM_EMAIL = "env@example.org"

    resolved = site.email_settings()

    assert resolved["host"] == "smtp.env.example"
    assert resolved["port"] == 587
    assert resolved["from_address"] == "env@example.org"


# ------------------------------------------------------- the environment wins, and is safe


def test_a_pinned_field_is_shown_readonly_and_says_where_it_comes_from(
    client, admin, monkeypatch, settings
):
    monkeypatch.setenv("POSTULO_EMAIL_HOST", "smtp.env.example")
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert 'data-pinned="email_host"' in html
    assert "smtp.env.example" in html
    assert "POSTULO_EMAIL_HOST" in html
    # readonly, not disabled: a disabled input leaves the tab order.
    assert "readonly" in html
    assert (
        'name="email_host"' in html and "disabled" not in html.split('name="email_host"')[1][:200]
    )


def test_a_pinned_field_cannot_be_written_by_posting_it_anyway(
    client, admin, monkeypatch, settings
):
    """The readonly attribute is a courtesy. This is the rule."""
    monkeypatch.setenv("POSTULO_EMAIL_HOST", "smtp.env.example")
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"
    client.force_login(admin)

    client.post(reverse("server:email"), {**STORED, "email_host": "smtp.attacker.example"})

    assert SiteSettings.get().email_host == ""
    assert site.email_settings()["host"] == "smtp.env.example"


def test_a_pinned_password_is_neither_offered_nor_writable(client, admin, monkeypatch, settings):
    monkeypatch.setenv("POSTULO_EMAIL_HOST_PASSWORD", "from-the-environment")
    settings.POSTULO_EMAIL_HOST_PASSWORD = "from-the-environment"
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()
    assert 'name="email_password"' not in html

    client.post(reverse("server:email"), {**STORED, "email_password": "written-anyway"})

    assert SiteSettings.get().email_password_encrypted == ""
    assert site.email_settings()["password"] == "from-the-environment"


def test_a_stored_value_under_a_pinned_one_is_kept_and_pointed_out(
    client, admin, monkeypatch, settings
):
    """Not overwritten, and not silent: removing the variable would change where mail goes."""
    client.force_login(admin)
    client.post(reverse("server:email"), STORED)

    monkeypatch.setenv("POSTULO_EMAIL_HOST", "smtp.env.example")
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"

    assert site.email_shadowed() == ("email_host",)
    assert "data-shadowed" in client.get(reverse("server:email")).content.decode()

    # The stored value is still there, and is what takes over when the variable goes.
    assert SiteSettings.get().email_host == "smtp.stored.example"
    monkeypatch.delenv("POSTULO_EMAIL_HOST")
    assert site.email_settings()["host"] == "smtp.stored.example"


# ------------------------------------------------------------------------- the password


def test_the_password_is_encrypted_at_rest(client, admin):
    client.force_login(admin)

    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})

    stored = SiteSettings.get().email_password_encrypted
    assert stored and "hunter2-and-then-some" not in stored
    assert secrets.decrypt(stored) == {"password": "hunter2-and-then-some"}


def test_the_password_is_never_rendered_nor_its_length(client, admin):
    client.force_login(admin)
    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})

    html = client.get(reverse("server:email")).content.decode()

    assert "hunter2-and-then-some" not in html
    assert SiteSettings.get().email_password_encrypted not in html
    # It says one is set, which is all it may say.
    assert "data-password-state" in html


def test_leaving_the_password_blank_keeps_the_stored_one(client, admin):
    client.force_login(admin)
    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})

    client.post(reverse("server:email"), {**STORED, "email_host": "smtp.moved.example"})

    assert site.email_settings()["password"] == "hunter2-and-then-some"
    assert site.email_settings()["host"] == "smtp.moved.example"


def test_forgetting_the_password_is_a_deliberate_act(client, admin):
    client.force_login(admin)
    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})

    client.post(reverse("server:email"), {**STORED, "forget_email_password": "on"})

    assert SiteSettings.get().email_password_encrypted == ""
    assert not SiteSettings.get().has_email_password


def test_a_password_encrypted_under_a_lost_key_does_not_stop_mail(client, admin, settings):
    """A rotated SECRET_KEY without POSTULO_FIELD_KEY. Falling back beats refusing to send."""
    client.force_login(admin)
    client.post(reverse("server:email"), {**STORED, "email_password": "hunter2-and-then-some"})
    settings.POSTULO_FIELD_KEY = "a-completely-different-key"
    settings.POSTULO_EMAIL_HOST_PASSWORD = "from-the-environment"

    resolved = site.email_settings()

    assert resolved["password"] == "from-the-environment"
    assert resolved["host"] == "smtp.stored.example"


# ------------------------------------------------------- read at send time, not at import


def test_the_backend_takes_the_settings_in_force_when_it_is_built(admin, settings):
    """The reason the page can exist: MAILERS is frozen at import and this is not."""
    row = SiteSettings.get()
    row.email_host, row.email_port, row.email_username = "smtp.first.example", 2525, "one"
    row.email_use_tls, row.email_timeout = False, 7
    row.email_password = "first-password"
    row.save()

    backend = postulo_mail.SiteSMTPBackend(alias="default")
    assert (backend.host, backend.port, backend.username) == ("smtp.first.example", 2525, "one")
    assert backend.password == "first-password" and backend.timeout == 7

    row.email_host = "smtp.second.example"
    row.save()

    assert postulo_mail.SiteSMTPBackend(alias="default").host == "smtp.second.example"


def test_the_backend_ignores_options_that_would_freeze_it(admin):
    """Passing OPTIONS would put the stale values back; they are dropped rather than obeyed."""
    row = SiteSettings.get()
    row.email_host = "smtp.stored.example"
    row.save()

    backend = postulo_mail.SiteSMTPBackend(alias="default", host="smtp.frozen.example", port=99)

    assert backend.host == "smtp.stored.example"
    assert backend.port != 99


def test_the_from_address_is_stamped_on_anything_that_did_not_choose_one(
    admin, settings, monkeypatch
):
    """The backend is the only place every message passes through.

    `DEFAULT_FROM_EMAIL` is read at send time by Django's code and allauth's, and neither
    offers a hook, so a from-address set on the page has to be applied here or nowhere.
    """
    settings.DEFAULT_FROM_EMAIL = "boot@example.org"
    row = SiteSettings.get()
    row.email_host, row.email_from = "smtp.stored.example", "chosen@example.org"
    row.save()

    sent = []
    monkeypatch.setattr(
        "django.core.mail.backends.smtp.EmailBackend.send_messages",
        lambda self, messages: sent.extend(messages) or len(messages),
    )

    postulo_mail.SiteSMTPBackend(alias="default").send_messages(
        [
            django_mail.EmailMessage(subject="s", body="b", to=["someone@example.org"]),
            django_mail.EmailMessage(
                subject="s", body="b", from_email="a-plugin@example.org", to=["x@example.org"]
            ),
        ]
    )

    assert [message.from_email for message in sent] == [
        "chosen@example.org",
        "a-plugin@example.org",
    ], "one that chose a sender keeps it; one that did not gets the instance's"


# ------------------------------------------------------------------- the connection test


def test_the_connection_test_uses_what_is_on_screen_not_what_is_stored(client, admin, monkeypatch):
    """Otherwise a configuration has to be saved -- over the working one -- to be tried."""
    row = SiteSettings.get()
    row.email_host = "smtp.stored.example"
    row.save()
    seen = {}

    def record(**kwargs):
        seen.update(kwargs)
        return "connected"

    monkeypatch.setattr(postulo_mail, "check_connection", record)
    client.force_login(admin)

    client.post(
        reverse("server:email_connection_test"),
        {**STORED, "email_host": "smtp.typed.example", "email_password": "typed-password"},
    )

    assert seen["host"] == "smtp.typed.example"
    assert seen["password"] == "typed-password"
    assert seen["port"] == 2525 and seen["use_tls"] is True


def test_the_connection_test_falls_back_to_the_stored_password(client, admin, monkeypatch):
    """The password is never rendered, so a blank one means the stored one -- as on save."""
    row = SiteSettings.get()
    row.email_password = "hunter2-and-then-some"
    row.save()
    seen = {}
    monkeypatch.setattr(postulo_mail, "check_connection", lambda **kw: seen.update(kw) or "ok")
    client.force_login(admin)

    client.post(reverse("server:email_connection_test"), {**STORED, "email_password": ""})

    assert seen["password"] == "hunter2-and-then-some"


def test_a_failing_connection_is_reported_and_does_not_block_saving(client, admin, monkeypatch):
    def refuse(**kwargs):
        raise postulo_mail.ConnectionFailed("nobody home")

    monkeypatch.setattr(postulo_mail, "check_connection", refuse)
    client.force_login(admin)

    response = client.post(reverse("server:email_connection_test"), {**STORED}, follow=True)
    assert "nobody home" in response.content.decode()

    response = client.post(reverse("server:email"), STORED, follow=True)
    assert SiteSettings.get().email_host == "smtp.stored.example"


def test_the_connection_test_says_no_when_there_is_no_server(settings):
    settings.POSTULO_EMAIL_HOST = ""
    with pytest.raises(postulo_mail.ConnectionFailed):
        postulo_mail.check_connection(
            host="", port=25, username="", password="", use_tls=False, timeout=1
        )


# ------------------------------------------------------------------------------ access


@pytest.mark.parametrize("route", ["server:email", "server:email_connection_test"])
def test_an_ordinary_account_reaches_none_of_this(client, django_user_model, route):
    person = django_user_model.objects.create_user(
        email="person@example.org", username="person", password="a-long-enough-password-42"
    )
    client.force_login(person)

    response = client.post(reverse(route), STORED)

    assert response.status_code in {302, 403, 404}
    assert SiteSettings.get().email_host == ""
