"""One id per request, in every line it logs and in the answer it gets (#233).

A gunicorn access line, a Postulo log line and a proxy's own record could not be laid
beside each other: nothing they shared said they were the same request.
"""

from __future__ import annotations

import json
import logging

import pytest

from postulo.core import logs

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ the scope


def test_outside_any_request_there_is_no_id():
    assert logs.current_request_id() == ""


def test_inside_a_scope_every_record_carries_the_id(caplog):
    """The record factory the settings install, so it holds for every handler."""
    with logs.request_scope("r-1"), caplog.at_level(logging.INFO, logger="postulo.tests.ids"):
        logging.getLogger("postulo.tests.ids").info("inside")
    logging.getLogger("postulo.tests.ids").info("outside")

    inside, outside = (r for r in caplog.records if r.name == "postulo.tests.ids")
    assert inside.request_id == "r-1"
    assert outside.request_id == ""


def test_scopes_nest_and_restore():
    with logs.request_scope("outer"):
        with logs.request_scope("inner"):
            assert logs.current_request_id() == "inner"
        assert logs.current_request_id() == "outer"
    assert logs.current_request_id() == ""


def test_a_scope_given_no_id_makes_one():
    with logs.request_scope() as identifier:
        assert len(identifier) == 32 and identifier == logs.current_request_id()


def test_a_prefixed_id_says_what_it_is_for():
    """A scheduler pass or an errand is not a request; its id says which it was."""
    identifier = logs.new_request_id("pass")
    assert identifier.startswith("pass-") and len(identifier) == len("pass-") + 12


def test_installing_the_factory_twice_wraps_once():
    before = logging.getLogRecordFactory()
    logs.install_record_factory()
    assert logging.getLogRecordFactory() is before, "the settings installed it already"


# ------------------------------------------------------------- the formatters


def a_record(message: str) -> logging.LogRecord:
    return logging.getLogRecordFactory()(
        "postulo.tests", logging.INFO, __file__, 1, message, (), None
    )


def test_the_console_line_names_the_request_only_when_there_is_one():
    formatter = logs.ConsoleFormatter("{levelname} {name}: {message}", style="{")

    with logs.request_scope("r-2"):
        assert formatter.format(a_record("in")) == "INFO postulo.tests: in [request r-2]"
    assert formatter.format(a_record("out")) == "INFO postulo.tests: out"


def test_the_json_line_carries_the_id_and_leaves_it_out_when_there_is_none():
    formatter = logs.JSONFormatter()

    with logs.request_scope("r-3"):
        assert json.loads(formatter.format(a_record("in")))["request_id"] == "r-3"
    assert "request_id" not in json.loads(formatter.format(a_record("out")))


def test_the_console_format_is_a_setting(settings):
    """`simple` for a person under `docker logs`; `json` for a collector that parses it."""
    assert settings.POSTULO_LOG_FORMAT == "simple"
    assert settings.LOGGING["handlers"]["console"]["formatter"] == "simple"
    assert settings.LOGGING["formatters"]["simple"]["()"] == "postulo.core.logs.ConsoleFormatter"
    assert "json" in settings.LOGGING["formatters"]


# ------------------------------------------------------------- the middleware


def test_a_request_is_given_an_id_and_told_it(client):
    response = client.get("/healthz")

    identifier = response.headers[logs.REQUEST_ID_HEADER]
    assert len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier)


def test_two_requests_get_two_ids(client):
    first = client.get("/healthz").headers[logs.REQUEST_ID_HEADER]
    second = client.get("/healthz").headers[logs.REQUEST_ID_HEADER]
    assert first != second


def test_an_id_the_caller_sent_comes_back_when_it_looks_like_one(client):
    """A proxy that sets one can lay its own log beside Postulo's."""
    response = client.get("/healthz", headers={"X-Request-ID": "proxy-7f3a.2026:09"})

    assert response.headers[logs.REQUEST_ID_HEADER] == "proxy-7f3a.2026:09"


@pytest.mark.parametrize(
    "given",
    ["has space", "x" * 201, "quote'd", "semi;colon", "<b>", "tab\there"],
)
def test_an_id_that_does_not_look_like_one_is_replaced_not_cleaned(client, given):
    """A log line is one place a stranger's newline or markup must not land."""
    response = client.get("/healthz", headers={"X-Request-ID": given})

    identifier = response.headers[logs.REQUEST_ID_HEADER]
    assert identifier != given and len(identifier) == 32


def test_the_middleware_is_second_in_the_chain(settings):
    """After the proxy check, before anything that could refuse and log the refusal."""
    chain = settings.MIDDLEWARE
    assert chain.index("postulo.core.middleware.RequestIDMiddleware") == 1
    assert chain[0] == "postulo.core.proxy.TrustedProxyMiddleware"


def test_the_view_can_read_the_id_off_the_request(rf):
    from postulo.core.middleware import RequestIDMiddleware

    seen = {}

    def view(request):
        seen["id"] = request.request_id
        seen["current"] = logs.current_request_id()
        from django.http import HttpResponse

        return HttpResponse("ok")

    response = RequestIDMiddleware(view)(rf.get("/", headers={"X-Request-ID": "given-1"}))

    assert seen == {"id": "given-1", "current": "given-1"}
    assert response[logs.REQUEST_ID_HEADER] == "given-1"
    assert logs.current_request_id() == "", "and the scope is closed with the request"
