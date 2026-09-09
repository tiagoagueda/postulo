"""Where the server is allowed to dial when the destination was typed (#148).

`plugins/http.py` guards a URL somebody typed with three properties, and none of them were on
the path a mail backend takes: Django opens a socket to a host and a port and asks nothing.
That was fine while the host was always the operator's own, and it stops being fine the moment
a person can type one — so the guard has to exist before the field does.

The three properties, each with tests here.

**Private and loopback addresses are refused** unless the operator has said otherwise, through
the switch that already exists rather than a second one.

**Every address a name answers with is checked**, not the first. A name resolving to one
public address and one private one would otherwise be a way straight through.

**The connection goes to the address that was checked.** This is the one people leave out and
the one that matters: a name with a one-second lifetime can answer publicly for the check and
privately a moment later.

And one property that is not about addresses at all: **the refusal never dials**, so a private
address gets the same answer whether something is listening on it or not. Otherwise the Test
button is a port scanner with a form around it.
"""

from __future__ import annotations

import ipaddress
import smtplib
from unittest import mock

import pytest
from django.test import override_settings

from postulo.core import destinations, mail

pytestmark = pytest.mark.django_db

PUBLIC = ipaddress.ip_address("93.184.216.34")
PRIVATE = ipaddress.ip_address("10.0.0.7")
LOOPBACK = ipaddress.ip_address("127.0.0.1")


def answering(*addresses):
    """A resolver that answers with exactly these."""
    return lambda host: list(addresses)


# ------------------------------------------------------------- refusing a private address


def test_a_public_address_is_approved(monkeypatch):
    monkeypatch.setattr(destinations, "addresses_for", answering(PUBLIC))

    assert destinations.approve("mail.example.org", allow_private=False) == PUBLIC


def test_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(destinations, "addresses_for", answering(PRIVATE))

    with pytest.raises(destinations.Refused) as raised:
        destinations.approve("relay.internal", allow_private=False)

    assert "private or local" in str(raised.value)
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in str(raised.value), "and names the way out"


def test_loopback_is_refused_too(monkeypatch):
    """The obvious one to forget, and the one that reaches everything on the machine."""
    monkeypatch.setattr(destinations, "addresses_for", answering(LOOPBACK))

    with pytest.raises(destinations.Refused):
        destinations.approve("localhost", allow_private=False)


def test_the_operator_can_allow_it(monkeypatch):
    """A mail server on the LAN is the same case as a Paperless on the LAN."""
    monkeypatch.setattr(destinations, "addresses_for", answering(PRIVATE))

    assert destinations.approve("relay.internal", allow_private=True) == PRIVATE


def test_the_switch_is_the_one_that_already_exists():
    """One decision, made once, by the person who owns the machine."""
    with override_settings(POSTULO_CONNECTIONS_ALLOW_PRIVATE=True):
        assert destinations.private_allowed()
    with override_settings(POSTULO_CONNECTIONS_ALLOW_PRIVATE=False):
        assert not destinations.private_allowed()


# ---------------------------------------------------------------- every address, not the first


def test_one_private_address_among_public_ones_refuses_the_lot(monkeypatch):
    """The way through, if only the first answer were checked."""
    monkeypatch.setattr(destinations, "addresses_for", answering(PUBLIC, PRIVATE))

    with pytest.raises(destinations.Refused):
        destinations.approve("both.example.org", allow_private=False)


def test_the_order_of_the_answers_does_not_decide(monkeypatch):
    monkeypatch.setattr(destinations, "addresses_for", answering(PRIVATE, PUBLIC))

    with pytest.raises(destinations.Refused):
        destinations.approve("both.example.org", allow_private=False)


def test_a_name_that_does_not_resolve_is_a_different_fact(monkeypatch):
    """Not refused — unresolvable. Different problem, different fix, different sentence."""
    monkeypatch.setattr(destinations, "addresses_for", answering())

    with pytest.raises(destinations.Unresolvable):
        destinations.approve("nowhere.example.org", allow_private=False)


def test_an_empty_host_says_so_rather_than_resolving_it():
    with pytest.raises(destinations.Unresolvable):
        destinations.addresses_for("")


# ----------------------------------------------------------------- nothing gets dialled


def test_a_refusal_never_opens_a_socket(monkeypatch):
    """Otherwise the Test button maps the internal network one refusal at a time."""
    monkeypatch.setattr(destinations, "addresses_for", answering(PRIVATE))
    opened = mock.Mock(side_effect=AssertionError("a socket was opened"))
    monkeypatch.setattr(destinations, "PinnedSMTP", opened)
    monkeypatch.setattr(destinations, "PinnedSMTP_SSL", opened)

    with pytest.raises(mail.ConnectionFailed):
        mail.check_connection(
            host="relay.internal",
            port=25,
            username="",
            password="",
            security="none",
            timeout=1,
        )

    assert not opened.called


def test_every_private_address_gets_the_same_answer(monkeypatch):
    """Listening or not, the reply is identical: there is nothing to learn from trying."""
    said = []
    for address in ("10.0.0.7", "10.0.0.8", "192.168.1.1", "172.16.4.4"):
        monkeypatch.setattr(destinations, "addresses_for", answering(ipaddress.ip_address(address)))
        with pytest.raises(destinations.Refused) as raised:
            destinations.approve("host.internal", allow_private=False)
        said.append(str(raised.value))

    assert len(set(said)) == 1, "a different sentence for one of them is a map of the network"


