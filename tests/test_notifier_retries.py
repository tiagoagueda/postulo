"""A send that fails at a notifier is tried again, on the webhook's schedule (#1163).

A notifier's `send` was called once. When it raised, the failure was written on the connection
and the notification was gone: a reminder announced while the mail server, Telegram or a push
service was unreachable for a minute never arrived, although a webhook delivery was retried
for ten hours. What is held here: the failed send waits in the queue a webhook's deliveries
wait in, and the pass that delivers those sends it again with the same notification, five
minutes on and doubling, as many times as a webhook is tried; a connection that is switched
off or retired is not dialled; two schedulers send a retry once between them; and the row
obeys the rules a webhook's row obeys (#752): gone with its connection and its account, its
text cleared once it has arrived, forgotten a week after its story is over, and never in an
archive.
"""

from __future__ import annotations

import datetime as dt
import json
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from postulo.notifications import webhooks
from postulo.notifications.base import Notification
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.notifications.service import notify
from postulo.plugins.api import ConnectionUnusable
from postulo.plugins.email import EmailNotifier
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

DUE = dt.datetime(2026, 10, 3, 9, 0, tzinfo=dt.UTC)


def mail_connection(user, **config):
    from allauth.account.models import EmailAddress

    EmailAddress.objects.get_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    return Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=True,
        config={"to": user.email, **config},
    )


def a_reminder(key="reminder:41:2026-10-03T09:00:00+00:00") -> Notification:
    return Notification(
        event="reminder_due",
        title="Chase them",
        body="Test Engineer at Aperture Science",
        url="https://postulo.example.org/applications/7/",
        key=key,
        occurred_at=DUE,
        data={"reminder_id": 41, "due_at": DUE.isoformat(), "application_id": 7},
    )


class Flaky:
    """A notifier's `send` that raises the first ``failures`` times and remembers every call.

    Set on the class in place of the method. An object is not bound as a function is, so it
    is called with what `send` is called with and no plugin in front.
    """

    def __init__(self, failures: int, error: Exception | None = None):
        self.failures = failures
        self.error = error or ConnectionError("smtp down")
        self.seen: list[Notification] = []

    def __call__(self, notification, config, user):
        self.seen.append(notification)
        if len(self.seen) <= self.failures:
            raise self.error


def later(minutes: float):
    """The clock, this many minutes on, for everything that asks Django the time."""
    return mock.patch.object(
        timezone, "now", return_value=timezone.now() + dt.timedelta(minutes=minutes)
    )


def make_due() -> None:
    WebhookDelivery.objects.filter(next_attempt_at__isnull=False).update(
        next_attempt_at=timezone.now()
    )


# ------------------------------------------------------------- the retry itself


def test_a_notifier_that_fails_once_delivers_on_a_later_pass_exactly_once(user, monkeypatch):
    """The notification was lost with the one failed send: nothing ever sent it again (#1163)."""
    connection = mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)

    assert notify(user, a_reminder()) == 0, "nothing took it yet"
    connection.refresh_from_db()
    assert connection.last_error == "ConnectionError: smtp down", "said at once, as before"

    row = WebhookDelivery.objects.get()
    assert row.connection == connection and row.owner == user
    assert row.status == DeliveryStatus.FAILED and row.attempts == 1
    assert "smtp down" in row.last_error

    with later(4):
        assert webhooks.send_pending() == (0, 0), "not before the five minutes are up"
    assert len(send.seen) == 1

    with later(6):
        assert webhooks.send_pending() == (1, 0)
        assert webhooks.send_pending() == (0, 0), "and not again"
    with later(600):
        assert webhooks.send_pending() == (0, 0), "nor ever"

    first, second = send.seen
    assert second == first, "the same notification, word for word"
    assert second.key == "reminder:41:2026-10-03T09:00:00+00:00"
    assert second.language == first.language != ""
    assert second.occurred_at == DUE and second.data["reminder_id"] == 41

    row.refresh_from_db()
    assert row.status == DeliveryStatus.SENT and row.sent_at and row.attempts == 2
    assert row.body == "", "the text is kept only until it has arrived (#752)"
    connection.refresh_from_db()
    assert connection.last_error == "" and connection.last_ok_at, "and the connection works again"


