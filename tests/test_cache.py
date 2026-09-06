"""The cache, because something that counts lives in it.

allauth's rate limits are kept in Django's default cache: ten failed sign-ins a minute
from one address, five in five minutes against one account, and so on. Django's own
default cache is `LocMemCache`, a dictionary inside one process, and the production image
runs three gunicorn workers — so a limit written as ten was really ten per worker,
whichever one the request happened to land on, and every restart forgot the lot.

The default here is therefore a database-backed cache: shared by every worker, kept across
a restart, and needing nothing an instance does not already have. An operator with Redis
or Memcached sets `POSTULO_CACHE_URL` and gets a faster one.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ the backend


def test_the_default_cache_is_not_confined_to_one_process():
    """The property that matters, stated as the thing it must not be."""
    backend = settings.CACHES["default"]["BACKEND"]
    assert backend != "django.core.cache.backends.locmem.LocMemCache", (
        "a per-process cache makes every rate limit as many times weaker as there are workers"
    )
    assert backend != "django.core.cache.backends.dummy.DummyCache", (
        "a cache that stores nothing counts nothing"
    )


def test_the_table_the_cache_needs_is_made_by_a_migration():
    """So a fresh install has one without anybody being told to run a command."""
    if settings.CACHES["default"]["BACKEND"] != "django.core.cache.backends.db.DatabaseCache":
        pytest.skip("this instance is pointed at a cache that needs no table")
    assert settings.CACHES["default"]["LOCATION"] in connection.introspection.table_names()


def test_a_value_written_by_one_client_is_read_by_another():
    """Two handles onto the cache, the way two workers hold two of their own.

    A `LocMemCache` passes this inside one process and fails it across two, which is
    exactly the failure that cannot be reproduced in a single-process test — so what is
    asserted here is the weaker, checkable half, and the backend check above carries the
    rest.
    """
    caches["default"].set("postulo-test-key", "written once", 60)
    try:
        assert caches.create_connection("default").get("postulo-test-key") == "written once"
    finally:
        caches["default"].delete("postulo-test-key")


# -------------------------------------------------------------- the limit itself


def test_repeated_wrong_passwords_are_eventually_refused(client, user):
    """allauth's own limit, exercised end to end, so the wiring is known to be connected.

    `login_failed` is `10/m/ip,5/300s/key`: five attempts against one account inside five
    minutes, and the sixth is turned away. The limit lives in the cache, so this passes
    only while the cache actually keeps what it is given.
    """
    caches["default"].clear()
    url = reverse("account_login")

    refused = []
    for attempt in range(1, 9):
        response = client.post(url, {"login": user.username, "password": "not-the-password"})
        if "too many" in response.content.decode().lower():
            refused.append(attempt)

    assert refused, "the attempts were never rate limited"
    assert refused[0] == 6, f"expected the sixth attempt to be turned away, not the {refused[0]}th"


def test_the_right_password_still_works_for_somebody_else(client, user, django_user_model):
    """A limit keyed on the account must not lock out the rest of the instance."""
    caches["default"].clear()
    other = django_user_model.objects.create_user(
        username="somebodyelse",
        email="somebody@example.org",
        password="a-perfectly-good-one",
    )
    for _attempt in range(6):
        client.post(reverse("account_login"), {"login": user.username, "password": "wrong"})

    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=other, email=other.email, verified=True, primary=True)
    response = client.post(
        reverse("account_login"),
        {"login": other.username, "password": "a-perfectly-good-one"},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated
