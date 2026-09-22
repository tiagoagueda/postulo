"""The webhook notifier, and the events about what a person did (#240).

Integrations had to poll. What is held here: the three new events default off for a
notifier that reaches a person and on for the webhook, and a connection saved before an
event existed does not start receiving it; a webhook `send` writes a row and posts nothing;
the scheduler delivers with backoff and gives up on a client error; the signature can be
verified and a stale one cannot; a notification's key means one delivery however many times
it is announced; and a status change, an interview and an offer reach a webhook -- and reach
nobody's mail.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal
from unittest import mock

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.applications.services import change_status, record_offer, schedule_interview
from postulo.jobs.models import Company, JobPosting
from postulo.notifications import base, slow, webhooks
from postulo.notifications.base import Notification, default_for, wants
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.notifications.service import notify
from postulo.plugins import webhook
from postulo.plugins.email import EmailNotifier
from postulo.plugins.models import Connection
from postulo.plugins.webhook import WebhookNotifier

pytestmark = pytest.mark.django_db

SECRET = "a-secret-of-sixteen-characters-or-more"


def webhook_connection(user, url="https://hooks.example.org/postulo", **config):
    connection = Connection(
        owner=user, kind="notifier", plugin="webhook", label="My automation", enabled=True
    )
    connection.config = {"url": url, **config}
    connection.secrets = {"secret": SECRET}
    connection.save()
    return connection


def an_application(user, title="Test Engineer"):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title=title)
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


class Answer:
    """What `post` returns, without a network."""

    def __init__(self, status_code: int):
        self.status_code = status_code


# ------------------------------------------------------------- the defaults


def test_what_the_person_did_is_off_for_a_person_and_on_for_a_machine():
    for event in base.ABOUT_WHAT_YOU_DID:
        assert not default_for(event, EmailNotifier())
        assert default_for(event, WebhookNotifier())
    assert default_for("reminder_due", EmailNotifier())
    assert default_for("reminder_due", WebhookNotifier())


def test_a_connection_saved_before_an_event_existed_does_not_start_receiving_it():
    """The switch is missing from an old configuration, and missing means the plugin's
    default -- not yes, which is what it used to mean."""
    old_config = {"to": "me@example.org", "event_reminder_due": True}
    assert wants(old_config, "reminder_due", EmailNotifier())
    assert not wants(old_config, "status_changed", EmailNotifier())
    assert wants(old_config, "status_changed", WebhookNotifier())
    assert not wants({"event_status_changed": False}, "status_changed", WebhookNotifier())


def test_the_form_opens_with_the_plugins_defaults(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:create", args=["notifier", "webhook"])).content.decode()
    assert 'name="plugin_url"' in html and 'name="plugin_secret"' in html
    import re

    assert re.search(r'name="plugin_event_status_changed"[^>]*checked', html)
    assert re.search(r'name="plugin_event_reminder_due"[^>]*checked', html)

    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    mail = client.get(reverse("connections:create", args=["notifier", "email"])).content.decode()
    assert not re.search(r'name="plugin_event_status_changed"[^>]*checked', mail)


def test_the_form_refuses_a_short_secret_and_a_private_address(client, user):
    client.force_login(user)
    response = client.post(
        reverse("connections:create", args=["notifier", "webhook"]),
        {
            "label": "x",
            "enabled": "on",
            "plugin_url": "http://127.0.0.1/hook",
            "plugin_secret": "short",
        },
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "At least 16 characters" in html
    assert "private" in html.lower()


# ------------------------------------------------------------- the signature


def test_a_receiver_can_verify_the_signature_and_refuses_a_stale_one():
    body = webhook.encode({"event": "test"})
    now = int(time.time())
    header = webhook.signature_header(SECRET, now, body)

    assert webhook.verify(SECRET, header, body)
    assert not webhook.verify("another secret entirely", header, body)
    assert not webhook.verify(SECRET, header, body + " ")
    assert not webhook.verify(SECRET, header, body, now=now + 600), "ten minutes old"
    assert not webhook.verify(SECRET, "t=abc,v1=00", body)


# --------------------------------------------------------------- the queue


def test_send_writes_a_row_and_posts_nothing(user):
    connection = webhook_connection(user)
    message = Notification(event="reminder_due", title="Chase them", key="reminder:1")

    with mock.patch.object(webhook, "post") as posted:
        taken = notify(user, message)

    assert taken == 1 and not posted.called
    (row,) = WebhookDelivery.objects.all()
    assert row.connection == connection and row.status == DeliveryStatus.PENDING
    assert json.loads(row.body)["title"] == "Chase them"
    assert json.loads(row.body)["version"] == webhook.PAYLOAD_VERSION


def test_a_key_means_one_delivery_however_often_it_is_announced(user):
    webhook_connection(user)
    message = Notification(event="reminder_due", title="Chase them", key="reminder:1")
    notify(user, message)
    notify(user, message)
    assert WebhookDelivery.objects.count() == 1

    notify(user, Notification(event="reminder_due", title="No key"))
    notify(user, Notification(event="reminder_due", title="No key"))
    assert WebhookDelivery.objects.count() == 3, "claiming no key means every announcement"


def test_the_scheduler_delivers_signed_and_records_the_answer(user):
    webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="Chase them", key="reminder:1"))
    seen = {}

    def fake_post(url, secret, body, *, event, delivery):
        seen.update(url=url, secret=secret, body=body, event=event, delivery=delivery)
        return Answer(200)

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", fake_post),
    ):
        assert webhooks.send_pending() == (1, 0)

    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.SENT and row.last_status == 200 and row.sent_at
    assert seen["url"] == "https://hooks.example.org/postulo" and seen["secret"] == SECRET
    assert seen["event"] == "reminder_due" and seen["delivery"] == str(row.pk)
    assert seen["body"] == row.body, "the bytes that were signed are the bytes that were sent"
    assert webhooks.send_pending() == (0, 0), "and not again"


def test_a_receiver_that_is_down_is_retried_with_backoff_and_then_given_up_on(user):
    webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))

    def refused(*args, **kwargs):
        raise httpx.ConnectError("refused")

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", refused),
    ):
        assert webhooks.send_pending() == (0, 1)
    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.FAILED and row.attempts == 1
    assert row.next_attempt_at > timezone.now() + dt.timedelta(minutes=4)
    assert "ConnectError" in row.last_error

    for _attempt in range(2, webhooks.MAX_ATTEMPTS + 1):
        WebhookDelivery.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())
        with (
            mock.patch.object(webhook, "check_destination", lambda url: None),
            mock.patch.object(webhook, "post", refused),
        ):
            webhooks.send_pending()
    row.refresh_from_db()
    assert row.status == DeliveryStatus.GIVEN_UP and row.attempts == webhooks.MAX_ATTEMPTS
    assert row.next_attempt_at is None


def test_a_client_error_is_not_retried_but_a_429_is(user):
    webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", lambda *a, **k: Answer(404)),
    ):
        webhooks.send_pending()
    assert WebhookDelivery.objects.get().status == DeliveryStatus.GIVEN_UP

    notify(user, Notification(event="reminder_due", title="y", key="r:2"))
    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", lambda *a, **k: Answer(429)),
    ):
        webhooks.send_pending()
    assert WebhookDelivery.objects.get(key="r:2").status == DeliveryStatus.FAILED


def test_a_switched_off_connection_waits_rather_than_spending_its_attempts(user):
    connection = webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    connection.enabled = False
    connection.save()
    with mock.patch.object(webhook, "post") as posted:
        assert webhooks.send_pending() == (0, 0)
    assert not posted.called
    assert WebhookDelivery.objects.get().attempts == 0


def test_a_private_destination_is_refused_at_delivery_too(user, settings):
    """A public hostname can come to point somewhere private later, so the check is not
    only the form's."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    webhook_connection(user, url="http://10.0.0.5/hook")
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    with mock.patch.object(webhook, "post") as posted:
        assert webhooks.send_pending() == (0, 1)
    assert not posted.called
    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.GIVEN_UP


