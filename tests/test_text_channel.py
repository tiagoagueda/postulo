"""A channel the instance can reach a locked-out person on (#143).

Deciding what this *is* was most of the issue, and the decision is the thing under test.

**A recovery channel cannot be a notifier.** A notifier's credentials belong to the person —
their Twilio account, their Apprise endpoint — and somebody locked out of their account is
exactly the one whose own gateway may be unreachable. Worse, "the account holder configured
the channel that proves they are the account holder" is circular. So it has to be operated by
the instance, which is what a transport already is.

**One kind, two mediums, rather than a second kind.** What differs between carrying an email
and carrying a text message is the payload; selection, configuration, the interlock that stops
the last way in being switched off, the exemption from per-person policy and the page that
shows them are all identical. A field describes that difference; a kind would have duplicated
everything around it.

**Postulo ships no gateway**, and that is the answer to "every gateway is somebody else's
jurisdiction" rather than an unfinished edge. So every test that needs one brings its own.
"""

from __future__ import annotations

import contextlib
from typing import ClassVar

import pytest
from django.test import override_settings

from postulo.core import channels, phone_numbers
from postulo.core.models import PhoneNumber, SiteSettings
from postulo.notifications import text, transport
from postulo.plugins import base, registry
from postulo.plugins.base import FieldSpec
from postulo.plugins.base import TestResult as PluginTestResult

pytestmark = pytest.mark.django_db


class Gateway:
    """Somebody else's package, in miniature."""

    name = "gateway"
    version = "1.0.0"
    kind = "transport"
    label = "Gateway"
    description = "Sends text messages through somebody else's service."
    medium = base.TEXT
    #: Class-level on purpose: the registry builds its own instance, so a test that wants
    #: to see what was sent has to look somewhere the instance cannot hide it.
    sent: ClassVar[list] = []
    raises: Exception | None = None
    delivers: int | None = None

    def config_fields(self) -> list[FieldSpec]:
        return [FieldSpec("token", "API token", type="password", secret=True)]

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def deliver(self, messages: list, config: dict) -> int:
        if self.raises is not None:
            raise self.raises
        type(self).sent = [*type(self).sent, *[(m.to, m.body, dict(config)) for m in messages]]
        return len(messages) if self.delivers is None else self.delivers


@contextlib.contextmanager
def installed(plugin_class=Gateway):
    plugin_class.sent = []
    registry.register_builtin("transport", plugin_class)
    try:
        yield plugin_class
    finally:
        registry.unregister_builtin("transport", plugin_class)


def gateway_that(*, raises=None, delivers=None):
    return type("Gateway", (Gateway,), {"raises": raises, "delivers": delivers, "sent": []})


# --------------------------------------------------------------- what a transport is


def test_a_transport_says_what_it_carries():
    assert base.medium_of(Gateway()) == base.TEXT


def test_one_written_before_the_question_carries_mail():
    """Nothing already written should have to be edited to go on meaning what it meant."""

    class Older:
        name = "older"

    assert base.medium_of(Older()) == base.MAIL


def test_nonsense_falls_back_to_mail_rather_than_disappearing():
    class Confused:
        medium = "carrier pigeon"

    assert base.medium_of(Confused()) == base.MAIL


def test_the_shipped_mail_transport_says_mail():
    from postulo.notifications.smtp import SMTPTransport

    assert base.medium_of(SMTPTransport()) == base.MAIL


# ------------------------------------------------------------------- selection


def test_nothing_ships_that_can_reach_a_telephone():
    """The answer to 'every gateway is somebody else's jurisdiction'."""
    assert transport.available(base.TEXT) == []
    assert transport.selected(base.TEXT) is None
    assert not text.can_reach_a_number()


def test_the_mail_transport_is_not_offered_as_a_text_one():
    assert [item.name for item in transport.available(base.MAIL)] == ["smtp"]


def test_installing_one_makes_it_the_text_transport():
    with installed():
        registry.plugins("transport", refresh=True)

        assert transport.selected(base.TEXT).name == "gateway"
        assert text.can_reach_a_number()


def test_installing_one_does_not_disturb_the_mail_transport():
    with installed():
        registry.plugins("transport", refresh=True)

        assert transport.selected(base.MAIL).name == "smtp"


def test_each_medium_has_its_own_configuration():
    """Two are selected at once, so one shared blob could hold only one of them."""
    row = SiteSettings.get()
    row.transport_config = {"endpoint": "https://mail.example/send"}
    row.text_config = {"sender": "Postulo"}
    row.text_secrets = {"token": "a-secret-token"}
    row.save()

    with installed():
        registry.plugins("transport", refresh=True)
        config = transport.configuration(transport.selected(base.TEXT))

    assert config == {"sender": "Postulo", "token": "a-secret-token"}
    assert "a-secret-token" not in SiteSettings.get().text_secrets_encrypted


# ---------------------------------------------------------------------- sending


def test_sending_without_a_gateway_says_so_rather_than_failing_quietly():
    with pytest.raises(text.NoGateway):
        text.send("+351912345678", "824193 is your code")


