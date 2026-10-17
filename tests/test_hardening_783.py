"""An old signed catalogue index cannot be served again (#783)."""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.cache import cache
from django.utils import timezone

from postulo.plugins import catalogue

pytestmark = pytest.mark.django_db


@pytest.fixture
def signer():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    return private, public


def index(private, **fields) -> tuple[bytes, str]:
    payload = json.dumps({"plugins": [], **fields}).encode()
    return payload, base64.b64encode(private.sign(payload)).decode()


@pytest.fixture
def serving(monkeypatch, signer):
    private, public = signer
    served = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *excinfo):
            return False

    monkeypatch.setattr(catalogue.http, "client", lambda **kw: Client())
    monkeypatch.setattr(
        catalogue,
        "configured",
        lambda: {"repo": {"url": "https://c.example/index.json", "key": public}},
    )
    monkeypatch.setattr(
        catalogue,
        "_get",
        lambda client, url, cap: served["sig"].encode() if url.endswith(".sig") else served["body"],
    )
    cache.clear()

    def serve(**fields):
        served["body"], served["sig"] = index(private, **fields)

    return serve


def test_an_index_with_no_counter_or_expiry_is_read_as_before(serving):
    serving()

    assert catalogue.fetch("repo").serial is None


def test_a_counter_that_goes_up_is_kept_and_an_older_index_is_refused(serving):
    serving(serial=10)
    assert catalogue.fetch("repo").serial == 10
    serving(serial=12)
    assert catalogue.fetch("repo").serial == 12

    serving(serial=11)
    with pytest.raises(catalogue.CatalogueError, match="older than one already seen"):
        catalogue.fetch("repo")
    serving(serial=12)
    assert catalogue.fetch("repo").serial == 12, "the same one again is not a rollback"


def test_the_counter_belongs_to_the_key_so_a_new_key_starts_again(serving, monkeypatch, signer):
    serving(serial=50)
    catalogue.fetch("repo")
    other = Ed25519PrivateKey.generate()
    other_public = base64.b64encode(
        other.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    monkeypatch.setattr(
        catalogue,
        "configured",
        lambda: {"repo": {"url": "https://c.example/index.json", "key": other_public}},
    )
    body, sig = index(other, serial=1)
    monkeypatch.setattr(
        catalogue,
        "_get",
        lambda client, url, cap: sig.encode() if url.endswith(".sig") else body,
    )

    assert catalogue.fetch("repo").serial == 1


def test_an_index_past_its_expiry_is_refused(serving):
    soon = (timezone.now() + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gone = (timezone.now() - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    serving(expires=soon)
    catalogue.fetch("repo")
    serving(expires=gone)
    with pytest.raises(catalogue.CatalogueError, match="expired"):
        catalogue.fetch("repo")


@pytest.mark.parametrize(
    "fields", [{"serial": "7"}, {"serial": -1}, {"serial": True}, {"expires": "soon"}]
)
def test_a_counter_or_expiry_that_is_not_one_is_refused(serving, fields):
    serving(**fields)

    with pytest.raises(catalogue.CatalogueError):
        catalogue.fetch("repo")


def test_the_counter_survives_a_cleared_cache(serving):
    """The memory is a row, not a cache entry a cull or a clear can take (#972)."""
    serving(serial=12)
    catalogue.fetch("repo")

    cache.clear()
    serving(serial=11)
    with pytest.raises(catalogue.CatalogueError, match="older than one already seen"):
        catalogue.fetch("repo")
    serving(serial=13)
    assert catalogue.fetch("repo").serial == 13
    cache.clear()
    serving(serial=12)
    with pytest.raises(catalogue.CatalogueError, match="older than one already seen"):
        catalogue.fetch("repo")