def test_the_retry_is_worded_and_sent_as_the_first_send_was(user):
    """The real notifier, the second time: the same message, to the same person."""
    mail_connection(user)
    with mock.patch.object(EmailNotifier, "send", Flaky(failures=1)):
        notify(user, a_reminder())
    assert not mail.outbox

    make_due()
    assert webhooks.send_pending() == (1, 0)

    (message,) = mail.outbox
    assert message.to == [user.email]
    assert message.subject == "[Postulo] Chase them"
    assert "Test Engineer at Aperture Science" in message.body
    assert "https://postulo.example.org/applications/7/" in message.body


def test_the_retry_runs_in_the_language_and_the_zone_the_first_send_ran_in(user, monkeypatch):
    """A notifier words what goes round the message; the scheduler has no language of its own."""
    from postulo.core import languages

    user.profile.language = "fr-FR"
    user.profile.time_zone = "America/New_York"
    user.profile.save()
    mail_connection(user)
    around = []

    def send(plugin, notification, config, recipient):
        around.append((languages.current(), timezone.get_current_timezone_name()))
        if len(around) == 1:
            raise ConnectionError("smtp down")

    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())
    make_due()
    assert webhooks.send_pending() == (1, 0)

    assert around == [("fr-FR", "America/New_York")] * 2


def test_a_send_that_works_queues_nothing(user):
    mail_connection(user)

    assert notify(user, a_reminder()) == 1

    assert len(mail.outbox) == 1 and not WebhookDelivery.objects.exists()


def test_only_the_connection_that_failed_is_tried_again(user, monkeypatch):
    """One retry per connection: the notifier that took the message is not handed it twice."""
    working = mail_connection(user)
    failing = Connection.objects.create(
        owner=user, kind="notifier", plugin="email", label="Other", config={"to": user.email}
    )
    calls = []

    def send(plugin, notification, config, recipient):
        calls.append(notification.key)
        if len(calls) == 1:
            raise ConnectionError("smtp down")

    monkeypatch.setattr(EmailNotifier, "send", send)

    assert notify(user, a_reminder()) == 1
    row = WebhookDelivery.objects.get()
    assert {row.connection_id} < {working.pk, failing.pk}
    make_due()
    assert webhooks.send_pending() == (1, 0)

    assert len(calls) == 3, "two connections, and one of them a second time"


def test_a_key_announced_again_while_its_retry_waits_makes_no_second_retry(user, monkeypatch):
    """A row is keyed as a webhook's is: one announcement, one row, however often it is made."""
    mail_connection(user)
    monkeypatch.setattr(EmailNotifier, "send", Flaky(failures=5))

    notify(user, a_reminder())
    notify(user, a_reminder())
    assert WebhookDelivery.objects.count() == 1

    notify(user, a_reminder(key=""))
    notify(user, a_reminder(key=""))
    assert WebhookDelivery.objects.count() == 3, "claiming no key means every announcement"


# ------------------------------------------------------------- when it stops


def test_retries_stop_after_as_many_attempts_as_a_webhook_gets(user, monkeypatch):
    """Five minutes, then doubling, and the first send counts as the first attempt."""
    mail_connection(user)
    send = Flaky(failures=1000)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())
    row = WebhookDelivery.objects.get()
    waits = [row.next_attempt_at - row.last_attempt_at]

    for _attempt in range(2, webhooks.MAX_ATTEMPTS + 5):
        make_due()
        webhooks.send_pending()
        row.refresh_from_db()
        if row.next_attempt_at is not None:
            waits.append(row.next_attempt_at - row.last_attempt_at)

    assert len(send.seen) == webhooks.MAX_ATTEMPTS, "the first send and seven retries"
    assert row.status == DeliveryStatus.GIVEN_UP and row.attempts == webhooks.MAX_ATTEMPTS
    assert row.next_attempt_at is None
    assert waits == [webhooks.FIRST_RETRY * 2**n for n in range(webhooks.MAX_ATTEMPTS - 1)]
    assert list(webhooks.pending(timezone.now() + dt.timedelta(days=30))) == []


