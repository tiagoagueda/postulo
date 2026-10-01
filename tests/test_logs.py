"""Keeping what Postulo says about itself, and reading it back from the interface.

Reading the log meant `docker logs` and a shell. The person administering a Postulo
instance is usually the person using it, and is quite often on a phone at the moment
something stops working.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.urls import reverse

from postulo.core import logs

pytestmark = pytest.mark.django_db


@pytest.fixture
def log_dir(tmp_path, settings):
    """A log directory of this test's own, so nothing leaks between them."""
    settings.POSTULO_LOG_DIR = str(tmp_path)
    return tmp_path


def write(directory, *records: dict) -> None:
    """Put lines in the file exactly as the handler would."""
    with (directory / "postulo.log").open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def a_record(**overrides) -> dict:
    return {
        "time": "2026-09-06T12:00:00.000+00:00",
        "level": "INFO",
        "logger": "postulo.plugins",
        "message": "Something happened",
        **overrides,
    }


# ------------------------------------------------------------- the formatter


def test_a_record_becomes_one_json_object_on_one_line():
    """A page can filter JSON without parsing prose, and a collector can take it as it is."""
    formatter = logs.JSONFormatter()
    record = logging.LogRecord(
        name="postulo.documents",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Could not send %s",
        args=("the letter",),
        exc_info=None,
    )
    written = formatter.format(record)

    assert "\n" not in written
    payload = json.loads(written)
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "postulo.documents"
    assert payload["message"] == "Could not send the letter"
    assert payload["time"].startswith("20")


def test_whatever_the_caller_attached_survives():
    """The point of JSON over a sentence: an extra is still there to be read."""
    formatter = logs.JSONFormatter()
    record = logging.LogRecord(
        name="postulo.plugins",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Delivery failed",
        args=(),
        exc_info=None,
    )
    record.connection = "paperless"
    record.attempt = 3

    payload = json.loads(formatter.format(record))
    assert payload["connection"] == "paperless"
    assert payload["attempt"] == 3


def test_something_that_will_not_serialise_is_kept_as_text_rather_than_lost():
    formatter = logs.JSONFormatter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hi",
        args=(),
        exc_info=None,
    )
    record.thing = object()

    payload = json.loads(formatter.format(record))
    assert "object object" in payload["thing"]


def test_a_traceback_travels_with_the_record():
    formatter = logs.JSONFormatter()
    try:
        raise ValueError("no")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="x",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="broke",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "ValueError: no" in payload["traceback"]


# --------------------------------------------------------------- reading back


def test_the_newest_records_come_back_first(log_dir):
    write(log_dir, a_record(message="first"), a_record(message="second"), a_record(message="third"))

    assert [r.message for r in logs.read(limit=10)] == ["third", "second", "first"]


def test_only_what_was_asked_for(log_dir):
    write(
        log_dir,
        a_record(level="INFO", logger="postulo.jobs", message="a capture"),
        a_record(level="ERROR", logger="postulo.plugins", message="a delivery"),
        a_record(level="WARNING", logger="postulo.plugins", message="a retry"),
    )

    assert [r.message for r in logs.read(level="WARNING")] == ["a retry", "a delivery"], (
        "a level means that one and everything worse, which is what somebody wants"
    )
    assert [r.message for r in logs.read(logger="postulo.jobs")] == ["a capture"]
    assert [r.message for r in logs.read(search="deliv")] == ["a delivery"]


def test_a_line_that_is_not_json_is_kept_rather_than_dropped(log_dir):
    """Something else wrote to the file, or a rotation cut a line in half."""
    (log_dir / "postulo.log").write_text(
        'not json at all\n{"level": "INFO", "message": "fine"}\n', encoding="utf-8"
    )

    messages = [r.message for r in logs.read()]
    assert "not json at all" in messages, "a log that discards what it cannot read is worse"
    assert "fine" in messages


def test_rotations_are_read_as_well(log_dir):
    write(log_dir, a_record(message="current"))
    (log_dir / "postulo.log.1").write_text(
        json.dumps(a_record(message="older")) + "\n", encoding="utf-8"
    )

    assert [r.message for r in logs.read(limit=10)] == ["current", "older"]


def test_a_large_file_is_read_from_the_end(log_dir):
    """An instance running for a year must still be able to open its own log page."""
    write(log_dir, *[a_record(message=f"line {n}") for n in range(5000)])

    found = logs.read(limit=5)
    assert [r.message for r in found] == [f"line {n}" for n in (4999, 4998, 4997, 4996, 4995)]


