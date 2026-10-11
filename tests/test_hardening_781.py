"""What a stranger's server answers cannot hold a worker or the scheduler (#781)."""

from __future__ import annotations

import socket
import threading
import time
from unittest import mock

import httpx
import pytest

from postulo.plugins import http, public_addresses
from postulo.plugins.webhook import TOTAL, post


def test_the_webhook_streams_the_answer_asks_for_nothing_compressed_and_has_a_total():
    seen = {}

    def handler(request):
        seen["encoding"] = request.headers.get("accept-encoding")
        return httpx.Response(200, content=b"x" * 10_000_000)

    def make_client(**kwargs):
        seen["total"] = kwargs.pop("total", None)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    with mock.patch("postulo.plugins.webhook.client", make_client):
        response = post("https://hooks.example.org/in", "s" * 20, "{}", event="x", delivery="1")

    assert response.status_code == 200
    assert seen == {"encoding": "identity", "total": TOTAL}
    assert response.is_closed


class Dribbler(threading.Thread):
    """A server that accepts, then sends one byte every so often, and never finishes."""

    def __init__(self):
        super().__init__(daemon=True)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]

    def run(self):
        connection, _address = self.listener.accept()
        try:
            for byte in b"HTTP/1.1 200 OK\r\n":
                connection.send(bytes([byte]))
                time.sleep(0.3)
        except OSError:
            pass
        finally:
            connection.close()
            self.listener.close()


def test_a_server_that_dribbles_its_answer_is_given_up_on_at_the_total(settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    server = Dribbler()
    server.start()
    started = time.monotonic()

    with pytest.raises(httpx.HTTPError):
        with http.client(timeout=5.0, total=1.0) as client:
            client.get(f"http://127.0.0.1:{server.port}/")

    assert time.monotonic() - started < 3
    server.join(timeout=10)


def test_a_name_that_never_resolves_is_given_up_on(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(public_addresses, "RESOLVE_SECONDS", 0.2)
    monkeypatch.setattr(
        public_addresses.socket, "getaddrinfo", lambda *args, **kwargs: release.wait(5)
    )
    started = time.monotonic()
    try:
        with pytest.raises(public_addresses.Unresolvable, match="too long"):
            public_addresses._addresses_for("slow.example.org")
    finally:
        release.set()

    assert time.monotonic() - started < 2


def test_every_client_a_plugin_is_handed_holds_its_waits_to_a_deadline():
    with http.client() as client:
        assert getattr(client._transport._pool._network_backend, "__class__", None).__name__ == (
            "_BoundedBackend"
        )
