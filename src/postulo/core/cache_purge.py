"""Expired cache entries, deleted once an hour (#1167).

Django's database cache and its file cache delete an expired entry in two cases only: when
that same key is asked for again, and when the store reaches ``MAX_ENTRIES``. Here that is
200,000, raised on purpose so that a rate-limit or lockout counter cannot be pushed out by
filling the cache (#755), and plenty of keys are never asked for again: the previous hour's
window of every allowance, the dashboard figures of a fingerprint that has since changed,
allauth's history of an address that never came back. They stayed until the 200,000th,
while every write counted the whole table (`DatabaseCache._base_set`) or listed the whole
directory (`FileBasedCache._cull`).

So what has expired is removed on a schedule, by the scheduler's pass and by the hourly
tidy a request does (`core.slow.tidy_up_if_due`) for the instance that runs no scheduler.

**Only what is already past its expiry.** A live counter is never touched, so nobody is
given their tries back, and what #755 protects is as it was.

**Only these two backends.** Redis and Memcached expire their own entries; pointed at one,
this does nothing at all, not even take its gate.

Django has no public method for either purge. The rows go with the statement
`DatabaseCache._cull` issues; the files are judged by `FileBasedCache._is_expired`, which
deletes the one it is handed when its time has passed. Private names, unchanged in Django
for over a decade, and used in preference to reading the pickled expiry a second way.
"""

from __future__ import annotations

import time

from django.core.cache import InvalidCacheBackendError, caches
from django.core.cache.backends.db import DatabaseCache
from django.core.cache.backends.filebased import FileBasedCache
from django.db import connections, router
from django.utils import timezone

#: How long one purge leaves the next caller alone.
PURGE_EVERY_SECONDS = 3600

#: The key, in the default cache, that says a purge was done within the hour.
GATE = "core:cache-purged"

#: Seconds one purge may spend deleting files before it leaves the rest to the next.
FILES_SECONDS = 5


def expired_rows(cache) -> int:
    """Delete the rows of a database cache that are past their expiry, in one statement.

    The comparison is the backend's own (`_cull`): the present moment without its
    microseconds, in whatever form this database keeps a date. A row that `get` would still
    answer with is later than that, and stays.
    """
    if not isinstance(cache, DatabaseCache):
        return 0
    entry = cache.cache_model_class
    connection = connections[router.db_for_write(entry)]
    quote = connection.ops.quote_name
    now = timezone.now().replace(microsecond=0)
    with connection.cursor() as cursor:
        # Both names are quoted by the database's own rule, and neither comes from a person:
        # the table is the one in the settings, the column is Django's.
        cursor.execute(
            f"DELETE FROM {quote(entry._meta.db_table)} WHERE {quote('expires')} < %s",  # noqa: S608
            [connection.ops.adapt_datetimefield_value(now)],
        )
        return max(cursor.rowcount, 0)


def expired_files(cache, *, seconds: float = FILES_SECONDS) -> int:
    """Delete the files of a file cache that are past their expiry. How many went.

    Each file is opened once and judged by the backend, which is what a `get` of its key
    would have done had anybody asked. A file that has gone since the directory was listed,
    that another process holds, or that is not a cache file at all, is left for next time.

    Held to ``seconds``, because one caller is a request: the directory an instance built
    up before this existed can hold tens of thousands of files, and deleting them is quick
    on most disks and slow on some. What is left goes in the hours that follow; whatever
    was deleted stays deleted, so each run starts where the last one stopped.
    """
    if not isinstance(cache, FileBasedCache):
        return 0
    gone = 0
    deadline = time.monotonic() + seconds
    for name in cache._list_cache_files():
        if time.monotonic() >= deadline:
            break
        try:
            with open(name, "rb") as held:
                gone += bool(cache._is_expired(held))
        except Exception:  # noqa: S112  one unreadable file is not a reason to keep the rest
            continue
    return gone


def _cache(alias: str):
    try:
        return caches[alias]
    except InvalidCacheBackendError:
        return None


def purge_if_due() -> int:
    """Remove what has expired from the two stores that never do, at most once an hour.

    The database cache when it is the default one, and the file cache when it is where the
    allowances are counted (`core.throttle`). Returns how many entries went.

    Cheap enough to call on every pass: with neither backend it returns at once, and with
    either the gate is one ``add``. Whoever loses the race for it, or finds the cache down,
    does nothing, as in `tidy_up_if_due`.
    """
    default, allowances = _cache("default"), _cache("ratelimit")
    if not isinstance(default, DatabaseCache) and not isinstance(allowances, FileBasedCache):
        return 0
    try:
        if not default.add(GATE, True, PURGE_EVERY_SECONDS):
            return 0
    except Exception:
        return 0
    return expired_rows(default) + expired_files(allowances)