def test_nothing_is_kept_when_no_directory_is_configured(settings):
    settings.POSTULO_LOG_DIR = ""

    assert logs.available() is False
    assert logs.read() == []
    assert logs.files() == []


# ------------------------------------------------------------------ the page


def test_an_administrator_can_read_the_log(client, admin_user, log_dir):
    write(log_dir, a_record(message="a plugin would not load", level="ERROR"))

    client.force_login(admin_user)
    html = client.get(reverse("server:logs")).content.decode()

    assert "a plugin would not load" in html
    assert 'data-level="ERROR"' in html


def test_the_page_warns_what_a_log_can_contain_before_showing_any(client, admin_user, log_dir):
    """Read out or pasted into a bug report, these records name people and companies."""
    client.force_login(admin_user)
    html = client.get(reverse("server:logs")).content.decode()

    assert "can name people and their applications" in html
    assert html.index("can name people") < html.index("<table") if "<table" in html else True


def test_the_filters_narrow_what_the_page_shows(client, admin_user, log_dir):
    write(
        log_dir,
        a_record(level="INFO", message="ordinary"),
        a_record(level="ERROR", message="alarming"),
    )
    client.force_login(admin_user)

    html = client.get(reverse("server:logs"), {"level": "ERROR"}).content.decode()
    assert "alarming" in html
    assert "ordinary" not in html


def test_somebody_who_is_not_an_administrator_cannot_read_it(client, user, log_dir):
    write(log_dir, a_record(message="private"))
    client.force_login(user)

    response = client.get(reverse("server:logs"))
    assert response.status_code in (302, 403)
    assert b"private" not in response.content


def test_the_page_says_so_when_nothing_is_being_kept(client, admin_user, settings):
    settings.POSTULO_LOG_DIR = ""
    client.force_login(admin_user)

    html = client.get(reverse("server:logs")).content.decode()
    assert 'data-logs="off"' in html
    assert "POSTULO_LOG_DIR" in html


def test_the_section_is_in_the_server_settings_sidebar(client, admin_user):
    client.force_login(admin_user)
    html = client.get(reverse("server:overview")).content.decode()

    assert reverse("server:logs") in html


# ---------------------------------------------------------- the wiring itself


def test_what_a_logger_writes_is_readable_from_the_page(client, admin_user, settings, tmp_path):
    """End to end: the handler the settings configure, through the file, onto the page."""
    settings.POSTULO_LOG_DIR = str(tmp_path)
    handler = logs.SharedRotatingFileHandler(tmp_path / "postulo.log", encoding="utf-8")
    handler.setFormatter(logs.JSONFormatter())
    logger = logging.getLogger("postulo.tests.wiring")
    logger.addHandler(handler)
    try:
        logger.error("the store refused it", extra={"connection": "paperless"})
    finally:
        logger.removeHandler(handler)
        handler.close()

    client.force_login(admin_user)
    html = client.get(reverse("server:logs")).content.decode()

    assert "the store refused it" in html
    assert "paperless" in html, "and the extra it carried"


# ------------------------------------------- one file, written by every process
#
# Three gunicorn workers, the scheduler, the worker and each `manage.py` the entrypoint
# runs all append to the same `postulo.log`. The standard handler rotates on its own word,
# so one rotation became one per process and most of what was kept fell off the end (#379).

#: Where a file somebody still holds open can be renamed, and there is a lock to take.
needs_flock = pytest.mark.skipif(
    logs.fcntl is None, reason="no flock here, and an open file cannot be renamed"
)

A_MOMENT = 1_790_000_000.0


class Loud(logs.SharedRotatingFileHandler):
    """The handler, except that what goes wrong inside it fails the test.

    `logging` swallows an exception raised while writing a record and prints it instead,
    which is right for an application and would let every test here pass on a traceback.
    """

    def handleError(self, record):
        raise


def a_handler(path, *, limit: int = 2000, backups: int = 500) -> Loud:
    handler = Loud(path, maxBytes=limit, backupCount=backups, encoding="utf-8", delay=True)
    handler.setFormatter(logs.JSONFormatter())
    return handler


def say(handler, message: str, *, at: float = A_MOMENT) -> None:
    record = logging.LogRecord(
        "postulo.tests.shared", logging.INFO, __file__, 1, message, None, None
    )
    record.created = at
    handler.emit(record)