# ------------------------------------------------------- connecting to what was approved


def test_the_connection_goes_to_the_approved_address_not_the_name(monkeypatch):
    """The window this closes: the second lookup a naive implementation would do."""
    monkeypatch.setattr(destinations, "addresses_for", answering(PUBLIC))
    opened = mock.MagicMock()
    monkeypatch.setattr(destinations, "PinnedSMTP", opened)

    mail.check_connection(
        host="mail.example.org",
        port=25,
        username="",
        password="",
        security="none",
        timeout=5,
    )

    assert opened.call_args.kwargs["host"] == str(PUBLIC)
    assert opened.call_args.kwargs["certificate_name"] == "mail.example.org"


@pytest.fixture
def watched_socket(monkeypatch):
    """What `smtplib` was told to dial, and what name it would prove, without dialling.

    Patched at `smtplib.SMTP` rather than by subclassing, so the mixin under test still runs:
    a subclass overriding `_get_socket` replaces the very method being checked.
    """
    seen: dict = {}

    def never_connects(self, host, port, timeout):
        seen["dialled"] = host
        seen["proved"] = self._host
        raise OSError("far enough")

    monkeypatch.setattr(smtplib.SMTP, "_get_socket", never_connects)
    return seen


def test_the_certificate_is_proved_against_the_name_that_was_typed(watched_socket):
    """A certificate checked against a number never matches, so the name is carried over."""
    with pytest.raises(OSError):
        destinations.PinnedSMTP("93.184.216.34", 25, certificate_name="mail.example.org")

    assert watched_socket["dialled"] == "93.184.216.34", "the address that was approved"
    assert watched_socket["proved"] == "mail.example.org", "the name a person typed"


def test_without_a_name_nothing_is_rewritten(watched_socket):
    """Given a bare address and nothing else, it behaves exactly as `smtplib` does."""
    with pytest.raises(OSError):
        destinations.PinnedSMTP("93.184.216.34", 25)

    assert watched_socket["proved"] == "93.184.216.34"


# ---------------------------------------------------------------- whose host it is


def test_a_host_pinned_by_the_environment_is_not_second_guessed(monkeypatch):
    """A line in a file only the operator can edit, and the default it carries is localhost.

    Checking the operator's own file would refuse the default configuration of every
    instance that has never opened the Email page.
    """
    monkeypatch.setenv("POSTULO_EMAIL_HOST", "localhost")

    assert mail.host_policy() is True


def test_a_host_stored_from_the_page_is_checked(monkeypatch):
    monkeypatch.delenv("POSTULO_EMAIL_HOST", raising=False)

    with override_settings(POSTULO_CONNECTIONS_ALLOW_PRIVATE=False):
        assert mail.host_policy() is False


def test_the_operators_switch_still_wins_over_the_page(monkeypatch):
    monkeypatch.delenv("POSTULO_EMAIL_HOST", raising=False)

    with override_settings(POSTULO_CONNECTIONS_ALLOW_PRIVATE=True):
        assert mail.host_policy() is True


# ------------------------------------------------------------------ the send path


def test_sending_dials_the_approved_address(monkeypatch):
    from postulo.notifications.smtp import GuardedBackend

    monkeypatch.setattr(destinations, "addresses_for", answering(PUBLIC))
    backend = GuardedBackend(alias="default", host="mail.example.org", port=25)
    seen = {}

    def fake_open(self):
        seen["host"] = self.host
        seen["pinned"] = self.connection_class
        return True

    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", fake_open)
    backend.open()

    assert seen["host"] == str(PUBLIC), "the address, not the name"
    assert seen["pinned"].keywords["certificate_name"] == "mail.example.org"


def test_the_backend_reports_the_name_afterwards(monkeypatch):
    """A log line or a page summary should say the server somebody configured."""
    from postulo.notifications.smtp import GuardedBackend

    monkeypatch.setattr(destinations, "addresses_for", answering(PUBLIC))
    monkeypatch.setattr("django.core.mail.backends.smtp.EmailBackend.open", lambda self: True)
    backend = GuardedBackend(alias="default", host="mail.example.org", port=25)

    backend.open()

    assert backend.host == "mail.example.org"


def test_sending_to_a_private_address_is_refused(monkeypatch):
    from postulo.notifications.smtp import GuardedBackend

    monkeypatch.setattr(destinations, "addresses_for", answering(PRIVATE))
    monkeypatch.delenv("POSTULO_EMAIL_HOST", raising=False)
    backend = GuardedBackend(alias="default", host="relay.internal", port=25)

    with (
        override_settings(POSTULO_CONNECTIONS_ALLOW_PRIVATE=False),
        pytest.raises(destinations.Refused),
    ):
        backend.open()


def test_an_already_open_connection_is_not_re_approved(monkeypatch):
    """`open()` returning early is Django's contract, and the guard must not break it."""
    from postulo.notifications.smtp import GuardedBackend

    called = mock.Mock(side_effect=AssertionError("resolved an open connection"))
    monkeypatch.setattr(destinations, "addresses_for", called)
    backend = GuardedBackend(alias="default", host="mail.example.org", port=25)
    backend.connection = object()

    assert backend.open() is False
    assert not called.called
