"""The two small things a scheduler needs that nothing else does: a lease, and a heartbeat.

Postulo's scheduler is one command in a loop. That is the right size for a single instance,
but it leaves two questions that a queue would have answered by itself: what stops two of
them running at once, and how does anybody know it is still going round (#221).

**The lease** is a cache key held for the length of one pass. It is a courtesy rather than a
lock -- every step of a pass also claims its own rows before acting, which is what actually
makes a second scheduler harmless -- and it exists so that the ordinary mistake, cron *and*
``--loop`` at the same time (the Compose file offers both), costs nothing instead of doubling
the work. A pass that dies holds the key until it expires, which is why it expires.

**The heartbeat** is a file, not a cache key or a row, because of who has to read it. The
scheduler's own container healthcheck has no database credentials and serves no port, and the
metrics endpoint that reports it runs in a *different* container. A file on the shared data
volume is the one thing both can see, and its modification time is the whole answer, so a
healthcheck can be a `test` on the file and nothing more.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

#: The cache key one pass holds while it runs.
LEASE_KEY = "postulo:scheduler:pass"


def heartbeat_path() -> Path:
    return Path(settings.POSTULO_SCHEDULER_HEARTBEAT)


def beat(when=None) -> None:
    """Record that a pass has just finished.

    Written to a temporary file and moved into place, so a reader never sees half a line --
    a healthcheck runs on its own clock and will sooner or later read this mid-write.
    """
    when = when or timezone.now()
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".heartbeat-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as writing:
            writing.write(when.isoformat())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def last_pass() -> dt.datetime | None:
    """When the scheduler last finished a pass, or ``None`` if it never has here.

    The file's contents are preferred and its modification time is the fallback: a volume
    restored from a backup can carry a file whose text is right and whose timestamp is not.
    """
    path = heartbeat_path()
    try:
        written = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return dt.datetime.fromisoformat(written)
    except ValueError:
        pass
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
    except OSError:
        return None


@contextmanager
def only_one_pass(seconds: int):
    """Hold the pass lease, or yield ``False`` because somebody else has it."""
    got_it = cache.add(LEASE_KEY, timezone.now().isoformat(), max(seconds, 60))
    try:
        yield bool(got_it)
    finally:
        if got_it:
            cache.delete(LEASE_KEY)
