"""Mail delivery as a plugin, and the lock that stops it being switched off (#104).

Four things here are the point, and each has a test.

**A plugin cannot supply `MAILERS`** — Django reads that at settings import, entry points
load later — so core names one backend and that backend asks which transport is selected at
send time. That is what makes delivery pluggable at all.

**Installing a transport must not silently redirect the mail.** The registry prefers
third-party plugins for sources, because a plugin written for one job board knows more about
it than a general parser does. That argument does not transfer to where an instance's mail
goes.

**The lock is a rule, evaluated.** Not `if plugin == "smtp": refuse`. A hardcoded exception
is one nobody deletes, so the day another recovery route lands the lock would stay shut out
of inertia — and an instance that later runs a second transport should be able to switch
this one off.

**A transport is nobody's to decide.** The per-person policy has four states and none of
them means anything about mail delivery; *forced off* would mean an account nobody can
recover.
"""

from __future__ import annotations

import contextlib

import pytest
from django.core import mail as django_mail
from django.test import override_settings
from django.urls import reverse

from postulo.core.models import SiteSettings
from postulo.notifications import transport
from postulo.notifications.smtp import SMTPTransport
from postulo.notifications.transport import PluggableBackend
from postulo.plugins import policy, registry
from postulo.plugins.base import FieldSpec

# Aliased: pytest would try to collect anything called Test* as a test class.
from postulo.plugins.base import TestResult as PluginTestResult

pytestmark = pytest.mark.django_db


class Carrier:
    """A transport that speaks an HTTP API — the case the kind exists for."""

    name = "carrier"
    version = "1.2.3"
    kind = "transport"
    label = "Carrier"
    description = "Delivers over somebody's HTTP API."

    def __init__(self):
        self.sent: list = []

    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec("endpoint", "Endpoint", type="url"),
            FieldSpec("token", "API token", type="password", secret=True),
        ]

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def deliver(self, messages: list, config: dict) -> int:
        self.sent.append((list(messages), dict(config)))
        return len(messages)


@contextlib.contextmanager
def also(plugin_class):
    """Register a second transport for the duration, then put things back."""
    registry.register_builtin("transport", plugin_class)
    try:
        yield
    finally:
        registry.unregister_builtin("transport", plugin_class)


@contextlib.contextmanager
def instead_of_smtp(plugin_class):
    registry.unregister_builtin("transport", SMTPTransport)
    registry.register_builtin("transport", plugin_class)
    try:
        yield
    finally:
        registry.unregister_builtin("transport", plugin_class)
        registry.register_builtin("transport", SMTPTransport)


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


def give_a_passkey(person):
    from allauth.mfa.models import Authenticator

    return Authenticator.objects.create(
        user=person, type=Authenticator.Type.WEBAUTHN, data={"credential": {}}
    )


# ----------------------------------------------------------------- the kind exists


def test_smtp_ships_as_a_transport_and_says_who_it_is(db):
    """Through its manifest, which is where a plugin's facts live now (#98)."""
    from postulo.plugins.base import manifest_of

    smtp = registry.find_plugin("transport", "smtp")

    assert smtp is not None
    assert smtp.kind == "transport"
    manifest = manifest_of(smtp)
    assert manifest.label and manifest.version and manifest.description
    assert manifest.author and manifest.licence and manifest.source_url


def test_a_transport_is_recognised_by_the_registry(db):
    with also(Carrier):
        names = {item.name for item in registry.plugins("transport", refresh=True)}
    assert {"smtp", "carrier"} <= names


# --------------------------------------------------------------- choosing one


def test_installing_a_transport_does_not_silently_redirect_the_mail(db):
    with also(Carrier):
        registry.plugins("transport", refresh=True)
        assert transport.selected().name == "smtp"


def test_the_administrator_chooses_and_that_is_what_carries_it(db):
    with also(Carrier):
        registry.plugins("transport", refresh=True)
        row = SiteSettings.get()
        row.email_transport = "carrier"
        row.save()

        assert transport.selected().name == "carrier"


def test_a_choice_that_is_no_longer_installed_falls_back_rather_than_failing(db):
    row = SiteSettings.get()
    row.email_transport = "carrier-that-was-removed"
    row.save()

    assert transport.selected().name == "smtp"


# ------------------------------------------------------- resolved at send time


def test_the_backend_asks_which_transport_at_send_time(db, settings):
    settings.DEFAULT_FROM_EMAIL = "boot@example.org"
    carrier = Carrier()
    with instead_of_smtp(lambda: carrier):
        registry.plugins("transport", refresh=True)
        sent = PluggableBackend(alias="default").send_messages(
            [django_mail.EmailMessage(subject="s", body="b", to=["someone@example.org"])]
        )

    assert sent == 1
    assert len(carrier.sent) == 1


def test_a_transports_own_settings_reach_it(db):
    carrier = Carrier()
    row = SiteSettings.get()
    row.email_transport = "carrier"
    row.transport_config = {"endpoint": "https://carrier.example/send"}
    row.transport_secrets = {"token": "a-secret-token"}
    row.save()

    with instead_of_smtp(lambda: carrier):
        registry.plugins("transport", refresh=True)
        PluggableBackend(alias="default").send_messages(
            [django_mail.EmailMessage(subject="s", body="b", to=["someone@example.org"])]
        )

    _messages, config = carrier.sent[0]
    assert config["endpoint"] == "https://carrier.example/send"
    assert config["token"] == "a-secret-token"
    assert "a-secret-token" not in SiteSettings.get().transport_secrets_encrypted


