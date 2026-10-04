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


def test_it_refuses_a_private_host_even_when_the_operator_pinned_theirs(monkeypatch, settings):
    """`POSTULO_EMAIL_HOST` exempts the operator's own relay, not whatever a person types
    while it is set (#358): the test and the send are both refused before a socket opens.
    """
    from unittest import mock

    from postulo.core import destinations
    from postulo.plugins.own_mail import OwnMail

    monkeypatch.setenv("POSTULO_EMAIL_HOST", "smtp.example.org")
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    opened = mock.Mock(side_effect=AssertionError("a socket was opened"))
    monkeypatch.setattr(destinations, "PinnedSMTP", opened)
    monkeypatch.setattr(destinations, "PinnedSMTP_SSL", opened)
    config = {"host": "127.0.0.1", "port": 1, "security": "none", "from_address": "a@example.org"}

    result = OwnMail().test(config)
    with pytest.raises(destinations.Refused):
        OwnMail().send(EmailMessage("Hi", "Body", "a@example.org", ["b@example.org"]), config)

    assert result.ok is False
    assert not opened.called


# ----------------------------------------- the action that sends (#361)
#
# The kind was offered, its secret kept, and nothing ever sent through it. The one place
# that does is the page that freezes what was sent with an application.


class _Server:
    """An outbox plugin that keeps what it was asked to send, or refuses to."""

    name = "own-mail"

    def __init__(self, refuses=None):
        self.sent = []
        self.refuses = refuses

    def send(self, message, config):
        if self.refuses:
            raise self.refuses
        self.sent.append(message)
        return 1


@pytest.fixture
def server(monkeypatch, mine):
    plugin = _Server()
    monkeypatch.setattr(correspondence, "outbox_for", lambda person: plugin)
    return plugin


class _Backend:
    name = "fake"

    def __init__(self, bytes_=b"%PDF-1.7 mailed"):
        self.drawn = []
        self.bytes_ = bytes_

    def is_available(self):
        return True

    def render(self, html):
        self.drawn.append(html)
        return self.bytes_


@pytest.fixture
def pdf(monkeypatch):
    backend = _Backend()
    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: backend)
    return backend


@pytest.fixture
def filed(user):
    from postulo.applications.models import Application, Status
    from postulo.documents.models import CV, CoverLetter
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    cv = CV.objects.create(owner=user, name="Backend EN", headline="Backend engineer")
    letter = CoverLetter.objects.create(
        owner=user, name="General", subject="About {{ role }}", body="Dear {{ company }}, hello."
    )
    return application, cv, letter


def _post(client, application, **fields):
    from django.urls import reverse

    return client.post(reverse("documents:send", args=[application.pk]), fields)


def _frozen(user):
    from postulo.documents.models import RenderedDocument

    return RenderedDocument.objects.filter(owner=user).count()


def _email(**over):
    return {
        "send_email": "on",
        "recipient": "hr@example.net",
        "subject": "Hi",
        "body": "Hello",
        **over,
    }


def test_the_page_offers_to_email_only_to_somebody_with_an_outbox(client, user, filed):
    from django.urls import reverse

    client.force_login(user)

    page = client.get(reverse("documents:send", args=[filed[0].pk])).content.decode()

    assert 'name="send_email"' not in page
    assert "Postulo sends nothing itself" in page


def test_the_page_shows_the_block_prefilled_when_there_is_an_outbox(client, user, filed, server):
    from django.urls import reverse

    from postulo.jobs.models import Contact

    application = filed[0]
    contact = Contact.objects.create(owner=user, name="Chell", email="chell@example.net")
    application.contact = contact
    application.save(update_fields=["contact"])
    client.force_login(user)

    page = client.get(reverse("documents:send", args=[application.pk])).content.decode()

    assert 'name="send_email"' in page
    assert 'value="chell@example.net"' in page
    assert "Postulo sends nothing itself" not in page


