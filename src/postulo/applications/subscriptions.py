"""Calendars somebody else keeps, read again by the scheduler and drawn read-only (#1098).

Importing a file (#661) takes what a calendar held once. An employment office's appointments,
a training provider's sessions and a calendar a career coach shares keep changing, so a
**subscription** is an address the scheduler reads again on an interval, with what it held
kept in a table of its own (`SubscribedEvent`) and replaced whole on every reading that
works. Nothing read here becomes an interview, an appointment or a reminder unless the person
turns one entry into one by hand (`views_subscriptions`).

**Every fetch is a guarded one.** The address is what a person typed, so it goes through the
connections' client (`plugins.http.client`): private and local addresses are refused unless
the operator set ``POSTULO_CONNECTIONS_ALLOW_PRIVATE``, every redirect is checked again, the
body is held to the importer's size (`ical_reader.MAX_BYTES`) and the whole download to
`SECONDS`. The text is read by `ical_reader`, the importer's own reader, with the same bounds.

**Once per interval, and less while it fails.** A pass reads the subscriptions whose interval
has come round, claimed first so two schedulers do not both read one, and stops starting new
ones once `BUDGET` seconds are spent. One that fails waits as a failing sync does (#1162): the
time it has been failing, where that is longer than its interval, and never more than a day;
its card says so, and when it will be tried again.

**The address is a secret.** It is never logged and never put in an error: a log line names
the subscription by its number, and what the card says is written here, not taken from the
exception.

**What is left out is said.** A recurring event (the reader does not expand recurrences), a
time with no zone Postulo can use (nobody is asked, so it is not guessed) and a task are not
drawn, and the card counts them.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import httpx
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from postulo.core import preferences
from postulo.plugins import http

from . import ical_reader
from .models import CalendarSubscription, SubscribedEvent

logger = logging.getLogger(__name__)

#: Each wait on the network, in seconds.
TIMEOUT = 10.0
#: The whole of one download, redirects and body included.
SECONDS = 30.0
#: How many events and tasks one calendar may hold. `ical_reader.MAX_COMPONENTS` bounds it too.
MAX_ENTRIES = ical_reader.MAX_COMPONENTS
#: How many seconds one scheduler pass may spend starting readings, as the copies do (#1172).
BUDGET = 60.0
#: The longest a failing subscription waits between two attempts: the longest interval.
LONGEST_WAIT = dt.timedelta(days=1)

#: The module the subscriptions belong to, as the import and the feeds do (#877).
ICALENDAR = "format-icalendar"

#: Why entries were left out of the last reading, as `CalendarSubscription.left_out` keys them.
REPEATS = "repeats"
NO_ZONE = "no_zone"
TASKS = "tasks"
UNREADABLE = "unreadable"


class Unreadable(Exception):
    """The calendar could not be read; the message is for the person, and names no address."""


# ------------------------------------------------------------------ the address


def normalise(address: str) -> str:
    """The address to fetch, from what was typed: ``webcal://`` is ``https://``.

    Raises `ValueError`, with a sentence for the person, for anything that is not a web
    address. Whether it may be reached is the destination policy's question, asked by
    `check`.
    """
    address = (address or "").strip()
    lowered = address.lower()
    for scheme in ("webcals://", "webcal://"):
        if lowered.startswith(scheme):
            address = "https://" + address[len(scheme) :]
            lowered = address.lower()
    if not lowered.startswith(("https://", "http://")) or len(address) <= len("https://"):
        raise ValueError(
            _("Enter the calendar's web address, starting with https:// or webcal://.")
        )
    return address


def check(address: str) -> None:
    """Refuse an address the instance's policy will not let it reach, before it is kept.

    Raises `ValueError` with the policy's own sentence. A name that does not resolve now is
    let through: it says nothing about the address, and the scheduler will say so on the card.
    """
    try:
        http.check_destination(address)
    except http.DestinationRefused as refused:
        if not refused.transient:
            raise ValueError(str(refused)) from None


# ------------------------------------------------------------------ reading it


def fetch(address: str) -> bytes:
    """The calendar's text, or `Unreadable` saying why in words that name no address."""
    try:
        with http.client(timeout=TIMEOUT) as client:
            status, body = http.get_bytes(
                client,
                address,
                limit=ical_reader.MAX_BYTES,
                seconds=SECONDS,
                headers={"Accept": "text/calendar, */*;q=0.5"},
            )
    except http.DestinationRefused as refused:
        raise Unreadable(str(refused)) from None
    except http.BodyTooLarge:
        raise Unreadable(
            _("The calendar is larger than %(limit)s MB, so it was not read.")
            % {"limit": ical_reader.MAX_BYTES // (1024 * 1024)}
        ) from None
    except (http.BodyTooSlow, httpx.TimeoutException):
        raise Unreadable(_("The calendar's server took too long to answer.")) from None
    except http.BodyRefused:
        raise Unreadable(
            _("The calendar's server sent something that could not be read.")
        ) from None
    except httpx.InvalidURL:
        raise Unreadable(_("That address is not one a calendar can be read from.")) from None
    except httpx.TooManyRedirects:
        raise Unreadable(
            _("The calendar's server sent Postulo round too many redirects.")
        ) from None
    except (httpx.HTTPError, OSError):
        raise Unreadable(_("The calendar's server could not be reached.")) from None
    if status in (401, 403):
        raise Unreadable(
            _("The calendar's server refused the address. It may have been changed or withdrawn.")
        )
    if status in (404, 410):
        raise Unreadable(_("The calendar's server has nothing at that address any more."))
    if status != 200:
        raise Unreadable(
            _("The calendar's server answered with an error (%(status)s).") % {"status": status}
        )
    return body


def events_from(subscription: CalendarSubscription, entries) -> tuple[list, dict]:
    """The rows to keep for ``entries``, and how many were left out for each reason.

    Run in the owner's zone (`refresh` puts it in force): a whole day's midnight is theirs.
    """
    rows: list[SubscribedEvent] = []
    left: dict[str, int] = {}

    def leave(reason: str) -> None:
        left[reason] = left.get(reason, 0) + 1

    for entry in entries:
        if entry.kind != ical_reader.EVENT:
            leave(TASKS)
            continue
        if entry.refusal == ical_reader.RECURS:
            leave(REPEATS)
            continue
        if entry.refusal:
            leave(UNREADABLE)
            continue
        if entry.needs_zone:
            leave(NO_ZONE)
            continue
        row = SubscribedEvent(
            subscription=subscription,
            uid=entry.uid[:255],
            summary=entry.summary,
            location=entry.location,
            description=entry.description,
            cancelled=entry.cancelled,
        )
        if entry.day is not None:
            row.day = entry.day
            row.starts_at = timezone.make_aware(dt.datetime.combine(entry.day, dt.time.min))
        else:
            row.starts_at = entry.start
            row.ends_at = entry.end if entry.end is not None and entry.end > entry.start else None
        rows.append(row)
    return rows, left


def refresh(subscription: CalendarSubscription) -> bool:
    """Read one subscription now and keep what it holds. Never raises; true when it worked.

    What it held before stays until a reading works: a server that is down for an hour does
    not empty somebody's calendar. Run as its owner, in their language and zone, whoever
    started it, as a sync is (#335).
    """
    now = timezone.now()
    with preferences.as_person(subscription.owner):
        try:
            address = subscription.address
            if not address:
                raise Unreadable(_("The address cannot be read here any more. Enter it again."))
            reading = ical_reader.read(fetch(address), limit=MAX_ENTRIES)
            rows, left = events_from(subscription, reading.entries)
        except Unreadable as error:
            return _failed(subscription, now, str(error))
        except ical_reader.Refused as refused:
            return _failed(subscription, now, str(refused))
        except Exception as error:
            # The subscription's number and the kind of failure, and nothing else: a message
            # or a traceback may quote the address, which is a secret (see the module).
            logger.error(
                "Calendar subscription %s could not be read: %s",
                subscription.pk,
                type(error).__name__,
            )
            return _failed(subscription, now, _("The calendar could not be read."))
        with transaction.atomic():
            SubscribedEvent.objects.filter(subscription=subscription).delete()
            SubscribedEvent.objects.bulk_create(rows)
            subscription.fetched_at = now
            subscription.last_ok_at = now
            subscription.last_error = ""
            subscription.left_out = left
            subscription.save(
                update_fields=["fetched_at", "last_ok_at", "last_error", "left_out", "updated_at"]
            )
    return True


def _failed(subscription: CalendarSubscription, now, error: str) -> bool:
    logger.info("Calendar subscription %s could not be read this time", subscription.pk)
    subscription.fetched_at = now
    subscription.last_error = str(error)[:500]
    subscription.save(update_fields=["fetched_at", "last_error", "updated_at"])
    return False


# ------------------------------------------------------------------ when


def wait_after_last_read(subscription: CalendarSubscription) -> dt.timedelta:
    """How long the scheduler leaves a subscription alone after it was last read.

    Its interval while it works. While it fails, the time it has been failing, where that is
    longer, and never more than a day -- the sync connections' rule (#1162): counting from the
    last reading that worked, or from when it was added, doubles the wait at each failure with
    no counter to keep, and the first reading that works clears it.
    """
    interval = dt.timedelta(minutes=subscription.interval or 60)
    if not subscription.last_error or subscription.fetched_at is None:
        return interval
    since = subscription.last_ok_at or subscription.created_at
    if since is None:
        return interval
    return min(max(interval, subscription.fetched_at - since), LONGEST_WAIT)


def is_due(subscription: CalendarSubscription, now=None) -> bool:
    if subscription.fetched_at is None:
        return True
    return (now or timezone.now()) - subscription.fetched_at >= wait_after_last_read(subscription)


def next_attempt(subscription: CalendarSubscription, now=None):
    """When a failing subscription will next be read, for its card; else ``None``."""
    if not (subscription.last_error and subscription.fetched_at):
        return None
    when = subscription.fetched_at + wait_after_last_read(subscription)
    return when if when > (now or timezone.now()) else None


def claim(subscription: CalendarSubscription) -> bool:
    """Take a subscription's turn before reading it, or say another run already has.

    The stamp is written with a conditional ``UPDATE``, as a sync connection's is (#576):
    whichever run changes the row owns the turn. It is replaced when the reading ends; if the
    run dies, the subscription waits one interval, not for ever.
    """
    return bool(
        CalendarSubscription.objects.filter(
            pk=subscription.pk,
            owner_id=subscription.owner_id,
            fetched_at=subscription.fetched_at,
        ).update(fetched_at=timezone.now())
    )


def due(now=None) -> list[CalendarSubscription]:
    """Every subscription whose turn has come, across accounts: the scheduler's work."""
    from postulo.core import modules

    now = now or timezone.now()
    return [
        subscription
        # Every account's, on purpose: this is the scheduler's pass (unscoped_queries.txt).
        for subscription in CalendarSubscription.objects.filter(
            owner__is_active=True
        ).select_related("owner")
        if is_due(subscription, now) and modules.is_on(subscription.owner, ICALENDAR)
    ]


def refresh_due(*, budget: float = BUDGET) -> tuple[int, int]:
    """Read every subscription that is due. Returns (read, failed). Called on every pass.

    Readings are started until ``budget`` seconds have gone and the rest wait for the next
    pass; one already started is never cut off, and `SECONDS` bounds it.
    """
    started = time.monotonic()
    read = failed = 0
    for subscription in due():
        if budget and time.monotonic() - started >= budget:
            logger.info("Subscription budget of %ss spent; the rest wait", budget)
            break
        if not claim(subscription):
            continue
        read += 1
        if not refresh(subscription):
            failed += 1
    return read, failed


# ------------------------------------------------------------------ what the card says


def left_out_lines(subscription: CalendarSubscription) -> list[str]:
    """What the last reading did not draw, and why, in the language in force."""
    left = subscription.left_out if isinstance(subscription.left_out, dict) else {}
    lines = []
    words = {
        REPEATS: lambda n: ngettext(
            "%(count)s repeating event: Postulo does not read repeating events.",
            "%(count)s repeating events: Postulo does not read repeating events.",
            n,
        ),
        NO_ZONE: lambda n: ngettext(
            "%(count)s event with a time but no time zone Postulo knows.",
            "%(count)s events with a time but no time zone Postulo knows.",
            n,
        ),
        TASKS: lambda n: ngettext("%(count)s task.", "%(count)s tasks.", n),
        UNREADABLE: lambda n: ngettext(
            "%(count)s event whose date could not be read.",
            "%(count)s events whose date could not be read.",
            n,
        ),
    }
    for reason in (REPEATS, NO_ZONE, TASKS, UNREADABLE):
        count = left.get(reason)
        if isinstance(count, int) and count > 0:
            lines.append(words[reason](count) % {"count": count})
    return lines
