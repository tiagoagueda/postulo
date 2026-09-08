"""Django's admin: off unless asked for, and throttled when it is (#116).

Found on the public test instance rather than read out of the source. `/admin/` answered
with Django's own username-and-password form, on the open internet, because
`POSTULO_ADMIN_URL` defaulted to the guessable path the comment above it said to move off.
And allauth's rate limits — good ones, and inherited — cover allauth's views;
`django.contrib.admin` has a login of its own, and nothing was counting attempts against it.
So the one credential form with no attempt limiting was the one that reaches every table.

Two changes, and a test each way round. The default is now empty and nothing is mounted;
an operator who wants the admin chooses to run it and chooses where, in the same breath.
When they do, its login is held to the same limits as a failed sign-in here.

Mounting is decided when the URLconf is imported, so the tests that need an admin reload it
under `override_settings` rather than pretending.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

pytestmark = pytest.mark.django_db

#: A path an operator might choose. Nothing guesses this, which is the point of choosing.
CHOSEN = "back-office-7f3a/"

WITH_AN_ADMIN = override_settings(POSTULO_ADMIN_URL=CHOSEN, ROOT_URLCONF="postulo.config.urls")


@pytest.fixture
def with_an_admin():
    """Mount the admin at a chosen path, then put the URLconf back as it was."""
    import importlib

    from postulo.config import urls

    with WITH_AN_ADMIN:
        importlib.reload(urls)
        clear_url_caches()
        yield CHOSEN
    importlib.reload(urls)
    clear_url_caches()


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_user(
        email="root@example.org",
        username="root",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


# --------------------------------------------------------------- off unless asked


def test_nothing_is_published_at_the_guessable_path(client):
    """The finding, as a test: what a stranger gets from the first path they would try."""
    for path in ("/admin/", "/admin/login/", "/django-admin/"):
        assert client.get(path).status_code == 404, path


def test_the_default_mounts_no_admin_at_all(settings):
    assert settings.POSTULO_ADMIN_URL == ""
    with pytest.raises(NoReverseMatch):
        reverse("admin:index")


def test_an_operator_who_asks_for_one_gets_it_where_they_asked(client, with_an_admin):
    assert reverse("admin:index") == f"/{CHOSEN}"

    response = client.get(f"/{CHOSEN}")

    assert response.status_code == 302
    assert "login" in response["Location"]
    assert client.get("/admin/").status_code == 404, "and only where they asked"


@pytest.mark.parametrize(
    "written,mounted",
    [
        ("back-office", "back-office/"),
        ("/back-office/", "back-office/"),
        ("  back-office/  ", "back-office/"),
        ("", ""),
    ],
)
def test_the_path_an_operator_writes_is_tidied_rather_than_silently_broken(
    monkeypatch, written, mounted
):
    """A forgotten slash produced a URL nobody could reach and no error saying why.

    Through the environment, because that is where the value comes from: assigning to
    `settings.POSTULO_ADMIN_URL` would not exercise the normalisation, which happens once
    when the value is read.
    """
    from importlib import reload

    from postulo.config.settings import base

    monkeypatch.setenv("POSTULO_ADMIN_URL", written)
    reload(base)

    assert base.POSTULO_ADMIN_URL == mounted


# ------------------------------------------------------------------- throttled


def test_the_admin_login_is_rate_limited(client, with_an_admin, superuser):
    """allauth's limits cover allauth's views. This is the one they did not reach."""
    login = f"/{CHOSEN}login/"
    codes = [
        client.post(login, {"username": "root", "password": "wrong-every-time"}).status_code
        for _ in range(12)
    ]

    assert 429 in codes, f"nothing throttled twelve attempts: {codes}"
    assert codes.index(429) <= 10, "it should bite around the tenth, not eventually"


def test_the_limit_is_configured_beside_allauths_own(client, with_an_admin, superuser):
    """One scheme, not two that drift: it is a key in the same dict, using the same cache."""
    from allauth.account import app_settings

    assert app_settings.RATE_LIMITS["admin_login"] == settings.ACCOUNT_RATE_LIMITS["admin_login"]
    # The defaults are still there rather than replaced by the one key added.
    assert app_settings.RATE_LIMITS.get("login_failed")


def test_loading_the_form_is_not_an_attempt(client, with_an_admin):
    """A GET costs nothing; guessing costs. Reading the page many times must not lock it."""
    login = f"/{CHOSEN}login/"

    codes = [client.get(login).status_code for _ in range(15)]

    assert set(codes) == {200}, codes


def test_a_correct_password_still_works_when_nobody_has_been_guessing(
    client, with_an_admin, superuser
):
    """The limit must not be so eager that it stops the person it is protecting."""
    response = client.post(
        f"/{CHOSEN}login/",
        {"username": "root", "password": "a-long-enough-password-42", "next": f"/{CHOSEN}"},
    )

    assert response.status_code == 302
    assert response["Location"] == f"/{CHOSEN}"