def test_nobody_elses_webhook_hears_a_thing(user, other_user):
    webhook_connection(other_user)
    notify(user, Notification(event="reminder_due", title="Mine", key="r:1"))
    assert WebhookDelivery.objects.count() == 0


# ----------------------------------------------------------- what you did


def test_a_status_change_reaches_a_webhook_and_not_the_mail(user, settings):
    settings.POSTULO_BACKGROUND_WORK = False
    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail",
        enabled=True,
        config={"to": user.email},
    )
    webhook_connection(user)
    application = an_application(user)

    from django.core import mail

    change_status(application, Status.INTERVIEWING)

    row = WebhookDelivery.objects.get()
    payload = json.loads(row.body)
    assert payload["event"] == "status_changed"
    assert payload["data"]["to_status"] == Status.INTERVIEWING
    assert payload["data"]["from_status"] == Status.APPLIED
    assert "Test Engineer" in payload["title"] and "Interviewing" in payload["title"]
    assert not mail.outbox, "a person's own notifier does not tell them what they just did"


def test_no_webhook_means_no_errand_per_click(user, settings):
    """A person with no webhook -- the ordinary case -- must not collect an errand row per
    status change for a message nobody would receive."""
    from postulo.core.models import Errand

    application = an_application(user)
    change_status(application, Status.INTERVIEWING)
    assert not Errand.objects.filter(kind="notify").exists()


