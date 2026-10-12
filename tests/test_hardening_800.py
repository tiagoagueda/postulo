"""/healthz tells a stranger the status and not the version (#800)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo import __version__

pytestmark = pytest.mark.django_db


def test_a_stranger_is_told_status_and_database_only(client):
    body = client.get(reverse("core:healthz"), REMOTE_ADDR="203.0.113.9").json()
    assert body == {"status": "ok", "database": "ok"}


def test_the_containers_own_probe_still_gets_the_version(client):
    body = client.get(reverse("core:healthz"), REMOTE_ADDR="127.0.0.1").json()
    assert body["version"] == __version__


def test_an_administrator_gets_the_version(client, admin_user):
    client.force_login(admin_user)
    body = client.get(reverse("core:healthz"), REMOTE_ADDR="203.0.113.9").json()
    assert body["version"] == __version__


def test_a_member_does_not(client, user):
    client.force_login(user)
    assert "version" not in client.get(reverse("core:healthz"), REMOTE_ADDR="203.0.113.9").json()
