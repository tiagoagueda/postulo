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


def test_a_hostname_that_fails_to_resolve_is_retried_not_blamed_on_a_private_address(
    user, settings
):
    """A resolver hiccup is transient whatever the operator's address policy says (#549)."""
    import socket

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    with (
        mock.patch("socket.getaddrinfo", side_effect=socket.gaierror(socket.EAI_AGAIN, "again")),
        mock.patch.object(webhook, "post") as posted,
    ):
        assert webhooks.send_pending() == (0, 1)
    assert not posted.called
    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.FAILED and row.attempts == 1
    assert row.next_attempt_at > timezone.now()
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" not in row.last_error


# ------------------------------------------- what the connection says about it


def test_the_webhook_says_it_delivers_later_and_the_mail_does_not():
    assert WebhookNotifier.delivers_later is True
    assert not getattr(EmailNotifier, "delivers_later", False)


def test_queueing_an_event_is_not_recorded_as_a_delivery(user):
    """`send` writes a row. Nothing has reached the receiver, so nothing has worked yet."""
    connection = webhook_connection(user)

    taken = notify(user, Notification(event="reminder_due", title="x", key="r:1"))

    connection.refresh_from_db()
    assert taken == 1, "the event was taken all the same"
    assert connection.last_ok_at is None and connection.last_error == ""


def test_a_failing_webhook_still_says_so_after_the_next_event_is_queued(user):
    """The delivery pass is the only one that knows how a delivery went (#574).

    `notify` treated any `send` that returned as a delivery and marked the connection as
    working, which for a notifier that only queues wiped what the pass had written. A
    receiver answering 404 all day read "worked" on Connections whenever anything else
    had happened since, and that page is the only place a person learns their automation
    has stopped hearing from Postulo.
    """
    connection = webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "post", lambda *a, **k: Answer(404)),
    ):
        webhooks.send_pending()
    connection.refresh_from_db()
    assert "404" in connection.last_error

    notify(user, Notification(event="reminder_due", title="y", key="r:2"))

    connection.refresh_from_db()
    assert "404" in connection.last_error, "queueing another event is not a delivery"
    assert connection.last_ok_at is None


def test_a_destination_refused_at_delivery_is_said_on_the_connection(user, settings):
    """The row was given up on and the connection never said why, or that anything had."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    connection = webhook_connection(user, url="http://10.0.0.5/hook")
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))

    with mock.patch.object(webhook, "post"):
        webhooks.send_pending()

    connection.refresh_from_db()
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in connection.last_error
    assert connection.last_error == WebhookDelivery.objects.get().last_error


def test_nobody_elses_webhook_hears_a_thing(user, other_user):
    webhook_connection(other_user)
    notify(user, Notification(event="reminder_due", title="Mine", key="r:1"))
    assert WebhookDelivery.objects.count() == 0


# ----------------------------------------------------------- what you did


def test_a_status_change_reaches_a_webhook_and_not_the_mail(
    user, settings, django_capture_on_commit_callbacks
):
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

    with django_capture_on_commit_callbacks(execute=True):
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


def test_an_interview_and_an_offer_reach_a_webhook(
    user, settings, django_capture_on_commit_callbacks
):
    settings.POSTULO_BACKGROUND_WORK = False
    webhook_connection(user)
    application = an_application(user)

    with django_capture_on_commit_callbacks(execute=True):
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
        "to_status": "offer",
    }
    message = slow.BUILDERS["status_changed"](payload)
    assert message.key == "status:1:9" and "Aperture" in message.title
    assert message.title == "Engineer at Aperture: Offer"
    # An errand queued before the label was looked up by the builder carries only the label.
    old = slow.BUILDERS["status_changed"]({**payload, "to_status": "", "status": "Offered"})
    assert old.title == "Engineer at Aperture: Offered"
    moved = slow.BUILDERS["interview_scheduled"](
        {
            "interview_id": 3,
            "application_id": 1,
            "starts_at": "2026-11-02T15:00:00+00:00",
            "moved": True,
            "role": "E",
            "company": "A",
        }
    )
    assert "moved" in moved.title.lower() and moved.data["moved"]
    assert moved.data["starts_at"] == "2026-11-02T15:00:00+00:00"
    assert moved.body and "T15:00" not in moved.body and "2026" in moved.body
    unparsed = slow.BUILDERS["interview_scheduled"](
        {"interview_id": 3, "application_id": 1, "starts_at": "x", "role": "E", "company": "A"}
    )
    assert unparsed.body == "x"
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


def test_a_deactivated_account_has_no_deliveries_pending_and_gets_no_new_ones(user):
    """The rows wait, so reactivating resumes them; `notify` itself queues nothing (#575)."""
    webhook_connection(user)
    message = Notification(event="reminder_due", title="Chase them", key="reminder:1")
    assert notify(user, message) == 1
    assert webhooks.pending().count() == 1

    user.is_active = False
    user.save(update_fields=["is_active"])
    assert list(webhooks.pending()) == []
    assert notify(user, message.but(key="reminder:2")) == 0
    assert WebhookDelivery.objects.count() == 1

    user.is_active = True
    user.save(update_fields=["is_active"])
    assert webhooks.pending().count() == 1


# ------------------------------------------------------------- a receiver that redirects


def redirecting_receiver(status=301):
    """A receiver that answers `status` to the first address and 200 to the one it names."""
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.scheme == "http":
            return httpx.Response(status, headers={"Location": "https://hooks.example.org/in"})
        return httpx.Response(200)

    def make_client(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return requests, make_client


@pytest.mark.parametrize(
    ("status", "final"),
    [(301, True), (308, True), (302, False), (303, False), (307, False)],
)
def test_a_redirect_is_a_failure_that_names_where_it_pointed_and_posts_once(user, status, final):
    webhook_connection(user, url="http://hooks.example.org/in")
    notify(user, Notification(event="reminder_due", title="Chase them", key="reminder:1"))
    requests, make_client = redirecting_receiver(status)

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "client", make_client),
    ):
        assert webhooks.send_pending() == (0, 1)

    row = WebhookDelivery.objects.get()
    assert len(requests) == 1 and requests[0].method == "POST"
    assert row.status != DeliveryStatus.SENT
    assert (row.status == DeliveryStatus.GIVEN_UP) is final
    assert "https://hooks.example.org/in" in row.last_error
    assert row.last_status == status


