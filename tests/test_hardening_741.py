"""An account with only the provider must have signed in lately for big steps (#741)."""

from __future__ import annotations

import time

import pytest
from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def sso_only():
    user = User.objects.create_user(email="sso@example.org", username="sso")
    user.set_unusable_password()
    user.save()
    return user


def sign_in_at(client, user, seconds_ago):
    client.force_login(user, backend="allauth.account.auth_backends.AuthenticationBackend")
    session = client.session
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {"method": "socialaccount", "at": time.time() - seconds_ago, "provider": "oidc"}
    ]
    session.save()


def test_an_old_session_is_signed_out_and_sent_to_sign_in_again(client, sso_only):
    sign_in_at(client, sso_only, seconds_ago=3600)

    response = client.get(reverse("accounts:delete"))

    assert response.status_code == 302 and response["Location"].startswith(reverse("account_login"))
    assert "next=" in response["Location"], "and back to what they wanted"
    assert client.get(reverse("settings:index")).status_code == 302, "they are signed out"


def test_a_session_that_signed_in_a_moment_ago_passes(client, sso_only):
    sign_in_at(client, sso_only, seconds_ago=30)

    assert client.get(reverse("accounts:delete")).status_code == 200


def test_a_session_with_no_record_of_how_it_began_does_not_pass(client, sso_only):
    client.force_login(sso_only)

    response = client.post(reverse("accounts:delete"), {"confirm_email": sso_only.email})

    assert response.status_code == 302 and response["Location"].startswith(reverse("account_login"))
    assert User.objects.filter(pk=sso_only.pk).exists()


def test_a_person_with_a_password_is_still_asked_for_it_by_allauth(client):
    user = User.objects.create_user(email="p@example.org", username="p", password="a-long-pass-42")
    client.force_login(user)

    response = client.get(reverse("accounts:delete"))

    assert response.status_code == 302 and "reauthenticate" in response["Location"]