class NothingToWaitFor:
    """A lock nobody else holds, for a machine with no `fcntl` of its own."""

    LOCK_EX, LOCK_NB = 2, 4

    @staticmethod
    def flock(descriptor, how):
        return None


class SomebodyIsRotating(NothingToWaitFor):
    @staticmethod
    def flock(descriptor, how):
        raise BlockingIOError(11, "Resource temporarily unavailable")


def test_rotations_are_read_in_the_order_they_were_made(log_dir):
    """By name, `.10` came before `.2`: from ten backups on, yesterday was read before today."""
    write(log_dir, a_record(message="current"))
    for number in range(1, 12):
        (log_dir / f"postulo.log.{number}").write_text(
            json.dumps(a_record(message=f"rotation {number}")) + "\n", encoding="utf-8"
        )
    # Neither is a rotation: the handler's own lock, and something an operator left.
    (log_dir / "postulo.log.lock").write_text("", encoding="utf-8")
    (log_dir / "postulo.log.bak").write_text("not a record\n", encoding="utf-8")

    names = [path.name for path in logs.files()]
    said = [record.message for record in logs.read(limit=100)]

    assert names == ["postulo.log", *[f"postulo.log.{number}" for number in range(1, 12)]]
    assert said == ["current", *[f"rotation {number}" for number in range(1, 12)]]


def test_the_kept_log_is_written_by_the_handler_that_shares_it(settings):
    """No process may size-rotate, on its own word, a file that another one writes."""
    configured = settings.LOGGING["handlers"]["file"]["class"]
    # By the file it writes: pytest hangs a file handler of its own on the root logger.
    writing = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "baseFilename", "").endswith("postulo.log")
    ]

    assert configured == "postulo.core.logs.SharedRotatingFileHandler"
    assert writing, "nothing on the root logger writes the kept log"
    assert all(isinstance(handler, logs.SharedRotatingFileHandler) for handler in writing)


def test_one_process_still_rotates_and_keeps_everything(log_dir, monkeypatch):
    monkeypatch.setattr(logs, "fcntl", NothingToWaitFor)
    handler = a_handler(log_dir / "postulo.log")
    try:
        for number in range(120):
            say(handler, f"line {number:04d}", at=A_MOMENT + number)
    finally:
        handler.close()

    assert (log_dir / "postulo.log.1").is_file(), "nothing was rotated at all"
    assert [record.message for record in logs.read(limit=1000)] == [
        f"line {number:04d}" for number in reversed(range(120))
    ]


def test_a_rotation_already_under_way_is_not_waited_for(log_dir, monkeypatch):
    """A log call must not block on another process. The record is written where it is."""
    monkeypatch.setattr(logs, "fcntl", SomebodyIsRotating)
    handler = a_handler(log_dir / "postulo.log", limit=500)
    try:
        for number in range(40):
            say(handler, f"line {number:04d}")
    finally:
        handler.close()

    assert not (log_dir / "postulo.log.1").exists(), "it rotated without the lock"
    assert len(logs.read(limit=1000)) == 40


def test_a_lock_that_cannot_be_made_does_not_stop_the_log(log_dir, monkeypatch):
    """A directory that will not take one more file still gets its records, and rotates."""
    import os

    really_open = os.open

    def refusing(path, *args, **kwargs):
        if str(path).endswith(logs.LOCK_SUFFIX):
            raise PermissionError(13, "Permission denied", str(path))
        return really_open(path, *args, **kwargs)

    monkeypatch.setattr(logs, "fcntl", NothingToWaitFor)
    monkeypatch.setattr(logs.os, "open", refusing)
    handler = a_handler(log_dir / "postulo.log", limit=500)
    try:
        for number in range(40):
            say(handler, f"line {number:04d}", at=A_MOMENT + number)
    finally:
        handler.close()

    assert (log_dir / "postulo.log.1").is_file()
    assert len(logs.read(limit=1000)) == 40


@needs_flock
def test_two_processes_writing_one_file_lose_nothing(log_dir):
    """Two handlers stand in for two processes, writing turn about past the limit.

    With the standard handler the second one found the file it still held full, rotated
    again, and pushed out a rotation holding the few lines the first had just written.
    """
    one, two = a_handler(log_dir / "postulo.log"), a_handler(log_dir / "postulo.log")
    try:
        for number in range(400):
            say(one if number % 2 else two, f"line {number:04d}", at=A_MOMENT + number)
    finally:
        one.close()
        two.close()

    rotations = logs.files()[1:]
    assert len(rotations) > 5
    assert all(path.stat().st_size > 1000 for path in rotations), "a rotation went out half empty"
    assert [record.message for record in logs.read(limit=10_000)] == [
        f"line {number:04d}" for number in reversed(range(400))
    ], "every record, once, and still in the order it was written"


