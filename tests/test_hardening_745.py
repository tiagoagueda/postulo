"""The names one account may try for its username are bounded (#745)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def taken():
    return User.objects.create_user(email="t@example.org", username="taken", password="x" * 14)


def change(client, username):
    return client.post(reverse("settings:account"), {"username": username})


@override_settings(POSTULO_USERNAME_RATE="3/h")
def test_trying_names_stops_after_the_allowance_and_says_so(client, user, taken):
    from django.core.cache import caches

    caches["default"].clear()
    client.force_login(user)

    answers = [change(client, "taken").content.decode() for _ in range(3)]
    fourth = change(client, "taken").content.decode()

    assert all(
        "already" in a.lower() or "taken" in a.lower() or "exists" in a.lower() for a in answers
    )
    assert "Too many requests" in fourth


@override_settings(POSTULO_USERNAME_RATE="3/h")
def test_saving_the_page_with_the_name_you_already_have_spends_nothing(client, user):
    from django.core.cache import caches

    caches["default"].clear()
    client.force_login(user)

    for _ in range(6):
        response = change(client, user.username)
        assert "Too many requests" not in response.content.decode()


@override_settings(POSTULO_USERNAME_RATE="3/h")
def test_one_accounts_tries_are_not_anothers(client, user, taken):
    from django.core.cache import caches

    caches["default"].clear()
    for _ in range(4):
        client.force_login(user)
        change(client, "taken")
    client.force_login(taken)

    assert "Too many requests" not in change(client, "someone-new").content.decode()