def test_it_mails_the_documents_and_freezes_the_very_bytes_it_mailed(
    client, user, filed, server, pdf
):
    from postulo.documents.models import RenderedDocument

    application, cv, letter = filed
    client.force_login(user)

    response = _post(client, application, cv=cv.pk, cover_letter=letter.pk, **_email(body=""))

    assert response.status_code == 302
    (message,) = server.sent
    assert message.from_email == "alex@example.org", "the outbox's own address, never rewritten"
    assert message.to == ["hr@example.net"]
    assert "Dear Black Mesa" in message.body, "empty means the chosen letter's text"
    assert [a[2] for a in message.attachments] == ["application/pdf"] * 2
    assert {a[1] for a in message.attachments} == {b"%PDF-1.7 mailed"}
    assert _frozen(user) == 2
    kept = RenderedDocument.objects.filter(owner=user).first()
    with kept.file.open("rb") as stored:
        assert stored.read() == b"%PDF-1.7 mailed"
    event = application.events.get(summary="Documents emailed")
    assert "hr@example.net" in event.body


def test_each_document_is_drawn_once(client, user, filed, server, pdf):
    application, cv, letter = filed
    client.force_login(user)

    _post(client, application, cv=cv.pk, cover_letter=letter.pk, **_email())

    assert len(pdf.drawn) == 2, "one for the CV and one for the letter, mailed and kept"


def test_a_refused_send_freezes_and_records_nothing(client, user, filed, server, pdf):
    application, cv, _letter = filed
    server.refuses = OSError("connection reset by hr@example.net")
    client.force_login(user)

    response = _post(client, application, cv=cv.pk, **_email())

    page = response.content.decode()
    assert response.status_code == 200
    assert "nothing was sent or recorded" in page
    assert 'value="hr@example.net"' in page, "what was typed is kept"
    assert "connection reset" not in page, "a server's words can quote the recipient"
    assert _frozen(user) == 0
    assert not application.events.exists()


def test_without_an_outbox_the_post_sends_nothing(client, user, filed, pdf):
    """The fields are not on the form for somebody with no outbox, so a posted tick is
    ignored: it is an ordinary freeze, and nothing is mailed behind their back."""
    application, cv, _letter = filed
    client.force_login(user)

    response = _post(client, application, cv=cv.pk, **_email())

    assert response.status_code == 302
    assert not application.events.filter(summary="Documents emailed").exists()


def test_another_sender_is_refused_not_rewritten(client, user, filed, server, pdf, monkeypatch):
    application, cv, _letter = filed
    original = correspondence.send

    def lying(person, message):
        message.from_email = "someone@else.example"
        return original(person, message)

    monkeypatch.setattr(correspondence, "send", lying)
    client.force_login(user)

    response = _post(client, application, cv=cv.pk, **_email())

    assert response.status_code == 200
    assert "not as someone@else.example" in response.content.decode()
    assert server.sent == [] and _frozen(user) == 0


def test_the_rate_limit_holds_and_the_page_says_so(client, user, filed, server, pdf, settings):
    settings.POSTULO_OUTBOX_RATE = "1/h"
    application, cv, _letter = filed
    client.force_login(user)

    assert _post(client, application, cv=cv.pk, **_email()).status_code == 302
    second = _post(client, application, cv=cv.pk, **_email())

    assert second.status_code == 200
    assert "Too many requests" in second.content.decode()
    assert len(server.sent) == 1
    assert _frozen(user) == 1, "only the one that went"


@pytest.mark.parametrize(
    "fields",
    [
        {"recipient": "not an address"},
        {"recipient": ""},
        {"subject": ""},
        {"body": ""},
    ],
)
def test_the_recipient_subject_and_message_are_checked(client, user, filed, server, pdf, fields):
    application, cv, _letter = filed
    client.force_login(user)

    response = _post(client, application, cv=cv.pk, **_email(**fields))

    assert response.status_code == 200
    assert server.sent == [] and _frozen(user) == 0


def test_without_ticking_the_box_nothing_is_mailed(client, user, filed, server, pdf):
    application, cv, _letter = filed
    client.force_login(user)

    response = _post(client, application, cv=cv.pk)

    assert response.status_code == 302
    assert server.sent == []