@needs_flock
def test_a_process_that_arrives_after_the_rotation_does_not_rotate_again(log_dir):
    one, two = (
        a_handler(log_dir / "postulo.log", limit=500),
        a_handler(log_dir / "postulo.log", limit=500),
    )
    try:
        say(two, "two holds the file open")
        while not (log_dir / "postulo.log.1").exists():
            say(one, "one fills it and rotates")

        two.doRollover()
        say(two, "written after it")
    finally:
        one.close()
        two.close()

    assert not (log_dir / "postulo.log.2").exists(), "one rotation became two"
    assert "written after it" in (log_dir / "postulo.log").read_text(encoding="utf-8")


@needs_flock
def test_a_lock_somebody_holds_is_respected(log_dir):
    import os

    holding = os.open(log_dir / f"postulo.log{logs.LOCK_SUFFIX}", os.O_RDONLY | os.O_CREAT, 0o644)
    logs.fcntl.flock(holding, logs.fcntl.LOCK_EX | logs.fcntl.LOCK_NB)
    handler = a_handler(log_dir / "postulo.log", limit=500)
    try:
        for number in range(40):
            say(handler, f"line {number:04d}")
    finally:
        handler.close()
        os.close(holding)

    assert not (log_dir / "postulo.log.1").exists()
    assert len(logs.read(limit=1000)) == 40


WRITER = """
import logging, sys, time
from pathlib import Path
from postulo.core import logs

path, name, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
handler = logs.SharedRotatingFileHandler(
    path, maxBytes=4000, backupCount=1000, encoding="utf-8", delay=True
)
handler.setFormatter(logs.JSONFormatter())
logger = logging.getLogger("postulo.tests.processes")
logger.propagate = False
logger.setLevel(logging.INFO)
logger.addHandler(handler)
while not go.exists():
    time.sleep(0.01)
for number in range(300):
    logger.info("%s line %04d", name, number)
handler.close()
"""


@needs_flock
def test_real_processes_writing_one_file_lose_nothing(log_dir, tmp_path_factory):
    """The same, with processes that are processes. Only what must hold whatever the
    scheduler does is asserted: every record is kept once, and no rotation was pushed out
    by a process that had not noticed the last one."""
    import subprocess
    import sys

    go = tmp_path_factory.mktemp("start") / "go"
    writers = [
        subprocess.Popen(  # noqa: S603 - this interpreter, and a script written here
            [sys.executable, "-c", WRITER, str(log_dir / "postulo.log"), name, str(go)],
            stderr=subprocess.PIPE,
        )
        for name in ("web-1", "web-2", "web-3", "scheduler")
    ]
    go.write_text("", encoding="utf-8")
    complaints = [writer.communicate(timeout=120)[1].decode() for writer in writers]

    assert [writer.returncode for writer in writers] == [0, 0, 0, 0], complaints
    assert not any("Logging error" in complaint for complaint in complaints), complaints
    said = sorted(record.message for record in logs.read(limit=10_000))
    assert said == sorted(
        f"{name} line {number:04d}"
        for name in ("web-1", "web-2", "web-3", "scheduler")
        for number in range(300)
    )
    rotations = logs.files()[1:]
    assert all(path.stat().st_size > 2000 for path in rotations), "a rotation went out half empty"


# ------------------------------------------------- what the log must not hold


def test_an_outbound_request_leaves_no_address_in_the_log(caplog, monkeypatch, settings):
    """httpx reports every request at INFO, with the whole address in it (#548).

    Those addresses are other people's: the posting somebody captured, a path on their own
    server, a webhook receiver whose address is the only secret it has. Staff read this log
    from Server settings and a collector is handed it, so the line must never be written.
    """
    import ipaddress

    import httpx

    from postulo.plugins import http

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    monkeypatch.setattr(
        http, "public_addresses_for", lambda url: [ipaddress.ip_address("93.184.216.34")]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    with caplog.at_level(logging.INFO), http.client(transport=transport) as client:
        client.post("https://ha.example/api/webhook/SECRETID?x=1")

    told = [
        record.getMessage()
        for record in caplog.records
        if record.levelno <= logging.INFO and "SECRETID" in record.getMessage()
    ]
    assert told == [], "the path of an outbound request reached the log"