def test_the_test_button_reports_a_redirect_rather_than_answering_200(user):
    requests, make_client = redirecting_receiver(301)

    with (
        mock.patch.object(webhook, "check_destination", lambda url: None),
        mock.patch.object(webhook, "client", make_client),
    ):
        result = WebhookNotifier().test(
            {"url": "http://hooks.example.org/in", "secret": SECRET}, user
        )

    assert len(requests) == 1
    assert not result.ok and "https://hooks.example.org/in" in result.message


def test_a_secret_nobody_can_read_gives_the_row_up_instead_of_ending_the_pass(user, settings):
    """Raised out of `deliver` it ended every scheduler pass that met the row (#573)."""
    settings.POSTULO_FIELD_KEY = ""
    connection = webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    settings.SECRET_KEY = "a-key-rotated-since-the-secret-was-written"

    with mock.patch.object(webhook, "post") as post:
        assert webhooks.send_pending() == (0, 1)

    post.assert_not_called()
    row = WebhookDelivery.objects.get()
    assert row.status == DeliveryStatus.GIVEN_UP
    assert "different key" in row.last_error
    connection.refresh_from_db()
    assert connection.last_error == row.last_error


def test_a_row_that_raises_does_not_stop_the_rows_behind_it(user):
    webhook_connection(user)
    notify(user, Notification(event="reminder_due", title="x", key="r:1"))
    notify(user, Notification(event="reminder_due", title="y", key="r:2"))

    with mock.patch.object(webhooks, "deliver", side_effect=[RuntimeError("boom"), True]):
        assert webhooks.send_pending() == (1, 1)


def test_nothing_is_announced_before_the_change_is_committed(
    user, settings, django_capture_on_commit_callbacks
):
    """A notifier is not called under the write lock, nor for a change rolled back (#578)."""
    from django.db import transaction

    settings.POSTULO_BACKGROUND_WORK = False
    webhook_connection(user)
    application = an_application(user)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        try:
            with transaction.atomic():
                change_status(application, Status.INTERVIEWING)
                raise RuntimeError("a later row of the import failed")
        except RuntimeError:
            pass
    assert callbacks == [] and not WebhookDelivery.objects.exists(), "rolled back, so unsaid"

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with transaction.atomic():
            change_status(application, Status.INTERVIEWING)
        assert not WebhookDelivery.objects.exists(), "not while the transaction is open"
    assert len(callbacks) == 1
    callbacks[0]()
    assert WebhookDelivery.objects.count() == 1
