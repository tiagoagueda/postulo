"""Mail has two halves, and only one of them is a way back in (#149).

> lets smtp plugin be internal split in 2 part, 1 part, server site that cannot be disable
> … and a second part, user side, that can be enable/disable by the user / admin with smtp
> settings by user

The instance's half was already there. This is the other one, and the reason it is a **new
kind** rather than a second transport or a notifier with extra fields is the first section
below: `recovery_routes()` reads transports, so anything that is not a transport cannot
become a way into somebody's account by accident. That is what makes "the person may switch
this off" safe to offer — the failure #104 exists to prevent, arriving through a door nobody
locked.
"""

from __future__ import annotations

import pytest
from django.core.mail import EmailMessage

from postulo.core import correspondence
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def mine(user):
    return Connection.objects.create(
        owner=user,
        kind="outbox",
        plugin="own-mail",
        label="Mine",
        enabled=True,
        config={"from_address": "alex@example.org", "host": "mail.example.org"},
    )


# ------------------------------------------------- the invariant that must not bend


def test_an_outbox_is_never_a_way_back_into_an_account():
    """The whole reason this is a kind of its own.

    If a person's own SMTP could carry a password reset, switching their own plugin off
    would remove their own way back in. `recovery_routes` reads transports and an outbox is
    not one, so the door is not merely shut — it was never cut into the wall.
    """
    from postulo.accounts import recovery
    from postulo.plugins.base import CONNECTED_KINDS
    from postulo.plugins.policy import UNGOVERNED_KINDS

    assert "outbox" in CONNECTED_KINDS, "governed, so a person may switch it off"
    assert "outbox" not in UNGOVERNED_KINDS, "which a transport can never be"

    from pathlib import Path

    text = Path(recovery.__file__).read_text(encoding="utf-8")
    assert "outbox" not in text, "recovery must not learn the word"


def test_the_two_halves_are_different_kinds():
    """One plugin in two parts would be one plugin that is at once governed and ungoverned,
    which `policy.decide` has no way to express. Two kinds is the honest shape.
    """
    from postulo.plugins.registry import find_any

    assert find_any("smtp").kind == "transport", "the instance's"
    assert find_any("own-mail").kind == "outbox", "and the person's"


def test_switching_the_person_s_half_off_leaves_the_instance_s_alone(user, mine, monkeypatch):
    """What an administrator switching it off means: *you cannot send from your own address
    here*. Not "you cannot be notified" — the instance still notifies, still recovers, still
    sends everything of its own.
    """
    from postulo.plugins import policy

    monkeypatch.setattr(policy, "plugins_for", lambda person, kind: [])

    assert correspondence.outbox_for(user) is None
    assert correspondence.can_send_as_themselves(user) is False

    from postulo.plugins.registry import find_any

    assert find_any("smtp") is not None, "the instance's mail is untouched"


# --------------------------------------------------------------- the From address


def test_a_message_with_no_sender_gets_the_outbox_s_own(user, mine, monkeypatch):
    sent = []
    monkeypatch.setattr(
        correspondence,
        "outbox_for",
        lambda person: type(
            "P", (), {"name": "own-mail", "send": lambda s, m, c: sent.append(m) or 1}
        )(),
    )
    message = EmailMessage(subject="Hello", body="Hi", to=["them@example.net"])

    correspondence.send(user, message)

    assert sent[0].from_email == "alex@example.org"


def test_a_message_claiming_somebody_else_is_refused_rather_than_rewritten(user, mine):
    """Quietly rewriting a sender is what makes mail look forged, and looking forged is what
    gets a domain listed. A mismatch here is a bug, not a preference to accommodate.
    """
    message = EmailMessage(
        subject="Hello", body="Hi", from_email="someone@else.example", to=["them@example.net"]
    )

    with pytest.raises(correspondence.WrongSender):
        correspondence.send(user, message)


def test_somebody_with_no_outbox_is_told_so(user):
    message = EmailMessage(subject="Hello", body="Hi", to=["them@example.net"])

    with pytest.raises(correspondence.NoOutbox):
        correspondence.send(user, message)


# ------------------------------------------------------------------ what it sends


def test_sending_is_bounded_per_account(user, mine, monkeypatch, settings):
    """Per account, like every other limit. This is the one surface where a mistake reaches
    strangers rather than the person who made it.
    """
    from postulo.core import throttle

    settings.POSTULO_OUTBOX_RATE = "1/h"
    monkeypatch.setattr(
        correspondence,
        "outbox_for",
        lambda person: type("P", (), {"name": "own-mail", "send": lambda s, m, c: 1})(),
    )

    correspondence.send(user, EmailMessage(subject="a", body="b", to=["x@example.net"]))
    with pytest.raises(throttle.TooOften):
        correspondence.send(user, EmailMessage(subject="a", body="b", to=["x@example.net"]))


def test_the_log_records_that_something_went_and_not_to_whom(user, mine, monkeypatch, caplog):
    """Who somebody wrote to is theirs. What is useful in a log is that mail left."""
    monkeypatch.setattr(
        correspondence,
        "outbox_for",
        lambda person: type("P", (), {"name": "own-mail", "send": lambda s, m, c: 1})(),
    )

    with caplog.at_level("INFO"):
        correspondence.send(
            user, EmailMessage(subject="Secret", body="Body", to=["them@example.net"])
        )

    written = caplog.text
    assert "own-mail" in written
    assert "them@example.net" not in written
    assert "Secret" not in written and "Body" not in written


# ------------------------------------------------------------------- the plugin


def test_it_asks_for_an_address_rather_than_guessing_one():
    """Plenty of people sign in with one address and correspond from another, and guessing
    would be a message going out with the wrong name on it.
    """
    from postulo.plugins.own_mail import OwnMail

    fields = {field.name: field for field in OwnMail().config_fields()}

    assert "from_address" in fields
    assert fields["from_address"].required is True
    assert fields["password"].secret is True, "stored encrypted and never shown back"


def test_a_blank_port_follows_the_encryption_that_was_chosen():
    """Somebody who picked TLS and left the port blank meant 465."""
    from postulo.plugins.own_mail import _port

    assert _port({"security": "ssl"}) == 465
    assert _port({"security": "starttls"}) == 587
    assert _port({"security": "ssl", "port": 2465}) == 2465, "never a correction"


def test_it_dials_only_where_the_instance_is_allowed_to():
    """A person's outbox is somewhere the server dials at somebody's typing, which is
    exactly the shape #148 exists for — and the same guard as the instance's own mail,
    rather than a second one that can drift.
    """
    import inspect

    from postulo.core.mail import GuardedBackend
    from postulo.plugins.own_mail import OwnMail

    assert "GuardedBackend" in inspect.getsource(OwnMail.send)
    assert "approve" in inspect.getsource(GuardedBackend.open)
