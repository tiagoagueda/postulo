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

**Every other notifier's second chance waits here too (#1163).** A notifier that sends at
once -- mail, a push, Apprise -- was called once, and a send that failed was lost while a
webhook's was retried for ten hours. Its failed send is now a row in this same queue, already
one attempt old: the same schedule, the same lease, the same pass, the same week of being
kept, and the same end when its connection or its account goes. What a row holds is the
notification as a webhook is sent it, which is all of it, so the retry hands the notifier
the notification it was handed the first time. Which of the two a row is, is its
connection's plugin: there is no second table and no second pass to reason about.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.core import languages
from postulo.core.preferences import as_person
from postulo.plugins import webhook as plugin
from postulo.plugins.api import ConnectionUnusable, DestinationRefused, Notification
from postulo.plugins.policy import allows, refused_connections
from postulo.plugins.secrets import SecretsUnreadable

from .base import announcement_key
from .models import DeliveryStatus, WebhookDelivery

logger = logging.getLogger(__name__)

#: The retry schedule: doubled from the first, so five, ten, twenty ... minutes. Eight
#: attempts is about ten hours, past any afternoon a receiver spends down.
FIRST_RETRY = dt.timedelta(minutes=5)
MAX_ATTEMPTS = 8
#: How many rows one scheduler pass delivers before leaving the rest to the next.
BATCH = 50
#: How many seconds one scheduler pass may spend starting deliveries (#365). A pass that has
#: used it leaves the rest of the batch to the next pass rather than holding everybody's
#: reminders behind receivers that answer slowly.
BUDGET = 60.0
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
    return _row(connection, notification, next_attempt_at=timezone.now())


def retry_later(connection, notification: Notification, error: str) -> WebhookDelivery | None:
    """A row for a send that has just failed at a notifier, unless its key has one (#1163).

    The send that failed was the first attempt, and the row says so: it is where a webhook's
    row is after its first failure, so the next attempt is `FIRST_RETRY` away and there are
    as many left as a webhook would have.
    """
    now = timezone.now()
    return _row(
        connection,
        notification,
        status=DeliveryStatus.FAILED,
        attempts=1,
        last_attempt_at=now,
        last_error=error[:500],
        next_attempt_at=now + FIRST_RETRY,
    )


def _row(connection, notification: Notification, **state) -> WebhookDelivery | None:
    key = notification.key
    limit = WebhookDelivery._meta.get_field("key").max_length
    if len(key) > limit:
        # A later announcer must not be able to overflow the column, which PostgreSQL
        # enforces and SQLite does not (#413). Hashed whole, so it still names one thing.
        key = announcement_key(key[: limit - 50], key)
    waiting = WebhookDelivery.objects.filter(owner_id=connection.owner_id, connection=connection)
    if key and waiting.filter(key=key).exists():
        return None
    return WebhookDelivery.objects.create(
        owner_id=connection.owner_id,
        connection=connection,
        event=notification.event,
        key=key,
        body=plugin.encode(plugin.payload_for(notification)),
        **state,
    )


def notification_from(body: str) -> Notification:
    """The notification a row was queued with, read back from the text it is kept as.

    `payload_for` writes every field a `Notification` has, so this is the one a notifier was
    handed the first time: the same words in the same language, and the same key, which is
    what lets a notifier that keeps its own record tell this retry from a second event (#229).
    """
    payload = json.loads(body)
    occurred_at = payload.get("occurred_at")
    return Notification(
        event=payload["event"],
        title=payload["title"],
        body=payload.get("body") or "",
        url=payload.get("url") or "",
        key=payload.get("key") or "",
        language=payload.get("language") or "",
        occurred_at=dt.datetime.fromisoformat(occurred_at) if occurred_at else None,
        data=payload.get("data") or {},
    )


def pending(now=None):
    from postulo.plugins.models import Connection

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
        # Nor is one whose owner has been deactivated; reactivating resumes the rows (#575).
        .filter(owner__is_active=True)
        # Nor one whose plugin the policy has off for its owner (#362).
        .exclude(connection__in=refused_connections(Connection.objects.filter(kind="notifier")))
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

    def arrived(status: int | None = None) -> bool:
        row.status = DeliveryStatus.SENT
        row.last_status = status
        row.last_error = ""
        row.next_attempt_at = None
        row.sent_at = now
        # The text of a notification (the role, the company, a note, an offer's terms) has done
        # its job once the receiver has it, and is not kept for the row's whole life (#752).
        row.body = ""
        row.save()
        connection.record_test(True)
        return True

    if connection is not None and not allows(connection):
        # Waits, and spends nothing: reversing the decision resumes it (#362).
        return False
    if connection is None or not connection.enabled:
        return fail(str(_("The connection is gone or switched off.")), final=connection is None)
    if connection.plugin != plugin.WebhookNotifier.name:
        return _send_again(row, connection, fail, arrived)
    url = (connection.config.get("url") or "").strip()
    try:
        secret = connection.secrets.get("secret") or ""
    except SecretsUnreadable as unreadable:
        # A key rotated, or a backup restored onto another instance: no retry can read it,
        # so the row is given up on and the connection says why, as the copy pass does
        # (#573). Raised here it ended the whole scheduler pass, every time it came back.
        connection.record_test(False, str(unreadable))
        return fail(str(unreadable), final=True)
    try:
        # Checked again at each delivery, not only when the form was saved: a public
        # hostname can come to point somewhere private later.
        plugin.check_destination(url)
    except DestinationRefused as refused:
        # Said on the connection as well as the row: the row is given up on for good,
        # and the connection is the only place a person looks (#574).
        connection.record_test(False, str(refused))
        # A name that did not resolve is a resolver's bad moment, not a verdict (#549).
        return fail(str(refused), final=not refused.transient)
    try:
        response = plugin.post(url, secret, row.body, event=row.event, delivery=str(row.pk))
    except Exception as error:
        logger.info("Webhook delivery %s to connection %s failed: %s", row.pk, connection.pk, error)
        connection.record_test(False, f"{type(error).__name__}: {error}")
        return fail(f"{type(error).__name__}: {error}")

    status = response.status_code
    if 200 <= status < 300:
        return arrived(status)
    moved = plugin.redirect_message(response)
    if moved:
        connection.record_test(False, moved)
        row.last_status = status
        # Moved for good: the address has to be corrected, so retrying is pointless.
        return fail(moved, final=status in (301, 308))
    answered = str(_("The receiver answered %(status)s.") % {"status": status})
    connection.record_test(False, answered)
    row.last_status = status
    # A client error will not fix itself by being retried; 408 and 429 are the two that
    # mean *later*, and are treated as the transient failures they are.
    return fail(answered, final=400 <= status < 500 and status not in (408, 429))


