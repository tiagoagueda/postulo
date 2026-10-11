"""*Sync now* is bounded per account, as *Test* is (#786)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse

from postulo.core import throttle

from .test_syncs import a_contact, a_sync

pytestmark = pytest.mark.django_db


def test_the_default_is_a_real_limit_and_looser_than_a_schedulers_quarter_hour(settings):
    rate = throttle.rate_for("POSTULO_CONNECTION_SYNC_RATE")

    assert rate and rate.times * 3600 / rate.seconds <= 60


def test_sync_now_stops_after_the_limit_and_says_when_to_try_again(client, user, settings):
    settings.POSTULO_CONNECTION_SYNC_RATE = "2/h"
    cache.clear()
    connection = a_sync(user)
    a_contact(user)
    client.force_login(user)
    url = reverse("connections:sync_now", args=[connection.pk])

    first = client.post(url, follow=True).content.decode()
    second = client.post(url, follow=True).content.decode()
    third = client.post(url, follow=True).content.decode()

    assert "Synced" in first or "Sync failed" in first or "already" in first
    assert "That is a lot of syncs" not in second
    assert "That is a lot of syncs. Try again in" in third


def test_the_limit_is_each_accounts_own(client, user, other_user, settings):
    settings.POSTULO_CONNECTION_SYNC_RATE = "1/h"
    cache.clear()
    mine, theirs = a_sync(user), a_sync(other_user)
    for owner, connection in ((user, mine), (other_user, theirs)):
        client.force_login(owner)
        page = client.post(reverse("connections:sync_now", args=[connection.pk]), follow=True)
        assert "That is a lot of syncs" not in page.content.decode()
