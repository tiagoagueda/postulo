"""A notifier may keep a message until a time of its choosing, and take several at once (#1259).

Two optional names, read like ``delivers_later``: ``hold_until`` is asked before ``send``,
and a time still to come keeps the message in the queue a failed send already waits in
(#1163); ``send_many`` is handed every due message of one connection together. What is held
here is what a held message must not do: arrive early, arrive twice when two schedulers find
it, reach somebody else, outlive its connection or its account, travel in an archive, or be
lost when the send that releases it fails.
"""

from __future__ import annotations

import datetime as dt
import json
from unittest import mock

import pytest
from django.utils import timezone

from postulo.notifications import webhooks
from postulo.notifications.base import Notification
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.notifications.service import notify
from postulo.plugins.api import ConnectionUnusable
from postulo.plugins.email import EmailNotifier
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


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


def a_message(n: int = 1) -> Notification:
    return Notification(
        event="reminder_due",
        title=f"Chase them {n}",
        body="Test Engineer at Aperture Science",
        url="https://postulo.example.org/applications/7/",
        key=f"reminder:{n}:2026-11-17T09:00:00+00:00",
    )


def later(minutes: float):
    return mock.patch.object(
        timezone, "now", return_value=timezone.now() + dt.timedelta(minutes=minutes)
    )


class Keeper:
    """A notifier's two optional names, set on the e-mail notifier's class for one test.

    ``until`` is how many minutes from now ``hold_until`` answers; ``failures`` how many times
    ``send_many`` raises before it works. Every batch it is handed is remembered.
    """

    def __init__(self, monkeypatch, *, until: float | None = 60, failures: int = 0, many=True):
        self.until = until
        self.failures = failures
        self.batches: list[list[Notification]] = []
        self.single: list[Notification] = []
        self.zones: list[str] = []
        keeper = self

        def hold_until(plugin, notification, config, user):
            keeper.zones.append(str(timezone.get_current_timezone()))
            if keeper.until is None:
                return None
            return timezone.now() + dt.timedelta(minutes=keeper.until)

        def send_many(plugin, notifications, config, user):
            keeper.batches.append(list(notifications))
            if len(keeper.batches) <= keeper.failures:
                raise ConnectionError("smtp down")

        def send(plugin, notification, config, user):
            keeper.single.append(notification)

        monkeypatch.setattr(EmailNotifier, "hold_until", hold_until, raising=False)
        monkeypatch.setattr(EmailNotifier, "send", send)
        if many:
            monkeypatch.setattr(EmailNotifier, "send_many", send_many, raising=False)


def test_a_held_message_is_taken_kept_and_not_sent_before_its_time(user, monkeypatch):
    connection = mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)

    assert notify(user, a_message()) == 1, "kept is taken"

    row = WebhookDelivery.objects.get()
    assert row.owner == user and row.connection == connection
    assert row.status == DeliveryStatus.PENDING and row.attempts == 0
    assert keeper.single == [] and keeper.batches == []
    connection.refresh_from_db()
    assert connection.last_ok_at is None and connection.last_error == "", "no send made yet"

    with later(59):
        assert webhooks.send_pending() == (0, 0)
    assert keeper.batches == []


def test_held_messages_of_one_connection_are_handed_over_together_once(user, monkeypatch):
    connection = mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)
    for n in (1, 2, 3):
        notify(user, a_message(n))
    assert WebhookDelivery.objects.count() == 3

    with later(61):
        webhooks.send_pending()
        webhooks.send_pending()

    (batch,) = keeper.batches
    assert [message.title for message in batch] == ["Chase them 1", "Chase them 2", "Chase them 3"]
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {DeliveryStatus.SENT}
    assert set(WebhookDelivery.objects.values_list("body", flat=True)) == {""}, "text dropped"
    connection.refresh_from_db()
    assert connection.last_error == ""


def test_without_send_many_each_held_message_goes_through_send(user, monkeypatch):
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=60, many=False)
    notify(user, a_message(1))
    notify(user, a_message(2))

    with later(61):
        webhooks.send_pending()

    assert [message.title for message in keeper.single] == ["Chase them 1", "Chase them 2"]


def test_a_notifier_that_says_now_sends_now(user, monkeypatch):
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=None)

    assert notify(user, a_message()) == 1

    assert [message.title for message in keeper.single] == ["Chase them 1"]
    assert not WebhookDelivery.objects.exists()


def test_a_time_already_past_or_a_hold_that_raises_sends_now(user, monkeypatch):
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=-5)
    notify(user, a_message(1))

    def broken(plugin, notification, config, user):
        raise RuntimeError("no idea")

    monkeypatch.setattr(EmailNotifier, "hold_until", broken, raising=False)
    notify(user, a_message(2))

    assert [message.title for message in keeper.single] == ["Chase them 1", "Chase them 2"]
    assert not WebhookDelivery.objects.exists()


def test_hold_until_is_asked_in_the_recipients_time_zone(user, monkeypatch):
    user.profile.time_zone = "Asia/Tokyo"
    user.profile.save(update_fields=["time_zone"])
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)

    notify(user, a_message())

    assert keeper.zones == ["Asia/Tokyo"]


