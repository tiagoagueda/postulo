"""Keeping what Postulo says about itself, and reading it back.

When something goes wrong on a self-hosted instance — a notifier that will not send, a
store that refuses a document, a capture that fails — the answer is in the log. Getting to
it meant ``docker logs`` and a shell, and the person administering a Postulo instance is
usually the person using it, often from a phone.

So records are kept as well as printed. The console handler is untouched, because
``docker logs`` is how an operator with a terminal expects to read them and taking that
away to add a page would be a poor trade. Beside it, a rotating file under the data volume,
capped by size and count so it cannot fill a disk.

**One file, written by every process.** Three gunicorn workers, the scheduler, the worker and
each `manage.py` the entrypoint runs all append to the same ``postulo.log``, so the handler
is `SharedRotatingFileHandler`: the standard one rotates on its own word, and one rotation
then became one per process (#379).

**One JSON object per line**, not a formatted sentence. A page can then filter by level and
by logger without parsing prose, the extras a record carried survive, and there is
something a collector can be handed as-is.

**What must never be in here.** A log is not a place for somebody's documents. The page is
for administrators, it says at the top that records may name people, companies and
applications, and nothing in Postulo writes a document's contents to a log.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

try:
    import fcntl
except ImportError:  # Windows, where Postulo is one process and there is no flock to take
    fcntl = None  # type: ignore[assignment]

#: Fields ``logging`` puts on every record. Anything else was added by the caller and is
#: worth keeping, which is most of the reason for writing JSON rather than a sentence.
STANDARD = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# ------------------------------------------------------------ the request id

#: The header a request may arrive with and every response leaves with (#233). A proxy
#: that sets one gets the same id back and in every line the request wrote, so its access
#: log, gunicorn's and Postulo's can be laid side by side.
REQUEST_ID_HEADER = "X-Request-ID"

#: What an id from outside may look like. Anything else is replaced rather than cleaned:
#: a log line is one place a newline from a stranger must never land. Matched whole:
#: `$` also matches before a final newline, and the id is echoed in a header and, since
#: #393, in the body of a refusal.
ACCEPTABLE_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")

_current: contextvars.ContextVar[str] = contextvars.ContextVar("postulo_request_id", default="")


def current_request_id() -> str:
    """The id of the request, scheduler pass or errand this code is running for, or ``""``."""
    return _current.get()


def new_request_id(prefix: str = "") -> str:
    """Fresh, unguessable, and short enough to read aloud from a log line."""
    stamp = uuid.uuid4().hex
    return f"{prefix}-{stamp[:12]}" if prefix else stamp


def acceptable(identifier: str) -> bool:
    return bool(identifier) and ACCEPTABLE_ID.fullmatch(identifier) is not None


@contextmanager
def request_scope(identifier: str | None = None) -> Iterator[str]:
    """Everything logged inside carries ``identifier``; a fresh one when none is given.

    A context variable rather than thread-local state, so the id follows a request across
    the async boundaries Django has and the sync ones the scheduler does not.
    """
    identifier = identifier or new_request_id()
    token = _current.set(identifier)
    try:
        yield identifier
    finally:
        _current.reset(token)


def install_record_factory() -> None:
    """Put the current id on every record ``logging`` makes, whichever handler formats it.

    Idempotent, because the settings module that calls this is imported more than once in a
    test run and a factory that wrapped itself would grow a stack of wrappers.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_postulo_request_id", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.request_id = _current.get()
        return record

    factory._postulo_request_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


class ConsoleFormatter(logging.Formatter):
    """The plain line, naming the request it belongs to when there is one."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        identifier = getattr(record, "request_id", "")
        return f"{line} [request {identifier}]" if identifier else line


class JSONFormatter(logging.Formatter):
    """One object per line: the time, the level, the logger, the message, the extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in STANDARD or key.startswith("_"):
                continue
            if key == "request_id" and not value:
                # Outside any request there is nothing to correlate, and a field that is
                # always present and usually empty is noise a reader learns to skip.
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


# ------------------------------------------------------------------- writing

#: Beside the log, and held only for the moment of a rotation.
LOCK_SUFFIX = ".lock"


class SharedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A size-rotated file that several processes write, rotated by one of them at a time.

    Every Postulo process logs to the same ``postulo.log``. The standard handler decides to
    rotate from the file *it* holds open and renames without asking anybody, which the
    logging cookbook says is not supported across processes, and this is why. Worker A
    rotates. B and C still hold the file A renamed, find it full at their next record, and
    rotate again, each in turn: the oldest generations fall off the end, the few lines A
    wrote into its new file become ``.1`` and then ``.2``, and A carries on writing into a
    file that is already a rotation. One real rotation left one full generation where
    ``POSTULO_LOG_BACKUPS`` promised three (#379).

    Two things put that right, and neither needs a process to know what the others are:

    - **Look at the file that is there.** Before judging its size, the handler lets go of
      its stream if the file at the path is no longer the one it holds, so what it measures
      is the live file and not a rotation somebody else made.
    - **Rotate one at a time.** The renaming happens under an exclusive lock on a file
      beside the log, and under it the handler looks again: a process that waited behind
      another's rotation finds the path already changed and has nothing left to do.

    The lock is never waited for. A log call must not block on another process, and if the
    lock is taken a rotation is under way, so the record is written to the stream in hand.
    At worst it lands in the file that has just become ``.1``, a few milliseconds out of
    place and kept.

    Neither of the tidier designs works here. A file per role still has gunicorn's three
    workers on one file. A single process that does all the rotating has to be one that is
    always running, and the scheduler is an optional profile.

    Where there is no ``flock`` -- Windows, which is a developer's machine and one process --
    or the lock file cannot be made, this is the standard handler and nothing else.
    """

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if fcntl is not None and self.stream is not None and self._moved():
            self._let_go()
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:
        if fcntl is None:
            super().doRollover()
            return
        try:
            lock = os.open(self.baseFilename + LOCK_SUFFIX, os.O_RDONLY | os.O_CREAT, 0o644)
        except OSError:
            # A directory that will not take one more file. Rotating unguarded is what
            # happened before, and is better than a log that stops rotating at all.
            super().doRollover()
            return
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            except OSError:
                # A filesystem with no locks to give. As above.
                pass
            if self.stream is not None and self._moved():
                # Rotated by somebody else between looking and locking.
                self._let_go()
                return
            super().doRollover()
        finally:
            # Opened for this one rotation and closed after it, which is also what gives
            # the lock back. A descriptor kept open would be inherited across a fork, and
            # a lock shared with one's own children excludes nobody.
            os.close(lock)

    def _moved(self) -> bool:
        """Whether the file at the path is no longer the one this handler holds open."""
        try:
            there = os.stat(self.baseFilename)
            here = os.fstat(self.stream.fileno())
        except OSError:
            return True
        return (there.st_dev, there.st_ino) != (here.st_dev, here.st_ino)

    def _let_go(self) -> None:
        """Close the stream; the next write opens whatever is at the path now."""
        stream, self.stream = self.stream, None
        try:
            stream.flush()
        finally:
            stream.close()


# ------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Record:
    """One line of the log, parsed. Anything unparseable still comes back as itself."""

    time: str
    level: str
    logger: str
    message: str
    extras: dict

    @property
    def when(self) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(self.time)
        except ValueError:
            return None


def directory() -> Path | None:
    configured = getattr(settings, "POSTULO_LOG_DIR", "")
    return Path(configured) if configured else None


def log_path() -> Path | None:
    place = directory()
    return place / "postulo.log" if place else None


def files() -> list[Path]:
    """The current file and its rotations, newest first."""
    path = log_path()
    if path is None or not path.parent.is_dir():
        return []
    prefix = f"{path.name}."
    numbered = []
    for candidate in path.parent.glob(f"{prefix}*"):
        number = candidate.name[len(prefix) :]
        # Only what the handler writes, `.1`, `.2` and so on, and in that order: by name
        # `.10` came before `.2`. The handler's lock file sits beside them, and so may
        # anything an operator left there (#379).
        if number.isascii() and number.isdigit():
            numbered.append((int(number), candidate))
    rotations = [candidate for _number, candidate in sorted(numbered)]
    return [p for p in [path, *rotations] if p.is_file()]


def _parse(line: str) -> Record | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except ValueError:
        # A line something else wrote, or one cut in half by a rotation. Keep it rather
        # than dropping it: a log that quietly discards what it cannot read is worse than
        # one with an odd line in it.
        return Record(time="", level="", logger="", message=line, extras={})
    if not isinstance(payload, dict):
        return Record(time="", level="", logger="", message=line, extras={})
    known = {"time", "level", "logger", "message"}
    return Record(
        time=str(payload.get("time", "")),
        level=str(payload.get("level", "")),
        logger=str(payload.get("logger", "")),
        message=str(payload.get("message", "")),
        extras={k: v for k, v in payload.items() if k not in known},
    )


def _lines_newest_first(limit: int | None = None) -> Iterator[str]:
    """Read backwards from the end, so a large file costs what the page shows.

    Reading the whole thing to take the last hundred lines would work and would also mean
    an instance that has been running for a year cannot open its own log page.

    ``limit`` is how many lines at most; with none, every line that is kept, for a reader
    that stops by itself once it has gone back far enough (`after`).
    """
    left: float = float("inf") if limit is None else limit
    for path in files():
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                block, buffer, position = 65536, b"", size
                while position > 0 and left > 0:
                    step = min(block, position)
                    position -= step
                    handle.seek(position)
                    buffer = handle.read(step) + buffer
                    pieces = buffer.split(b"\n")
                    buffer = pieces.pop(0)
                    for raw in reversed(pieces):
                        if not raw.strip():
                            continue
                        yield raw.decode("utf-8", "replace")
                        left -= 1
                        if left <= 0:
                            return
                if left > 0 and buffer.strip():
                    yield buffer.decode("utf-8", "replace")
                    left -= 1
        except OSError:
            continue
        if left <= 0:
            return


def read(*, limit: int = 200, level: str = "", logger: str = "", search: str = "") -> list[Record]:
    """The most recent records, newest first, narrowed by whatever was asked for."""
    wanted = LEVELS[LEVELS.index(level) :] if level in LEVELS else ()
    needle = search.strip().casefold()
    found: list[Record] = []
    # Read more than asked for, since filtering throws some away.
    for line in _lines_newest_first(limit * 20 if (wanted or logger or needle) else limit):
        record = _parse(line)
        if record is None:
            continue
        if wanted and record.level not in wanted:
            continue
        if logger and not record.logger.startswith(logger):
            continue
        if needle and needle not in f"{record.message} {record.logger}".casefold():
            continue
        found.append(record)
        if len(found) >= limit:
            break
    return found


#: How far behind the moment it was given `after` goes on looking. Several processes write
#: the one file, and a record is stamped before it is written, so the file is nearly in
#: the order of its times and not strictly: a line stamped a little earlier can sit after
#: one stamped later.
OUT_OF_ORDER = dt.timedelta(seconds=1)


def _instant(when: dt.datetime) -> dt.datetime:
    """A time with no offset is read as UTC, which is what the formatter writes."""
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def after(since: dt.datetime, *, limit: int = 200, level: str = "") -> list[Record]:
    """The oldest ``limit`` records later than ``since``, oldest first: a page of the log.

    For a reader that carries on from where it stopped. `read` hands back the newest
    records, and filtering those by time afterwards served a collector the *end* of
    whatever had happened since it last asked: with more than ``limit`` new records the
    older ones were skipped, the collector moved its mark past them, and they were never
    served. A burst is when a log matters and it was the start of the burst that went
    missing (#475).

    So this goes back as far as ``since`` however many lines that is, and keeps the oldest.
    Asking again with the last ``time`` received gets the next page, and a short page is
    the end.

    **A page never ends inside a millisecond.** Times are written to the millisecond and
    two records often share one. The reader asks for what is *later* than the last time it
    holds, so a page that stopped between two such records would lose the second; the ones
    sharing the page's last time are handed over with it, and a page may be longer than
    ``limit`` by that many.

    A line with no time in it, which is one something else wrote into the file, belongs
    to no moment and is left out. And one case no mark made of a time can cover: a record
    stamped earlier than one already served and written after that reader asked.
    """
    wanted = LEVELS[LEVELS.index(level) :] if level in LEVELS else ()
    since = _instant(since)
    try:
        floor = since - OUT_OF_ORDER
    except OverflowError:  # the first year there is: nothing is older than that anyway
        floor = since

    # Read newest first, so the newest is on the left and what falls off that end when the
    # page is full is what the reader will be given next time.
    page: deque[tuple[dt.datetime, Record]] = deque()
    twins: list[Record] = []
    with closing(_lines_newest_first()) as lines:
        for line in lines:
            record = _parse(line)
            when = record.when if record else None
            if record is None or when is None:
                continue
            when = _instant(when)
            if when <= floor:
                break
            if when <= since or (wanted and record.level not in wanted):
                continue
            page.append((when, record))
            if len(page) > limit:
                dropped_at, dropped = page.popleft()
                twins = [*twins, dropped] if dropped_at == page[0][0] else []
    return [record for _when, record in reversed(page)] + twins[::-1]


def loggers(sample: int = 2000) -> list[str]:
    """Which loggers have said anything lately, for the filter."""
    names = {record.logger for record in read(limit=sample) if record.logger}
    return sorted(names)


def size_on_disk() -> int:
    return sum(path.stat().st_size for path in files() if path.is_file())


def available() -> bool:
    """Whether anything is being kept at all."""
    place = directory()
    return bool(place) and place.is_dir()


def ensure_directory_at(place: str | Path) -> None:
    """Make the log directory before logging is configured. Never raises.

    Takes the path rather than reading it from settings, because it is called *from* the
    settings module while they are still being assembled. And it swallows the failure: a
    directory that cannot be made costs an administrator a page, and should not be what
    stops an instance from starting.
    """
    try:
        os.makedirs(place, exist_ok=True)
    except OSError:
        pass
