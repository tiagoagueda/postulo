"""Keeping what Postulo says about itself, and reading it back.

When something goes wrong on a self-hosted instance — a notifier that will not send, a
store that refuses a document, a capture that fails — the answer is in the log. Getting to
it meant ``docker logs`` and a shell, and the person administering a Postulo instance is
usually the person using it, often from a phone.

So records are kept as well as printed. The console handler is untouched, because
``docker logs`` is how an operator with a terminal expects to read them and taking that
away to add a page would be a poor trade. Beside it, a rotating file under the data volume,
capped by size and count so it cannot fill a disk.

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
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

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
#: a log line is one place a newline from a stranger must never land.
ACCEPTABLE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")

_current: contextvars.ContextVar[str] = contextvars.ContextVar("postulo_request_id", default="")


def current_request_id() -> str:
    """The id of the request, scheduler pass or errand this code is running for, or ``""``."""
    return _current.get()


def new_request_id(prefix: str = "") -> str:
    """Fresh, unguessable, and short enough to read aloud from a log line."""
    stamp = uuid.uuid4().hex
    return f"{prefix}-{stamp[:12]}" if prefix else stamp


def acceptable(identifier: str) -> bool:
    return bool(identifier) and ACCEPTABLE_ID.match(identifier) is not None


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
    rotations = sorted(path.parent.glob(f"{path.name}.*"), key=lambda p: p.name)
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


def _lines_newest_first(limit: int) -> Iterator[str]:
    """Read backwards from the end, so a large file costs what the page shows.

    Reading the whole thing to take the last hundred lines would work and would also mean
    an instance that has been running for a year cannot open its own log page.
    """
    for path in files():
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                block, buffer, position = 65536, b"", size
                while position > 0 and limit > 0:
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
                        limit -= 1
                        if limit <= 0:
                            return
                if limit > 0 and buffer.strip():
                    yield buffer.decode("utf-8", "replace")
                    limit -= 1
        except OSError:
            continue
        if limit <= 0:
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