def test_a_message_reaches_the_gateway_as_a_number_and_a_line(user):
    with installed() as gateway:
        registry.plugins("transport", refresh=True)

        assert text.send("+351912345678", "824193 is your code", on_behalf_of=user)

    to, body, _config = gateway.sent[0]
    assert to == "+351912345678"
    assert body == "824193 is your code"


def test_a_gateway_that_raises_is_a_failure_and_not_an_exception(user):
    with installed(gateway_that(raises=OSError("the API said no"))):
        registry.plugins("transport", refresh=True)

        assert text.send("+351912345678", "hello", on_behalf_of=user) is False


def test_a_gateway_that_sends_nothing_is_a_failure_too(user):
    with installed(gateway_that(delivers=0)):
        registry.plugins("transport", refresh=True)

        assert text.send("+351912345678", "hello", on_behalf_of=user) is False


def test_a_failure_does_not_log_the_number_or_the_message(user, caplog):
    """That line lands in an operator's log, and the body is a code into an account."""
    with installed(gateway_that(raises=OSError("the API said no"))):
        registry.plugins("transport", refresh=True)
        text.send("+351912345678", "824193 is your code", on_behalf_of=user)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "+351912345678" not in logged
    assert "824193" not in logged


# ----------------------------------------------------------------- the two limits


@override_settings(POSTULO_TEXT_RATE="2/h", POSTULO_TEXT_PER_NUMBER_RATE="100/h")
def test_one_account_cannot_drive_the_resend_button(user):
    with installed():
        registry.plugins("transport", refresh=True)
        text.send("+351912345670", "one", on_behalf_of=user)
        text.send("+351912345671", "two", on_behalf_of=user)

        with pytest.raises(text.TooMany):
            text.send("+351912345672", "three", on_behalf_of=user)


@override_settings(POSTULO_TEXT_RATE="100/h", POSTULO_TEXT_PER_NUMBER_RATE="2/h")
def test_one_number_cannot_be_made_to_buzz_all_afternoon(user, django_user_model):
    """The limit that matters: a stranger whose number was mistyped cannot switch it off."""
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )
    with installed():
        registry.plugins("transport", refresh=True)
        text.send("+351912345678", "one", on_behalf_of=user)
        text.send("+351912345678", "two", on_behalf_of=other)

        with pytest.raises(text.TooMany):
            text.send("+351912345678", "three", on_behalf_of=user)


@override_settings(POSTULO_TEXT_RATE="1/h", POSTULO_TEXT_PER_NUMBER_RATE="1/h")
def test_a_send_with_nobody_signed_in_is_still_bounded_by_the_number():
    """A recovery flow has no account yet, and the per-number limit is the one that holds."""
    with installed():
        registry.plugins("transport", refresh=True)
        text.send("+351912345678", "one")

        with pytest.raises(text.TooMany):
            text.send("+351912345678", "two")


@override_settings(POSTULO_TEXT_RATE="", POSTULO_TEXT_PER_NUMBER_RATE="")
def test_an_operator_can_switch_both_off(user):
    with installed():
        registry.plugins("transport", refresh=True)
        for index in range(20):
            assert text.send("+351912345678", str(index), on_behalf_of=user)


# ----------------------------------------------- what it changes for a number


def test_a_number_becomes_confirmable_the_moment_a_gateway_exists():
    """Nothing in the contract changes; the answer does (#146)."""
    assert not channels.confirmable(channels.TelephoneChannel())

    with installed():
        registry.plugins("transport", refresh=True)

        assert channels.confirmable(channels.TelephoneChannel())
        assert channels.TelephoneChannel().carrier() == "gateway"


# -------------------------------------------------------------- the recovery route


def test_a_text_message_is_not_a_route_without_a_gateway(user):
    assert "text" not in transport.recovery_routes()


def test_a_gateway_alone_is_not_a_route_either(user):
    """A route has to reach everybody, exactly as the passkey route does."""
    with installed():
        registry.plugins("transport", refresh=True)

        assert "text" not in transport.recovery_routes()


def test_it_becomes_a_route_when_every_account_has_a_confirmed_number(user):
    row = PhoneNumber.objects.create(owner=user, holder=user.profile, number="+351912345678")
    row.record_verified()

    with installed():
        registry.plugins("transport", refresh=True)

        assert "text" in transport.recovery_routes()


def test_one_account_without_one_is_enough_to_keep_it_out(user, django_user_model):
    row = PhoneNumber.objects.create(owner=user, holder=user.profile, number="+351912345678")
    row.record_verified()
    django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )

    with installed():
        registry.plugins("transport", refresh=True)

        assert "text" not in transport.recovery_routes()


def test_an_unverified_number_does_not_count(user):
    PhoneNumber.objects.create(owner=user, holder=user.profile, number="+351912345678")

    with installed():
        registry.plugins("transport", refresh=True)

        assert "text" not in transport.recovery_routes()


def test_a_number_on_a_contact_does_not_count(user):
    """A recruiter's switchboard is not a way into this account (#142)."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Somewhere")
    contact = Contact.objects.create(owner=user, company=company, name="A recruiter")
    theirs = PhoneNumber.objects.create(owner=user, holder=contact, number="+351912345679")
    theirs.record_verified()

    with installed():
        registry.plugins("transport", refresh=True)

        assert "text" not in transport.recovery_routes()
        assert phone_numbers.accounts_without_a_verified_number() == 1
