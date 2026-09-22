"""The built-in webhook notifier: every event, as signed JSON, to an address you name (#240).

Integrations had to poll. An automation -- n8n, Home Assistant, a script -- that wanted to
know an application had moved had to ask the API and diff, because nothing left Postulo on
its own except a reminder to a person. This is the notifier for a machine: it takes the same
`Notification` every other notifier takes and `POST`s it as JSON, signed, to a URL.

**Nothing is sent from inside a request.** `send` asks Postulo to keep a delivery row and
returns; the scheduler's pass delivers what is due, with backoff, and gives up after a fixed
number of tries. A receiver that is down for an afternoon gets everything when it comes
back, and a request that recorded a status change never waits on somebody else's server.
The rows are Postulo's (`postulo.notifications.webhooks`), for the reason the browser
notifier's notices are: a plugin holds no rows, and a delivery has to outlive the send that
left it, scoped to its owner by Postulo rather than by the plugin.

**Signed, so the receiver can tell Postulo from anyone who found the address.** The body is
signed with HMAC-SHA256 over the timestamp and the body, keyed by a secret the person set on
the connection and Postulo stores encrypted; the receiver recomputes it and refuses anything
older than a few minutes, so a captured request cannot be replayed later. `verify` is the
reference implementation and the wiki repeats it in three languages.

**The destination rules are the instance's.** The address goes through the same check every
connection's does: private and local addresses are refused unless the operator allowed them,
and the check runs again at each delivery, so a public hostname cannot come to point at the
router later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import ClassVar

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from postulo.plugins.api import (
    DestinationRefused,
    FieldSpec,
    Notification,
    TestResult,
    check_destination,
    client,
    declares,
    shipped,
)

SIGNATURE_HEADER = "Postulo-Signature"
EVENT_HEADER = "Postulo-Event"
DELIVERY_HEADER = "Postulo-Delivery"
#: A signature older than this is refused by `verify`: a captured request is not replayable
#: on Tuesday. Five minutes is generous to clock drift and stingy to an attacker.
TOLERANCE = dt.timedelta(minutes=5)
#: The shortest a secret may be. Sixteen characters of anything is past guessing; the form
#: says so rather than accepting `hunter2`.
MIN_SECRET = 16
#: How long one delivery may take, in seconds. A receiver is a machine and answers at once
#: or is down; waiting longer holds the scheduler's pass for nothing.
TIMEOUT = 10.0
#: The version of the payload's shape. A receiver checks it before reading the rest.
PAYLOAD_VERSION = 1


# ------------------------------------------------------------------- signing


def sign(secret: str, timestamp: int, body: str) -> str:
    """The hex HMAC-SHA256 of ``<timestamp>.<body>`` under ``secret``."""
    message = f"{timestamp}.{body}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signature_header(secret: str, timestamp: int, body: str) -> str:
    """``t=<unix seconds>,v1=<hex>`` -- the timestamp travels so the receiver can refuse old
    ones, and the scheme is named so a second one can be added beside it later."""
    return f"t={timestamp},v1={sign(secret, timestamp, body)}"


def verify(secret: str, header: str, body: str, *, now: float | None = None) -> bool:
    """What a receiver does. Constant-time on the digest, and refuses a stale timestamp."""
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs((now or time.time()) - timestamp) > TOLERANCE.total_seconds():
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, parts.get("v1", ""))


# ------------------------------------------------------------------- payload


def payload_for(notification: Notification) -> dict:
    """The notification as JSON: the human words, the machine fields, and never a document.

    ``data`` is the sender's own vocabulary -- ids and dates, as `Notification.data` has been
    since #229 -- so a receiver reads it with ``.get`` and works without it. Nothing here is
    ever a file or the text of one.
    """
    return {
        "version": PAYLOAD_VERSION,
        "event": notification.event,
        "key": notification.key,
        "occurred_at": (
            notification.occurred_at.isoformat() if notification.occurred_at is not None else None
        ),
        "language": notification.language,
        "title": notification.title,
        "body": notification.body,
        "url": notification.url,
        "data": dict(notification.data),
    }


def encode(payload: dict) -> str:
    """One canonical text for one payload, so the signature is over the bytes that are sent."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------- the plugin


@declares(
    shipped(
        name="webhook",
        label="Webhook",
        kind="notifier",
        description=_lazy(
            "Posts every event as signed JSON to an address you give, for an automation or a "
            "script to act on. Delivered by the scheduler, retried if the receiver is down."
        ),
    )
)
class WebhookNotifier:
    #: The events about what the person did are for a machine, and on for one (#240). A
    #: human notifier keeps them off: telling somebody they changed a status is telling them
    #: what they just did.
    event_defaults: ClassVar[dict[str, bool]] = {
        "status_changed": True,
        "interview_scheduled": True,
        "offer_recorded": True,
    }

    def config_fields(self, user=None) -> list[FieldSpec]:
        return [
            FieldSpec(
                "url",
                str(_("Receiver address")),
                help=str(
                    _(
                        "Where the JSON is posted. HTTPS, and a public address unless the "
                        "operator has allowed private ones."
                    )
                ),
            ),
            FieldSpec(
                "secret",
                str(_("Signing secret")),
                type="password",
                secret=True,
                help=str(
                    _(
                        "At least %(n)s characters. The receiver uses it to check that a request "
                        "came from Postulo; it is never sent."
                    )
                    % {"n": MIN_SECRET}
                ),
            ),
        ]

    def validate(self, config: dict) -> dict[str, list[str]]:
        problems: dict[str, list[str]] = {}
        url = (config.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            problems["url"] = [str(_("An address starting with https:// or http://."))]
        else:
            try:
                check_destination(url)
            except DestinationRefused as refused:
                problems["url"] = [str(refused)]
        if len(config.get("secret") or "") < MIN_SECRET:
            problems["secret"] = [
                str(_("At least %(n)s characters.") % {"n": MIN_SECRET}),
            ]
        return problems

    def send(self, notification: Notification, config: dict, user) -> None:
        """Queue it. The scheduler delivers; nothing leaves from inside a request."""
        from postulo.notifications import webhooks

        webhooks.enqueue_for(user, (config.get("url") or "").strip(), notification)

    def test(self, config: dict, user=None) -> TestResult:
        """One signed request, now, with a payload that says it is a test."""
        url = (config.get("url") or "").strip()
        secret = config.get("secret") or ""
        body = encode(
            {
                "version": PAYLOAD_VERSION,
                "event": "test",
                "key": "",
                "occurred_at": timezone.now().isoformat(),
                "language": "",
                "title": str(_("Postulo can reach this address.")),
                "body": "",
                "url": "",
                "data": {},
            }
        )
        try:
            check_destination(url)
            response = post(url, secret, body, event="test", delivery="test")
        except DestinationRefused as refused:
            return TestResult(False, str(refused))
        except Exception as error:
            return TestResult(False, f"{type(error).__name__}: {error}")
        if 200 <= response.status_code < 300:
            return TestResult(
                True, str(_("Answered %(status)s.") % {"status": response.status_code})
            )
        return TestResult(
            False, str(_("The receiver answered %(status)s.") % {"status": response.status_code})
        )


def post(url: str, secret: str, body: str, *, event: str, delivery: str):
    """One signed request, through the client that checks where it is dialling."""
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature_header(secret, timestamp, body),
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery,
    }
    with client(timeout=TIMEOUT) as http:
        return http.post(url, content=body.encode("utf-8"), headers=headers)
