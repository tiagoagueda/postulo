"""The log served at /logs, which is personal data leaving the instance over HTTP.

Metrics can genuinely carry nothing about anybody. A log entry cannot: explaining that a
delivery failed means naming the connection, and often the company and the application. So
this endpoint is off, and when it is on it is behind a token, and it is in
`tests/security/` rather than beside the other log tests because that is what it is.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TOKEN = "a-long-random-collector-token"


@pytest.fixture
def kept(tmp_path, settings):
    """A log with something in it, and the endpoint switched off."""
    settings.POSTULO_LOG_DIR = str(tmp_path)
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = False
    settings.POSTULO_LOGS_TOKEN = ""
    lines = [
        {
            "time": "2026-09-06T10:00:00.000+00:00",
            "level": "INFO",
            "logger": "postulo.jobs",
            "message": "a capture",
        },
        {
            "time": "2026-09-06T11:00:00.000+00:00",
            "level": "ERROR",
            "logger": "postulo.plugins",
            "message": "a delivery to Aperture failed",
            "connection": "paperless",
        },
    ]
    (tmp_path / "postulo.log").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    return tmp_path


def fetch(client, token: str = "", **params):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.get(reverse("core:logs_endpoint"), params, **headers)


def records(response) -> list[dict]:
    return [json.loads(line) for line in response.content.decode().splitlines() if line.strip()]


# --------------------------------------------------------------------- off


def test_it_is_off_and_says_nothing_at_all(client, kept):
    """A 404, not a 403. A refusal confirms something is there; this confirms nothing."""
    response = fetch(client)

    assert response.status_code == 404
    assert b"a delivery" not in response.content


def test_being_off_holds_even_with_a_token(client, kept, settings):
    settings.POSTULO_LOGS_TOKEN = TOKEN
    assert fetch(client, TOKEN).status_code == 404


# -------------------------------------------------------------- on, and open


def test_on_without_a_token_refuses_to_serve(client, kept, settings, caplog):
    """The variable that matters was forgotten. Failing loudly beats publishing quietly."""
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = ""

    response = fetch(client)

    assert response.status_code == 503
    assert b"a delivery" not in response.content
    assert any("POSTULO_LOGS_TOKEN" in message for message in caplog.messages), (
        "and the operator is told, rather than finding out later"
    )


# ------------------------------------------------------------- on, with one


def test_the_wrong_token_gets_nothing(client, kept, settings):
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    for attempt in ("", "not-the-token", TOKEN[:-1]):
        response = fetch(client, attempt)
        assert response.status_code == 401, attempt
        assert b"a delivery" not in response.content


def test_a_session_is_not_a_substitute_for_the_token(client, kept, settings, admin_user):
    """The reader is a collector. Being signed in as an administrator is not the same thing."""
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN
    client.force_login(admin_user)

    assert fetch(client).status_code == 401


def test_the_right_token_gets_the_records_oldest_first(client, kept, settings):
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    response = fetch(client, TOKEN)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/x-ndjson")
    lines = records(response)
    assert [r["message"] for r in lines] == ["a capture", "a delivery to Aperture failed"], (
        "oldest first, which is the order a collector appends them in"
    )
    assert lines[1]["connection"] == "paperless", "the extras travel too"


def test_a_collector_can_ask_only_for_what_it_has_not_seen(client, kept, settings):
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    response = fetch(client, TOKEN, since="2026-09-06T10:30:00.000+00:00")

    assert [r["message"] for r in records(response)] == ["a delivery to Aperture failed"]


def test_a_level_narrows_it(client, kept, settings):
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    response = fetch(client, TOKEN, level="ERROR")

    assert [r["message"] for r in records(response)] == ["a delivery to Aperture failed"]


def test_one_request_cannot_ask_for_everything(client, kept, settings):
    """A collector polls. Handing it the whole file on request is a way to be knocked over."""
    from postulo.core import views_logs

    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    assert fetch(client, TOKEN, limit="99999999").status_code == 200
    assert views_logs.MAX_LIMIT <= 1000

    # Nonsense is a default rather than an error: a collector with a bad parameter should
    # still get its log.
    assert fetch(client, TOKEN, limit="lots").status_code == 200


def test_the_answer_is_never_cached(client, kept, settings):
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    response = fetch(client, TOKEN)
    assert "no-store" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------------ what the page says


def test_the_log_page_says_when_the_endpoint_is_open(client, kept, settings, admin_user):
    """Whether these records leave the instance is the most consequential fact about them."""
    from django.urls import reverse as url

    client.force_login(admin_user)

    html = client.get(url("server:logs")).content.decode()
    assert "data-logs-endpoint" not in html, "off: nothing to say"

    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN
    html = client.get(url("server:logs")).content.decode()
    assert 'data-logs-endpoint="on"' in html
    assert "/logs" in html

    settings.POSTULO_LOGS_TOKEN = ""
    html = client.get(url("server:logs")).content.decode()
    assert 'data-logs-endpoint="open"' in html
    assert "has no token" in html