def test_a_connection_that_is_switched_off_is_not_retried(user, monkeypatch):
    """As a webhook's rows do, it waits without spending attempts (#243)."""
    connection = mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())

    connection.enabled = False
    connection.save()
    make_due()

    assert webhooks.send_pending() == (0, 0)
    assert len(send.seen) == 1
    assert WebhookDelivery.objects.get().attempts == 1


def test_a_connection_that_was_retired_is_not_retried(user, monkeypatch):
    connection = mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())

    connection.retire("The address is no longer yours.")
    make_due()

    assert webhooks.send_pending() == (0, 0)
    assert len(send.seen) == 1


def test_a_connection_the_notifier_calls_finished_is_retired_and_nothing_is_queued(
    user, monkeypatch
):
    """`ConnectionUnusable` is a verdict, not a bad moment: no retry would end differently."""
    connection = mail_connection(user)
    monkeypatch.setattr(
        EmailNotifier, "send", Flaky(failures=1, error=ConnectionUnusable("Revoked."))
    )

    assert notify(user, a_reminder()) == 0

    connection.refresh_from_db()
    assert not connection.enabled and connection.last_error == "Revoked."
    assert not WebhookDelivery.objects.exists()


def test_a_retry_that_finds_the_connection_finished_retires_it_and_gives_up(user, monkeypatch):
    connection = mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())

    send.failures, send.error = 1000, ConnectionUnusable("Revoked.")
    make_due()
    assert webhooks.send_pending() == (0, 1)

    connection.refresh_from_db()
    assert not connection.enabled and connection.last_error == "Revoked."
    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.GIVEN_UP and row.next_attempt_at is None
    connection.enabled = True
    connection.save()
    assert webhooks.send_pending() == (0, 0), "switching it back on does not resend it"
    assert len(send.seen) == 2


def test_a_deactivated_account_is_not_retried_until_it_is_active_again(user, monkeypatch):
    """Nothing is sent to a deactivated account, whoever asks (#575)."""
    mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())
    make_due()

    user.is_active = False
    user.save(update_fields=["is_active"])
    assert webhooks.send_pending() == (0, 0)

    user.is_active = True
    user.save(update_fields=["is_active"])
    assert webhooks.send_pending() == (1, 0)
    assert len(send.seen) == 2


def test_a_notifier_that_only_queues_is_left_to_whatever_delivers_for_it(user, monkeypatch):
    """The webhook's own rows are the retry already; a second row would post it twice."""
    from postulo.plugins.webhook import WebhookNotifier

    connection = Connection(owner=user, kind="notifier", plugin="webhook", label="Hook")
    connection.config = {"url": "https://hooks.example.org/postulo"}
    connection.secrets = {"secret": "a-secret-of-sixteen-characters-or-more"}
    connection.save()
    monkeypatch.setattr(WebhookNotifier, "send", Flaky(failures=1))

    assert notify(user, a_reminder()) == 0

    assert not WebhookDelivery.objects.exists()


def test_a_notification_that_cannot_be_kept_fails_as_it_did_and_fails_nobody(user, monkeypatch):
    """What is queued is the text a webhook is sent; a value that has none is not queued."""
    connection = mail_connection(user)
    monkeypatch.setattr(EmailNotifier, "send", Flaky(failures=1))
    odd = a_reminder().but(data={"when": object()})

    assert notify(user, odd) == 0

    connection.refresh_from_db()
    assert connection.last_error == "ConnectionError: smtp down"
    assert not WebhookDelivery.objects.exists()


