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

import datetime as dt
import json
import logging
import os
from collections.abc import Iterator
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
