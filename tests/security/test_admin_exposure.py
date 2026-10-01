"""Django's admin: off unless asked for (#116), and with no login of its own (#367).

Found on the public test instance rather than read out of the source. `/admin/` answered
with Django's own username-and-password form, on the open internet, because
`POSTULO_ADMIN_URL` defaulted to the guessable path the comment above it said to move off.
The default is now empty and nothing is mounted; an operator who wants the admin chooses to
run it and chooses where, in the same breath.

That form was the admin's own, and what #116 did about it was to count attempts. What it
could not do was ask for a second factor: Django's login view is not allauth's, so the right
password made a full session for somebody whose account has an authenticator app. The form
is gone. The admin sends whoever is not signed in to Postulo's sign-in, which has the code,
the proven address and the limits already.

Mounting is decided when the URLconf is imported, so the tests that need an admin reload it
under `override_settings` rather than pretending.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP
from django.test import override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

from tests.test_mfa import current_code

pytestmark = pytest.mark.django_db

#: A path an operator might choose. Nothing guesses this, which is the point of choosing.
CHOSEN = "back-office-7f3a/"

PASSWORD = "a-long-enough-password-42"
#: What an authenticator app was given when it was set up.
SECRET = "JBSWY3DPEHPK3PXP"

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
        password=PASSWORD,
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


# --------------------------------------------------------- no login of its own


@pytest.fixture
def administrator(superuser):
    """An administrator as *Hardening* asks for: a proven address and an authenticator app."""
    EmailAddress.objects.create(user=superuser, email=superuser.email, verified=True, primary=True)
    TOTP.activate(superuser, SECRET)
    return superuser


def who_is_signed_in(client) -> str | None:
    return client.session.get("_auth_user_id")


def test_a_password_alone_does_not_open_the_admin(client, with_an_admin, administrator):
    """The finding (#367): the admin's login took a password and asked for nothing else.

    It was Django's own view, so allauth's stages never ran, and the session it made was
    the one the rest of Postulo uses. Somebody with an authenticator app was one phished
    password away from every table.
    """
    response = client.post(
        f"/{CHOSEN}login/",
        {"username": "root", "password": PASSWORD, "next": f"/{CHOSEN}"},
    )

    assert who_is_signed_in(client) is None, "the right password made a session by itself"
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('account_login')}?next=/{CHOSEN}"


def test_the_admin_is_reached_through_postulos_sign_in_and_its_second_step(
    client, with_an_admin, administrator
):
    """The way in that is left, walked from the admin's address to the admin's index."""
    asked = client.get(f"/{CHOSEN}", follow=True)
    assert asked.request["PATH_INFO"] == reverse("account_login")
    assert asked.status_code == 200

    response = client.post(
        reverse("account_login"),
        {"login": "root", "password": PASSWORD, "next": f"/{CHOSEN}"},
    )
    assert response["Location"] == reverse("mfa_authenticate"), "the code is asked for"
    assert who_is_signed_in(client) is None
    assert client.get(f"/{CHOSEN}").status_code == 302, "and the admin stays shut meanwhile"

    response = client.post(reverse("mfa_authenticate"), {"code": current_code(SECRET)})
    assert response["Location"] == reverse("mfa_trust")
    response = client.post(reverse("mfa_trust"), {"action": "dont_trust"})

    assert response["Location"] == f"/{CHOSEN}", "back to where they were going"
    assert client.get(f"/{CHOSEN}").status_code == 200


def test_an_administrator_cannot_become_another_from_inside(
    client, with_an_admin, administrator, django_user_model
):
    """Django's view reads a POST from somebody already signed in, and signs them in again.

    So wrapping it, which is what allauth offers for this, would have left one door: an
    administrator holding a colleague's password skips the colleague's second factor.
    """
    colleague = django_user_model.objects.create_user(
        email="second@example.org", username="second", password=PASSWORD, is_staff=True
    )
    client.force_login(colleague)

    response = client.post(f"/{CHOSEN}login/", {"username": "root", "password": PASSWORD})

    assert who_is_signed_in(client) == str(colleague.pk)
    assert response["Location"] == f"/{CHOSEN}"


def test_somebody_who_is_not_staff_is_refused_and_offered_no_form(client, with_an_admin, user):
    client.force_login(user)

    assert client.get(f"/{CHOSEN}").status_code == 302
    assert client.get(f"/{CHOSEN}login/").status_code == 403
    assert client.post(f"/{CHOSEN}login/", {"username": "root"}).status_code == 403


def test_where_to_go_afterwards_is_never_another_site(client, with_an_admin):
    response = client.get(f"/{CHOSEN}login/", {"next": "https://elsewhere.example/"})

    assert response["Location"] == f"{reverse('account_login')}?next=/{CHOSEN}"


def test_no_limit_of_the_admins_own_is_left_to_drift(with_an_admin):
    """It had one (#116), for a form that is gone. Postulo's sign-in carries allauth's."""
    from allauth.account import app_settings

    assert "admin_login" not in app_settings.RATE_LIMITS
    assert app_settings.RATE_LIMITS.get("login_failed")