# ------------------------------------------------------------- two schedulers


def test_two_schedulers_that_found_the_same_retry_send_it_once(user, monkeypatch):
    """The lease is the webhook's: a conditional UPDATE only one pass can win (#221)."""
    mail_connection(user)
    send = Flaky(failures=1)
    monkeypatch.setattr(EmailNotifier, "send", send)
    notify(user, a_reminder())
    make_due()
    one, other = list(webhooks.pending()), list(webhooks.pending())
    assert len(one) == len(other) == 1, "both read the row before either took it"

    with mock.patch.object(webhooks, "pending", side_effect=[one, other]):
        assert webhooks.send_pending() == (1, 0)
        assert webhooks.send_pending() == (0, 0)

    assert len(send.seen) == 2, "the first send and one retry"


def test_a_retry_taken_by_one_pass_cannot_be_claimed_by_another(user, monkeypatch):
    mail_connection(user)
    monkeypatch.setattr(EmailNotifier, "send", Flaky(failures=1))
    notify(user, a_reminder())
    make_due()
    (mine,), (theirs,) = list(webhooks.pending()), list(webhooks.pending())

    assert webhooks.claim(mine) is True
    assert webhooks.claim(theirs) is False


# ------------------------------------------- the rules a webhook's row obeys (#752)


def failed_send(user, monkeypatch) -> WebhookDelivery:
    mail_connection(user)
    monkeypatch.setattr(EmailNotifier, "send", Flaky(failures=1000))
    notify(user, a_reminder())
    return WebhookDelivery.objects.get()


def test_deleting_the_account_deletes_what_was_waiting_to_be_sent(
    user, other_user, monkeypatch, settings, tmp_path, django_capture_on_commit_callbacks
):
    from postulo.accounts import deletion

    settings.MEDIA_ROOT = str(tmp_path)
    failed_send(user, monkeypatch)
    mail_connection(other_user)
    notify(other_user, a_reminder())
    assert WebhookDelivery.objects.count() == 2

    with django_capture_on_commit_callbacks(execute=True):
        deletion.delete_account(user)

    (left,) = WebhookDelivery.objects.all()
    assert left.owner == other_user, "and nobody else's"


def test_deleting_the_connection_deletes_what_it_was_still_to_be_told(user, monkeypatch):
    row = failed_send(user, monkeypatch)

    row.connection.delete()

    assert not WebhookDelivery.objects.exists()


def test_a_week_after_it_was_given_up_on_the_row_is_forgotten(user, monkeypatch):
    row = failed_send(user, monkeypatch)
    for _attempt in range(webhooks.MAX_ATTEMPTS):
        make_due()
        webhooks.send_pending()
    row.refresh_from_db()
    assert row.status == DeliveryStatus.GIVEN_UP

    with later(60 * 24 * 6):
        assert webhooks.forget_old() == 0
    with later(60 * 24 * 8):
        assert webhooks.forget_old() == 1
    assert not WebhookDelivery.objects.exists()


def test_the_archive_carries_no_queue_and_its_format_is_what_it_was(user, monkeypatch):
    """A connection and what waits on it were never in an archive, and still are not."""
    from postulo.core import export

    row = failed_send(user, monkeypatch)
    assert "Chase them" in row.body

    document = json.dumps(export.build_document(user))

    assert "Chase them" not in document and "Aperture Science" not in document
    assert "smtp down" not in document


def test_nobody_elses_failed_send_is_queued_for_me(user, other_user, monkeypatch):
    mail_connection(user)
    mail_connection(other_user)
    monkeypatch.setattr(EmailNotifier, "send", Flaky(failures=1))

    notify(other_user, a_reminder())

    row = WebhookDelivery.objects.get()
    assert row.owner == other_user and row.connection.owner == other_user