def test_a_webhook_is_never_asked_to_hold(user, monkeypatch):
    from postulo.plugins.webhook import WebhookNotifier

    asked = []
    monkeypatch.setattr(WebhookNotifier, "hold_until", lambda *args: asked.append(1), raising=False)
    connection = Connection(owner=user, kind="notifier", plugin="webhook", label="Hook")
    connection.config = {"url": "https://hooks.example.org/postulo"}
    connection.secrets = {"secret": "a-secret-of-sixteen-characters-or-more"}
    connection.save()

    notify(user, a_message())

    assert asked == []


def test_the_same_key_held_twice_is_one_message(user, monkeypatch):
    mail_connection(user)
    Keeper(monkeypatch, until=60)

    notify(user, a_message())
    notify(user, a_message())

    assert WebhookDelivery.objects.count() == 1


# ------------------------------------------------------------- two schedulers


def test_two_schedulers_that_found_the_same_held_messages_hand_them_over_once(user, monkeypatch):
    """Each row is claimed as the pass claims one, so a digest is never sent twice (#221)."""
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)
    for n in (1, 2):
        notify(user, a_message(n))

    with later(61):
        one, other = list(webhooks.pending()), list(webhooks.pending())
        assert len(one) == len(other) == 2
        with mock.patch.object(webhooks, "pending", side_effect=[one, other]):
            webhooks.send_pending()
            webhooks.send_pending()

    assert len(keeper.batches) == 1 and len(keeper.batches[0]) == 2


def test_a_pass_hands_over_only_what_it_claimed(user, monkeypatch):
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)
    for n in (1, 2):
        notify(user, a_message(n))

    with later(61):
        _first, second = list(webhooks.pending())
        assert webhooks.claim(second) is True, "another pass took the second already"
        webhooks.send_pending()

    (batch,) = keeper.batches
    assert [message.title for message in batch] == ["Chase them 1"]


# ------------------------------------------------------------- after the release


def test_a_failure_after_the_release_retries_the_group_from_the_first_attempt(user, monkeypatch):
    connection = mail_connection(user)
    keeper = Keeper(monkeypatch, until=60, failures=1)
    for n in (1, 2):
        notify(user, a_message(n))

    with later(61):
        assert webhooks.send_pending()[0] == 0
    rows = list(WebhookDelivery.objects.order_by("pk"))
    assert [row.status for row in rows] == [DeliveryStatus.FAILED] * 2
    assert [row.attempts for row in rows] == [1, 1], "counted from the release, not before"
    connection.refresh_from_db()
    assert "smtp down" in connection.last_error

    with later(61 + 6):
        webhooks.send_pending()

    assert len(keeper.batches) == 2 and len(keeper.batches[1]) == 2, "retried together"
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {DeliveryStatus.SENT}


def test_a_connection_finished_at_the_release_is_retired_and_nothing_is_retried(user, monkeypatch):
    connection = mail_connection(user)
    Keeper(monkeypatch, until=60)

    def finished(plugin, notifications, config, user):
        raise ConnectionUnusable("That address is no longer yours.")

    for n in (1, 2):
        notify(user, a_message(n))
    monkeypatch.setattr(EmailNotifier, "send_many", finished, raising=False)

    with later(61):
        webhooks.send_pending()

    connection.refresh_from_db()
    assert connection.enabled is False
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {
        DeliveryStatus.GIVEN_UP
    }


# ------------------------------------------------------------- whose it is


def test_nobody_elses_held_message_is_handed_over_with_mine(user, other_user, monkeypatch):
    mail_connection(user)
    mail_connection(other_user)
    keeper = Keeper(monkeypatch, until=60)
    notify(user, a_message(1))
    notify(other_user, a_message(2))

    with later(61):
        webhooks.send_pending()

    assert sorted(len(batch) for batch in keeper.batches) == [1, 1]
    for batch in keeper.batches:
        (message,) = batch
        row = WebhookDelivery.objects.get(key=message.key)
        assert row.status == DeliveryStatus.SENT


def test_a_deactivated_account_gets_nothing_until_it_is_active_again(user, monkeypatch):
    mail_connection(user)
    keeper = Keeper(monkeypatch, until=60)
    notify(user, a_message())
    user.is_active = False
    user.save(update_fields=["is_active"])

    with later(61):
        webhooks.send_pending()
    assert keeper.batches == []

    user.is_active = True
    user.save(update_fields=["is_active"])
    with later(61):
        webhooks.send_pending()
    assert len(keeper.batches) == 1


def test_deleting_the_connection_deletes_what_it_was_keeping(user, monkeypatch):
    connection = mail_connection(user)
    Keeper(monkeypatch, until=60)
    notify(user, a_message())

    connection.delete()

    assert not WebhookDelivery.objects.exists()


def test_the_archive_carries_nothing_that_is_kept(user, monkeypatch):
    from postulo.core import export

    mail_connection(user)
    Keeper(monkeypatch, until=60)
    notify(user, a_message())
    assert "Chase them 1" in WebhookDelivery.objects.get().body

    document = json.dumps(export.build_document(user))

    assert "Chase them 1" not in document
