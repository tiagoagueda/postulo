"""A webhook delivery keeps its notification's text no longer than it needs it (#752)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from postulo.notifications import webhooks
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def connection(user):
    return Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="webhook",
        label="Hook",
        config={"url": "https://x.test/h"},
    )


def row(user, connection, status, *, days_ago=0, body='{"role": "Tester", "terms": "private"}'):
    when = timezone.now() - dt.timedelta(days=days_ago)
    return WebhookDelivery.objects.create(
        owner=user,
        connection=connection,
        event="offer_recorded",
        key=f"k-{status}-{days_ago}",
        body=body,
        status=status,
        sent_at=when if status == DeliveryStatus.SENT else None,
        last_attempt_at=when,
    )


def test_a_week_on_the_finished_rows_are_gone_and_the_live_ones_stay(user, connection):
    old_sent = row(user, connection, DeliveryStatus.SENT, days_ago=9)
    old_given_up = row(user, connection, DeliveryStatus.GIVEN_UP, days_ago=9)
    fresh_sent = row(user, connection, DeliveryStatus.SENT, days_ago=1)
    waiting = row(user, connection, DeliveryStatus.PENDING, days_ago=30)
    retrying = row(user, connection, DeliveryStatus.FAILED, days_ago=30)

    assert webhooks.forget_old() == 2

    left = set(WebhookDelivery.objects.values_list("pk", flat=True))
    assert left == {fresh_sent.pk, waiting.pk, retrying.pk}
    assert old_sent.pk not in left and old_given_up.pk not in left


def test_deleting_the_connection_deletes_what_it_was_told(user, connection):
    row(user, connection, DeliveryStatus.PENDING)

    connection.delete()

    assert not WebhookDelivery.objects.exists()


def test_the_text_is_cleared_once_the_receiver_has_it(user, connection, monkeypatch):
    waiting = row(user, connection, DeliveryStatus.PENDING)

    class Reply:
        status_code = 204

    monkeypatch.setattr(webhooks.plugin, "post", lambda *a, **k: Reply())
    monkeypatch.setattr(webhooks.plugin, "check_destination", lambda url: None)
    waiting.next_attempt_at = timezone.now()
    waiting.save()

    assert webhooks.deliver(waiting) is True

    waiting.refresh_from_db()
    assert waiting.status == DeliveryStatus.SENT and waiting.body == ""
