"""The GeoNames download and the update check are bounded as they arrive (#782)."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest
from django.core.management.base import CommandError

from postulo.core import updates
from postulo.jobs.management.commands import fetch_geonames as command
from postulo.plugins import http


class OneAnswer:
    """A client whose one streamed answer is whatever the test hands it."""

    def __init__(self, response):
        self.response = response

    def stream(self, method, url, headers=None):
        import contextlib

        @contextlib.contextmanager
        def opened():
            yield self.response

        return opened()


def test_get_bytes_returns_a_small_body_and_a_status_for_the_rest():
    assert http.get_bytes(OneAnswer(httpx.Response(200, content=b"hello")), "u", limit=10) == (
        200,
        b"hello",
    )
    assert http.get_bytes(OneAnswer(httpx.Response(404, content=b"gone")), "u", limit=10) == (
        404,
        b"",
    )


def test_a_body_over_the_limit_is_refused_before_it_is_held():
    with pytest.raises(http.BodyTooLarge):
        http.get_bytes(OneAnswer(httpx.Response(200, content=b"x" * 11)), "u", limit=10)


def test_the_geonames_download_over_its_limit_is_a_refusal_the_command_words(monkeypatch):
    monkeypatch.setattr(command, "MAX_DOWNLOAD_BYTES", 10)
    answer = OneAnswer(httpx.Response(200, content=b"x" * 100))

    with pytest.raises(CommandError, match="refused"):
        command.Command()._fetch(answer, command.COUNTRIES_URL, "the country table")


def test_a_zip_that_would_inflate_past_the_limit_is_refused_without_inflating_it(monkeypatch):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cities1000.txt", "a" * 50_000)
    monkeypatch.setattr(command, "MAX_TABLE_BYTES", 1000)
    answer = OneAnswer(httpx.Response(200, content=out.getvalue()))

    with pytest.raises(CommandError, match="zip of one"):
        command.Command()._fetch(answer, command.CITIES_URL, "the city table")


def test_the_update_check_reads_a_small_feed_and_refuses_a_huge_one(monkeypatch):
    monkeypatch.setattr(
        updates,
        "public_only_client",
        lambda **kw: _Context(OneAnswer(httpx.Response(200, json={"tag_name": "v9.0.0"}))),
        raising=False,
    )
    import postulo.plugins.http as plugin_http

    monkeypatch.setattr(
        plugin_http,
        "public_only_client",
        lambda **kw: _Context(OneAnswer(httpx.Response(200, json={"tag_name": "v9.0.0"}))),
    )
    assert updates._fetch("https://example.org/releases/latest") == {"tag_name": "v9.0.0"}

    monkeypatch.setattr(updates, "MAX_ANSWER_BYTES", 5)
    with pytest.raises(http.BodyTooLarge):
        updates._fetch("https://example.org/releases/latest")


class _Context:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *excinfo):
        return False
