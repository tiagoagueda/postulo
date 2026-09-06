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
    from logging.handlers import RotatingFileHandler

    settings.POSTULO_LOG_DIR = str(tmp_path)
    handler = RotatingFileHandler(tmp_path / "postulo.log", encoding="utf-8")
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
