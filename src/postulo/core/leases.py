"""A lease: one row that says who is doing a thing, and until when (#1130).

Three pieces of work run once at a time whoever asks: a scheduler pass, a backup, and each
sync connection. Each used to hold a key in the cache, taken with ``cache.add``. On the
database cache that is a ``SELECT`` followed by an ``UPDATE`` that does not repeat what the
``SELECT`` found, so on PostgreSQL two processes arriving together over an *expired* key both
read it as expired, both wrote their token, and both ran. An expired key is what a killed
worker leaves behind, so that is exactly when it mattered.

Here every operation is one statement that carries its own condition, so the database decides
and it decides the same way on SQLite and on PostgreSQL:

* **take** inserts the row; where one is already there, it updates it *only if it has
  expired*, and holds the lease if and only if that changed one row. On PostgreSQL a second
  ``UPDATE`` waits for the first and then looks at the row the first one left, which has not
  expired, and changes nothing.
* **release** deletes the row *only if it still holds this token*: a run that outlived its
  lease leaves alone the one a later run took (#576, #988).
* **renew** moves the end *only if it still holds this token*, for work that knows it is
  taking long.

A table of Postulo's own rather than the cache also means the lease does not go with a cache
the operator flushes or points somewhere else.

A lease is a row like any other: taken inside a transaction, nobody else sees it until the
transaction ends. The callers that must be seen at once are therefore not atomic.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Lease


def _insert(key: str, token: str, expires: dt.datetime) -> bool:
    """Write the row for a key nobody holds. ``False`` when there is a row already."""
    try:
        # A savepoint, so that a caller's own transaction survives the refusal.
        with transaction.atomic():
            Lease.objects.create(key=key, token=token, expires=expires)
    except IntegrityError:
        return False
    return True


def _take_over(key: str, token: str, expires: dt.datetime, now: dt.datetime) -> bool:
    """Make an expired row this taker's. The condition is in the statement, which is the point."""
    return Lease.objects.filter(key=key, expires__lt=now).update(token=token, expires=expires) == 1


def take(key: str, seconds: int) -> str | None:
    """Take the lease for ``seconds``. The token to give it back with, or ``None`` if it is held."""
    now = timezone.now()
    expires = now + dt.timedelta(seconds=seconds)
    token = uuid.uuid4().hex
    if _insert(key, token, expires) or _take_over(key, token, expires, now):
        return token
    return None


def release(key: str, token: str) -> bool:
    """Give the lease back, if it is still this token's. Whether it was."""
    deleted, _ = Lease.objects.filter(key=key, token=token).delete()
    return deleted == 1


def renew(key: str, token: str, seconds: int) -> bool:
    """Hold the lease for ``seconds`` from now, if it is still this token's. Whether it was."""
    expires = timezone.now() + dt.timedelta(seconds=seconds)
    return Lease.objects.filter(key=key, token=token).update(expires=expires) == 1


def held(key: str) -> bool:
    """Whether somebody holds the lease now."""
    return Lease.objects.filter(key=key, expires__gte=timezone.now()).exists()
