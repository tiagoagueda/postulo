"""Outbound clients ignore the environment's proxy unless the operator says otherwise (#817)."""

from __future__ import annotations

from postulo.plugins import http


def test_a_proxy_in_the_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.internal:3128")
    with http.client() as client:
        assert not client._mounts, "no proxy transport was mounted"
        assert client._trust_env is False


def test_the_operator_may_accept_it(monkeypatch, settings):
    settings.POSTULO_OUTBOUND_USE_ENV_PROXY = True
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    with http.client() as client:
        assert client._trust_env is True


def test_the_public_only_client_ignores_it_too(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    with http.public_only_client() as client:
        assert client._trust_env is False