def test_smtp_keeps_its_own_columns_and_the_environment_still_wins(db, monkeypatch, settings):
    """The one exception, and why: a fresh instance must be able to send before there is a row."""
    row = SiteSettings.get()
    row.email_host = "smtp.stored.example"
    row.save()
    smtp = registry.find_plugin("transport", "smtp")

    assert transport.configuration(smtp)["host"] == "smtp.stored.example"

    monkeypatch.setenv("POSTULO_EMAIL_HOST", "smtp.env.example")
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"

    assert transport.configuration(smtp)["host"] == "smtp.env.example"


def test_a_fresh_instance_with_no_rows_can_still_send(db, settings):
    """No SiteSettings row at all: the state before anybody has an account to verify."""
    SiteSettings.objects.all().delete()
    settings.POSTULO_EMAIL_HOST = "smtp.env.example"
    settings.POSTULO_EMAIL_PORT = 587

    config = transport.configuration(registry.find_plugin("transport", "smtp"))

    assert config["host"] == "smtp.env.example" and config["port"] == 587


# ------------------------------------------------------------------- the lock


def test_the_lock_refuses_while_email_is_the_only_way_back_in(db, admin):
    refusal = transport.refuse_switching_off("smtp")

    assert refusal
    assert "SMTP" in refusal, "it names what it is protecting, not just 'not allowed'"
    assert transport.recovery_routes(without="smtp") == []


def test_the_lock_opens_by_itself_once_there_is_another_way_in(db, admin):
    """A rule, not a name check: nothing here mentions SMTP, and nothing had to be deleted."""
    assert transport.refuse_switching_off("smtp")

    give_a_passkey(admin)

    assert transport.recovery_routes(without="smtp") == ["passkey"]
    assert transport.refuse_switching_off("smtp") == ""


def test_one_account_without_a_passkey_is_enough_to_keep_it_shut(db, admin, django_user_model):
    give_a_passkey(admin)
    django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )

    assert transport.refuse_switching_off("smtp")


def test_a_transport_that_is_not_the_selected_one_is_not_locked(db, admin):
    with also(Carrier):
        registry.plugins("transport", refresh=True)
        assert transport.refuse_switching_off("carrier") == ""


def test_switching_off_the_package_that_carries_the_mail_is_refused(client, admin, monkeypatch):
    """The plugins page deals in distributions, so the lock has to answer for one."""
    monkeypatch.setattr(transport, "_distribution_of", lambda plugin: "postulo-smtp")
    client.force_login(admin)

    response = client.post(
        reverse("server:plugin_action"), {"action": "disable", "name": "postulo-smtp"}, follow=True
    )

    body = response.content.decode()
    assert "only way back into an account" in body


def test_removing_the_package_that_carries_the_mail_is_refused(client, admin, monkeypatch):
    monkeypatch.setattr(transport, "_distribution_of", lambda plugin: "postulo-smtp")
    client.force_login(admin)

    response = client.post(
        reverse("server:plugin_action"), {"action": "remove", "name": "postulo-smtp"}, follow=True
    )

    assert "only way back into an account" in response.content.decode()


def test_an_unrelated_package_is_not_caught_by_the_lock(client, admin, monkeypatch):
    monkeypatch.setattr(transport, "_distribution_of", lambda plugin: "postulo-smtp")

    assert transport.refuse_removing_distribution("postulo-something-else") == ""


# ------------------------------------------- a transport is nobody's to decide (#95)


def test_a_transport_is_not_offered_on_the_per_person_page(db, admin):
    rows = policy.overview(admin)

    assert not [row for row in rows if row["kind"] == "transport"]
    assert "transport" not in policy.GOVERNED_KINDS


def test_a_person_cannot_switch_a_transport_off_for_themselves(db, admin):
    """Not merely absent from the page: refused, because the page is not the boundary."""
    changed = policy.set_choice(admin, "smtp", on=False)

    assert changed is False
    assert "smtp" not in (admin.profile.plugins_off or [])
    assert policy.decide("smtp", admin).theirs is False


def test_an_administrator_cannot_force_a_transport_off_for_somebody(
    client, admin, django_user_model
):
    """*Forced off* for a transport would be an account nobody can recover."""
    from postulo.plugins.models import PluginPolicy

    person = django_user_model.objects.create_user(
        email="person@example.org", username="person", password="a-long-enough-password-42"
    )
    client.force_login(admin)

    client.post(reverse("server:person_plugins", args=[person.pk]), {"state:smtp": "off"})

    assert not PluginPolicy.objects.filter(plugin="smtp").exists()
    assert policy.decide("smtp", person).on is True


@override_settings(
    MAILERS={"default": {"BACKEND": "postulo.notifications.transport.PluggableBackend"}}
)
def test_the_email_page_says_the_transport_is_locked(client, admin):
    """Under the pluggable backend, which is what production runs.

    The tests otherwise use locmem, where no transport carries anything — and a page saying
    "SMTP is how this instance sends mail" beside a line reading `locmem.EmailBackend` would
    be two sentences contradicting each other.
    """
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "data-locked" in html
    assert 'data-transport="smtp"' in html
    assert "would have no way in" in html


def test_the_page_claims_no_transport_when_none_is_carrying_anything(client, admin):
    """The development case: the console backend is named directly and nothing is pluggable."""
    client.force_login(admin)

    html = client.get(reverse("server:email")).content.decode()

    assert "data-transport" not in html
    assert "data-locked" not in html