def _send_again(row, connection, fail, arrived) -> bool:
    """One more attempt at a send that failed at its notifier (#1163).

    What `notifications.service` does for a first send, for one connection: the notifier is
    handed the notification it was handed then, in the language its words are in and the
    recipient's time zone. The outcome is written where the first one was, on the connection,
    and on the row, which is what decides whether there is another attempt.
    """
    notifier = connection.plugin_instance
    if notifier is None:
        # Not a verdict: a plugin missing during an upgrade is back before the attempts end.
        return fail(
            str(_("The %(plugin)s plugin is not installed.") % {"plugin": connection.plugin})
        )
    try:
        notification = notification_from(row.body)
    except (ValueError, KeyError, TypeError) as unreadable:
        # An event this version no longer has, or a row that is not one: no retry reads it.
        return fail(f"{type(unreadable).__name__}: {unreadable}", final=True)
    owner = row.owner
    try:
        # The recipient's time zone, and the language the words are already in: theirs, as
        # `notify` stamped it, and theirs again for a row that somehow names none.
        with as_person(owner), languages.override(notification.language):
            notifier.send(notification, connection.full_config, owner)
    except ConnectionUnusable as dead:
        # The other side has ended the connection since the first send: switched off with
        # the reason, as a first send does, and this row is not what brings it back (#216).
        logger.warning(
            "Notifier %r says connection %s is finished: %s", connection.plugin, connection.pk, dead
        )
        connection.retire(str(dead), keep_secrets=dead.keep_secrets)
        return fail(str(dead), final=True)
    except SecretsUnreadable as unreadable:
        # No retry can read them either, as for a webhook's secret (#573).
        connection.record_test(False, str(unreadable))
        return fail(str(unreadable), final=True)
    except Exception as error:
        logger.info(
            "Notifier %r failed again for connection %s: %s",
            connection.plugin,
            connection.pk,
            error,
        )
        connection.record_test(False, f"{type(error).__name__}: {error}")
        return fail(f"{type(error).__name__}: {error}")
    return arrived()


#: How long a delivered or given-up row is kept, for the connection's page to say what
#: happened lately; a notification's text is not worth more than that (#752).
KEEP_DAYS = 7


def forget_old(*, days: int = KEEP_DAYS) -> int:
    """Delete the rows whose story is over and a week old: sent, or given up. How many went."""
    cutoff = timezone.now() - dt.timedelta(days=days)
    done = WebhookDelivery.objects.filter(
        status__in=(DeliveryStatus.SENT, DeliveryStatus.GIVEN_UP)
    ).filter(
        models.Q(sent_at__lt=cutoff) | models.Q(sent_at__isnull=True, last_attempt_at__lt=cutoff)
    )
    return done.delete()[0]


def send_pending(*, limit: int = BATCH, budget: float = BUDGET) -> tuple[int, int]:
    """Deliver what is due. Returns (sent, failed). Called by the scheduler on every pass.

    With a ``budget`` in seconds, deliveries are started until that much time has gone and the
    rest wait for the next pass, as `run_syncs` does (#365). Each one is held to the plugin's
    TOTAL, so a pass spends at most the budget and one delivery more here, however many
    receivers answer slowly; before this, fifty slow receivers could hold every account's
    reminders for the whole of fifty deadlines. A delivery already started is never cut off.

    The sends that failed at another notifier are in the same list, each claimed the same
    way (#1163). One of those is held to its notifier's own deadline rather than to TOTAL,
    so the "one delivery more" is then that notifier's longest send.
    """
    started = time.monotonic()
    sent = failed = 0
    for row in list(pending()[:limit]):
        if budget and time.monotonic() - started >= budget:
            logger.info("Webhook budget of %ss spent; the rest wait for the next pass", budget)
            break
        if row.connection is not None and not allows(row.connection):
            # Asked before the claim, which would move the retry time a lease ahead: a row the
            # policy has off waits where it is, and resumes the moment it is switched on (#362).
            continue
        if not claim(row):
            continue
        try:
            delivered = deliver(row)
        except Exception:
            # One row's surprise is not the pass's: the rest are still due (#573).
            logger.exception("Webhook delivery %s could not be attempted", row.pk)
            failed += 1
            continue
        if delivered:
            sent += 1
        else:
            failed += 1
    return sent, failed
