"""The access log does not carry a one-time secret (#754)."""

from __future__ import annotations

from pathlib import Path

import pytest

from postulo.core.logredact import REDACTED, redact


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ('"GET /accounts/recover/Zx9-secret_Token/ HTTP/1.1" 302', "/accounts/recover/"),
        ('"GET /accounts/invitation/abc123def/ HTTP/1.1" 302', "/accounts/invitation/"),
        (
            '"GET /accounts/password/reset/key/1-dgawx6-3721/ HTTP/1.1" 200',
            "/accounts/password/reset/key/",
        ),
        ('"GET /accounts/confirm-email/MQ:1tXyz/ HTTP/1.1" 200', "/accounts/confirm-email/"),
    ],
)
def test_a_secret_in_the_path_is_replaced_and_the_kind_of_link_stays(line, kept):
    out = redact(line)

    assert kept + REDACTED in out
    assert "secret_Token" not in out and "abc123def" not in out and "3721" not in out


def test_the_consent_callback_loses_its_query():
    out = redact('"GET /settings/connections/consent/?code=4%2F0AbC&state=eyJ HTTP/1.1" 302')

    assert "4%2F0AbC" not in out and "eyJ" not in out
    assert "/settings/connections/consent/?" + REDACTED in out


def test_a_referer_that_carries_one_is_cleaned_by_the_same_rule():
    out = redact('"https://x.example/accounts/recover/abc/" "Mozilla/5.0"')

    assert "abc" not in out and "Mozilla/5.0" in out


def test_an_ordinary_line_is_untouched():
    line = '"GET /applications/12/?tab=notes HTTP/1.1" 200 5123'

    assert redact(line) == line
    assert redact("") == ""


def test_the_image_starts_gunicorn_with_the_redacting_logger():
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    assert '"--logger-class", "postulo.config.gunicorn_logging.RedactingLogger"' in dockerfile


def test_the_logger_applies_it_to_every_atom_that_can_hold_an_address():
    pytest.importorskip("gunicorn")
    from postulo.config.gunicorn_logging import RedactingLogger

    class Base:
        def atoms(self, *args):
            return {
                "r": "GET /accounts/recover/abc/ HTTP/1.1",
                "U": "/accounts/recover/abc/",
                "s": 302,
            }

    class Probe(RedactingLogger, Base):
        pass

    got = RedactingLogger.atoms(Probe.__new__(Probe), None, None, {}, 0)
    assert "abc" not in got["r"] and "abc" not in got["U"] and got["s"] == 302


@pytest.mark.parametrize(
    "path",
    [
        "/accounts/password/reset/key/1-dgawx6-3721abcdef/",
        "/accounts/confirm-email/MQ:1tXyzSECRETKEY/",
        "/accounts/recover/RECOVERYTOKEN/",
    ],
)
def test_the_json_log_loses_the_same_secrets_as_the_access_log(path):
    """A 500 on the one request that carries a key must not write the key (#979)."""
    import json
    import logging

    from django.test import RequestFactory

    from postulo.core.logs import JSONFormatter

    request = RequestFactory().get(path)
    record = logging.LogRecord(
        "django.request", logging.ERROR, __file__, 1, "Internal Server Error: %s", (path,), None
    )
    record.request = request

    written = JSONFormatter().format(record)
    secret = path.rstrip("/").rsplit("/", 1)[1]
    assert secret not in written
    document = json.loads(written)
    assert secret not in document["message"] and secret not in document["request"]
