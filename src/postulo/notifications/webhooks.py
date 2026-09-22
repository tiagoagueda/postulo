"""Where a webhook delivery waits, and the pass that sends it (#240).

The plugin (`postulo.plugins.webhook`) signs and posts; this is the queue in front of it. A
delivery is a row first and a request later, made by the scheduler with backoff and given up
on after a fixed number of tries -- the same shape as a document's copy on its way to an
external store, and for the same reasons: nothing is posted from inside the request that
caused the event, and a receiver down for an afternoon gets everything when it comes back.

**Once per thing, however many passes.** A row is keyed by the notification's stable key
(#229), so a retried errand or a second scheduler cannot make the same reminder arrive
twice. A notification that claims no key is delivered every time it is announced, which is
what claiming no key means.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.plugins import webhook as plugin
from postulo.plugins.api import DestinationRefused, Notification

from .models import DeliveryStatus, WebhookDelivery

logger = logging.getLogger(__name__)

#: The retry schedule: doubled from the first, so five, ten, twenty ... minutes. Eight
#: attempts is about ten hours, past any afternoon a receiver spends down.
FIRST_RETRY = dt.timedelta(minutes=5)
MAX_ATTEMPTS = 8
#: How many rows one scheduler pass delivers before leaving the rest to the next.
BATCH = 50
#: What is claimed while a delivery is in flight, so a pass killed mid-request leaves a row
#: that comes back by itself rather than one stuck for ever (#221).
SENDING_LEASE = dt.timedelta(minutes=15)


def enqueue_for(user, url: str, notification: Notification) -> list[WebhookDelivery]:
    """A delivery row on every webhook connection of ``user`` configured with ``url``.

    The plugin's `send` is handed a configuration rather than a connection, and the row
    belongs to a connection: it is found by the address it was configured with, which is the
    one thing about it a person typed and the one thing that tells two apart.
    """
    from postulo.plugins.models import Connection

    rows = []
    connections = Connection.objects.for_user(user).enabled().of_kind("notifier")
    for connection in connections.filter(plugin=plugin.WebhookNotifier.name):
        if (connection.config.get("url") or "").strip() == url:
            row = enqueue(connection, notification)
            if row is not None:
                rows.append(row)
    return rows


def enqueue(connection, notification: Notification) -> WebhookDelivery | None:
    """A delivery row for this connection, unless its key has one already."""
    if (
        notification.key
        and WebhookDelivery.objects.filter(connection=connection, key=notification.key).exists()
    ):
        return None
    return WebhookDelivery.objects.create(
        owner=connection.owner,
        connection=connection,
        event=notification.event,
        key=notification.key,
        body=plugin.encode(plugin.payload_for(notification)),
        next_attempt_at=timezone.now(),
    )


def pending(now=None):
    now = now or timezone.now()
    return (
        WebhookDelivery.objects.filter(
            status__in=(DeliveryStatus.PENDING, DeliveryStatus.FAILED),
            attempts__lt=MAX_ATTEMPTS,
            next_attempt_at__lte=now,
        )
        # A connection that is switched off is not dialled, and its rows wait rather than
        # spending their attempts; switching it on resumes them (#243).
        .exclude(connection__enabled=False)
        .select_related("connection", "owner")
        .order_by("next_attempt_at", "pk")
    )


def claim(row, now=None) -> bool:
    """Take this row for sending, or say another pass already has (#221)."""
    now = now or timezone.now()
    return bool(
        WebhookDelivery.objects.filter(pk=row.pk, next_attempt_at=row.next_attempt_at).update(
            next_attempt_at=now + SENDING_LEASE
        )
    )


def deliver(row) -> bool:
    """One attempt at one row. Records the outcome on the row; never raises."""
    now = timezone.now()
    connection = row.connection
    row.attempts += 1
    row.last_attempt_at = now

    def fail(message: str, *, final: bool = False) -> bool:
        exhausted = row.attempts >= MAX_ATTEMPTS
        row.status = DeliveryStatus.GIVEN_UP if final or exhausted else DeliveryStatus.FAILED
        row.last_error = message[:500]
        row.next_attempt_at = (
            None if final or exhausted else now + FIRST_RETRY * (2 ** (row.attempts - 1))
        )
        row.save()
        return False

    if connection is None or not connection.enabled:
        return fail(str(_("The connection is gone or switched off.")), final=connection is None)
    url = (connection.config.get("url") or "").strip()
    secret = connection.secrets.get("secret") or ""
    try:
        # Checked again at each delivery, not only when the form was saved: a public
        # hostname can come to point somewhere private later.
        plugin.check_destination(url)
    except DestinationRefused as refused:
        return fail(str(refused), final=True)
    try:
        response = plugin.post(url, secret, row.body, event=row.event, delivery=str(row.pk))
    except Exception as error:
        logger.info("Webhook delivery %s to connection %s failed: %s", row.pk, connection.pk, error)
        connection.record_test(False, f"{type(error).__name__}: {error}")
        return fail(f"{type(error).__name__}: {error}")

    status = response.status_code
    if 200 <= status < 300:
        row.status = DeliveryStatus.SENT
        row.last_status = status
        row.last_error = ""
        row.next_attempt_at = None
        row.sent_at = now
        row.save()
        connection.record_test(True)
        return True
    answered = str(_("The receiver answered %(status)s.") % {"status": status})
    connection.record_test(False, answered)
    row.last_status = status
    # A client error will not fix itself by being retried; 408 and 429 are the two that
    # mean *later*, and are treated as the transient failures they are.
    return fail(answered, final=400 <= status < 500 and status not in (408, 429))


def send_pending(*, limit: int = BATCH) -> tuple[int, int]:
    """Deliver what is due. Returns (sent, failed). Called by the scheduler on every pass."""
    sent = failed = 0
    for row in list(pending()[:limit]):
        if not claim(row):
            continue
        if deliver(row):
            sent += 1
        else:
            failed += 1
    return sent, failed
