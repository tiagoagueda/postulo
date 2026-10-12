"""A stranger cannot choose the id a request is logged under (#798)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

CHOSEN = "borrowed-id-1234"


def test_an_id_from_a_stranger_is_replaced(client):
    response = client.get(
        reverse("core:healthz"), HTTP_X_REQUEST_ID=CHOSEN, REMOTE_ADDR="203.0.113.9"
    )
    assert response["X-Request-ID"] != CHOSEN


def test_an_id_from_a_trusted_proxy_is_kept(client):
    response = client.get(
        reverse("core:healthz"), HTTP_X_REQUEST_ID=CHOSEN, REMOTE_ADDR="127.0.0.1"
    )
    assert response["X-Request-ID"] == CHOSEN


def test_no_id_at_all_gets_a_fresh_one(client):
    assert client.get(reverse("core:healthz"), REMOTE_ADDR="203.0.113.9")["X-Request-ID"]