def test_an_interview_and_an_offer_reach_a_webhook(user, settings):
    settings.POSTULO_BACKGROUND_WORK = False
    webhook_connection(user)
    application = an_application(user)

    interview = schedule_interview(
        application, kind="video", starts_at=timezone.now() + dt.timedelta(days=2)
    )
    record_offer(application, base_amount=Decimal("65000"), currency="EUR")

    events = sorted(WebhookDelivery.objects.values_list("event", flat=True))
    assert "interview_scheduled" in events and "offer_recorded" in events
    scheduled = WebhookDelivery.objects.get(event="interview_scheduled")
    assert json.loads(scheduled.body)["data"]["interview_id"] == interview.pk
    offered = WebhookDelivery.objects.get(event="offer_recorded")
    assert "65,000 EUR" in json.loads(offered.body)["body"]


def test_the_builders_word_the_three_events(user):
    payload = {
        "application_id": 1,
        "event_id": 9,
        "role": "Engineer",
        "company": "Aperture",
        "status": "Offer",
    }
    message = slow.BUILDERS["status_changed"](payload)
    assert message.key == "status:1:9" and "Aperture" in message.title
    moved = slow.BUILDERS["interview_scheduled"](
        {
            "interview_id": 3,
            "application_id": 1,
            "starts_at": "x",
            "moved": True,
            "role": "E",
            "company": "A",
        }
    )
    assert "moved" in moved.title.lower() and moved.data["moved"]
    offered = slow.BUILDERS["offer_recorded"](
        {
            "offer_id": 4,
            "application_id": 1,
            "event_id": 2,
            "terms": "1 EUR",
            "role": "E",
            "company": "A",
        }
    )
    assert offered.key == "offer:4:2"


# ------------------------------------------------------------- the test button


def test_the_test_button_posts_one_signed_request(user):
    plugin = WebhookNotifier()
    seen = {}

    def fake_post(url, secret, body, *, event, delivery):
        seen.update(url=url, event=event, body=json.loads(body))
        return Answer(204)

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", fake_post),
    ):
        result = plugin.test({"url": "https://hooks.example.org/x", "secret": SECRET}, user)

    assert result.ok and seen["event"] == "test" and seen["body"]["event"] == "test"
