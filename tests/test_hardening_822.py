"""base-uri is none, and a policy violation is logged, never stored (#822)."""

from __future__ import annotations

import json
import logging

import pytest
from django.urls import reverse

from postulo.config.settings import base

pytestmark = pytest.mark.django_db


def test_no_page_may_set_a_base_element():
    assert base.SECURE_CSP["base-uri"] == ["'none'"]
    assert base.SECURE_CSP["report-uri"] == ["/csp-report/"]


def report(client, body, **extra):
    return client.post(
        reverse("core:csp_report"),
        data=json.dumps(body),
        content_type="application/csp-report",
        **extra,
    )


def test_a_report_is_one_log_line_and_the_address_is_not_in_it(client, caplog):
    with caplog.at_level(logging.WARNING, logger="postulo.core.views"):
        response = report(
            client,
            {
                "csp-report": {
                    "effective-directive": "img-src",
                    "blocked-uri": "https://tracker.example/pixel.gif?secret=1",
                }
            },
        )
    assert response.status_code == 204
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "img-src" in text
    assert "tracker.example" not in text and "secret" not in text


def test_a_report_needs_no_csrf_token_and_no_session(client):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    assert report(strict, {"csp-report": {}}).status_code == 204


def test_nonsense_is_a_204_too_and_a_get_is_405(client):
    assert (
        client.post(
            reverse("core:csp_report"), data="not json", content_type="text/plain"
        ).status_code
        == 204
    )
    assert client.get(reverse("core:csp_report")).status_code == 405


def test_a_huge_body_is_read_only_so_far(client):
    huge = {"csp-report": {"effective-directive": "script-src", "blocked-uri": "x" * 100_000}}
    assert report(client, huge).status_code == 204


def test_an_address_that_floods_is_still_answered_quietly(client):
    codes = {
        report(client, {"csp-report": {}}, REMOTE_ADDR="203.0.113.77").status_code
        for _ in range(40)
    }
    assert codes == {204}
