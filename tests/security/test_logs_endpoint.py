"""The log served at /logs, which is personal data leaving the instance over HTTP.

Metrics can genuinely carry nothing about anybody. A log entry cannot: explaining that a
delivery failed means naming the connection, and often the company and the application. So
this endpoint is off, and when it is on it is behind a token, and it is in
`tests/security/` rather than beside the other log tests because that is what it is.
"""

from __future__ import annotations

import datetime as dt
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


def test_a_token_with_a_byte_above_ascii_is_refused_not_a_crash(client, kept, settings):
    """Headers arrive as Latin-1; `compare_digest` on such a str raised a 500 (#372)."""
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN

    response = client.get(reverse("core:logs_endpoint"), HTTP_AUTHORIZATION="Bearer café")

    assert response.status_code == 401


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


# ------------------------------------------------ a collector that pages (#475)
#
# `since` is how a collector asks only for what it has not seen. The view took the newest
# `limit` records and filtered by time afterwards, so when more than `limit` had been
# written since the last poll the collector was handed the newest of them, moved its mark
# past the rest, and never saw how the incident started.

BURST = dt.datetime(2026, 9, 7, 8, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def collecting(kept, settings):
    """The endpoint on, with its token."""
    settings.POSTULO_LOGS_ENDPOINT_ENABLED = True
    settings.POSTULO_LOGS_TOKEN = TOKEN
    return kept


def stamp(when: dt.datetime) -> str:
    """A time as the formatter writes one."""
    return when.isoformat(timespec="milliseconds")


def keep(directory, lines: list[tuple[dt.datetime, str, str]], name: str = "postulo.log") -> None:
    """Replace a file of the log with these ``(time, level, message)`` lines, in this order."""
    (directory / name).write_text(
        "".join(
            json.dumps(
                {"time": stamp(when), "level": level, "logger": "postulo.plugins", "message": said}
            )
            + "\n"
            for when, level, said in lines
        ),
        encoding="utf-8",
    )


def a_burst(count: int, first: int = 0) -> list[tuple[dt.datetime, str, str]]:
    return [
        (BURST + dt.timedelta(seconds=number), "ERROR", f"line {number}")
        for number in range(first, first + count)
    ]


def said(response) -> list[str]:
    return [record["message"] for record in records(response)]


def test_a_burst_is_handed_over_from_its_start(client, collecting):
    """Five hundred records since the last poll: the first two hundred, not the last."""
    keep(collecting, a_burst(500))

    first = fetch(client, TOKEN, since="2026-09-07T00:00:00+00:00")
    assert said(first) == [f"line {number}" for number in range(200)]

    second = fetch(client, TOKEN, since=records(first)[-1]["time"])
    assert said(second) == [f"line {number}" for number in range(200, 400)]

    third = fetch(client, TOKEN, since=records(second)[-1]["time"])
    assert said(third) == [f"line {number}" for number in range(400, 500)], "a short page: the end"

    assert said(fetch(client, TOKEN, since=records(third)[-1]["time"])) == []


def test_a_page_carries_on_across_a_rotation(client, collecting):
    keep(collecting, a_burst(250), name="postulo.log.1")
    keep(collecting, a_burst(250, first=250))

    first = fetch(client, TOKEN, since="2026-09-07T00:00:00+00:00")
    second = fetch(client, TOKEN, since=records(first)[-1]["time"])

    assert said(first) == [f"line {number}" for number in range(200)]
    assert said(second) == [f"line {number}" for number in range(200, 400)]


def test_since_is_an_instant_however_its_offset_is_written(client, collecting):
    """Half past twelve at +02:00 is half past ten: compared as text it selected nothing."""
    response = fetch(client, TOKEN, since="2026-09-06T12:30:00.000+02:00")

    assert said(response) == ["a delivery to Aperture failed"]


def test_a_time_with_no_offset_is_utc(client, collecting):
    response = fetch(client, TOKEN, since="2026-09-06T10:30:00")

    assert said(response) == ["a delivery to Aperture failed"]


def test_a_time_pasted_into_the_address_as_it_was_given_still_works(client, collecting):
    """The `time` of a record, put into the query unencoded: its `+` arrives as a space."""
    response = client.get(
        reverse("core:logs_endpoint") + "?since=2026-09-06T10:30:00.000+00:00",
        HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
    )

    assert response.status_code == 200
    assert said(response) == ["a delivery to Aperture failed"]


@pytest.mark.parametrize("nonsense", ["yesterday", "2026-13-45", "10:30 02:00"])
def test_something_that_is_not_a_time_is_refused_not_guessed_at(client, collecting, nonsense):
    """It used to select the wrong records and answer 200, which a collector files as right."""
    response = fetch(client, TOKEN, since=nonsense)

    assert response.status_code == 400
    assert "since" in response.json()["detail"]
    assert b"a delivery" not in response.content


def test_the_earliest_time_there_is_means_everything(client, collecting):
    response = fetch(client, TOKEN, since="0001-01-01")

    assert response.status_code == 200
    assert said(response) == ["a capture", "a delivery to Aperture failed"]


def test_a_page_never_ends_between_two_records_of_the_same_millisecond(client, collecting):
    """The collector asks for what is later than its last time, so the twin would be lost."""
    later = BURST + dt.timedelta(milliseconds=7)
    keep(
        collecting,
        [
            (BURST, "INFO", "before"),
            (later, "INFO", "one of three"),
            (later, "INFO", "two of three"),
            (later, "INFO", "three of three"),
            (later + dt.timedelta(seconds=1), "INFO", "after"),
        ],
    )

    first = fetch(client, TOKEN, since="2026-09-07T00:00:00+00:00", limit=2)
    second = fetch(client, TOKEN, since=records(first)[-1]["time"], limit=2)

    assert said(first) == ["before", "one of three", "two of three", "three of three"]
    assert said(second) == ["after"]


def test_a_level_and_a_time_together_give_the_oldest_that_match(client, collecting):
    keep(
        collecting,
        [
            (
                BURST + dt.timedelta(seconds=number),
                "ERROR" if number % 2 else "INFO",
                f"line {number}",
            )
            for number in range(40)
        ],
    )

    response = fetch(client, TOKEN, since="2026-09-07T00:00:00+00:00", level="ERROR", limit=5)

    assert said(response) == ["line 1", "line 3", "line 5", "line 7", "line 9"]


def test_a_record_written_a_little_out_of_order_is_not_what_ends_the_search(client, collecting):
    """Several processes write the one file, so it is nearly in time order and not strictly.

    The second line was stamped before the first and written after it. A search that
    stopped at the first record no later than `since` would stop there and miss the line
    above it.
    """
    keep(
        collecting,
        [
            (BURST + dt.timedelta(milliseconds=500), "INFO", "written first"),
            (BURST + dt.timedelta(milliseconds=200), "INFO", "stamped earlier, written second"),
            (BURST + dt.timedelta(seconds=1), "INFO", "written last"),
        ],
    )

    response = fetch(client, TOKEN, since=stamp(BURST + dt.timedelta(milliseconds=300)))

    assert said(response) == ["written first", "written last"]


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
