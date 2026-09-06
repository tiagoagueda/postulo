"""Passkeys: what the account page offers, and the two things it has to warn about.

The protocol itself is exercised in a real browser with a virtual authenticator
(`tests/e2e/test_passkeys.py`), because that is the only place the browser's own rules
apply. What is here is everything around it — whether it can be offered at all, what a
person is told, and the words on the form.
"""

from __future__ import annotations

import pytest
from allauth.mfa.models import Authenticator
from django.urls import reverse

pytestmark = pytest.mark.django_db


def account_page(client, user):
    client.force_login(user)
    return client.get(reverse("settings:account")).content.decode()


# ------------------------------------------------- whether it can be offered


def test_a_passkey_needs_a_secure_page_and_localhost_counts(rf, settings):
    from postulo.accounts import passkeys

    settings.MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "postulo.example.org"]

    assert passkeys.usable_here(rf.get("/", secure=True))
    assert passkeys.usable_here(rf.get("/", HTTP_HOST="localhost:8000")), (
        "browsers treat localhost as a secure context, which is what makes development work"
    )
    assert not passkeys.usable_here(rf.get("/", HTTP_HOST="postulo.example.org"))


def test_the_page_says_so_rather_than_offering_something_that_cannot_work(client, user, settings):
    """An instance on a mesh VPN reaches Postulo over plain HTTP and cannot have passkeys.

    No setting on this server changes that: the browser refuses the API. Saying so is the
    only useful thing to do, and it beats a button that fails with nothing to read.
    """
    settings.MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False
    html = account_page(client, user)

    assert "data-passkeys-insecure" in html
    assert "HTTPS" in html
    assert "Add one" not in html, "no button for something the browser will refuse"


def test_where_a_passkey_would_be_tied_is_said_before_one_is_made(client, user):
    """A passkey does not move to a second hostname, and there is no migrating it.

    Somebody who reaches their instance at two names, or renames it later, finds every
    passkey unusable. That is the standard's rule, and the moment to say it is now.
    """
    html = account_page(client, user)

    assert "data-passkeys-host" in html
    assert "testserver" in html
    assert "will not carry your passkeys over" in html


# --------------------------------------------------------------- what it says


def test_the_account_page_offers_passkeys_before_the_authenticator_app(client, user):
    """They are the better answer, so they come first."""
    html = account_page(client, user)

    assert html.index("Passkeys") < html.index("Two-factor authentication")
    assert 'data-passkeys="0"' in html


def test_a_person_with_a_passkey_is_told_to_make_recovery_codes(client, user):
    """A passkey can be the only way in. Lose the device and there is nothing else to try."""
    assert "data-no-recovery-codes" not in account_page(client, user), "nothing to warn about yet"

    Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={})
    html = account_page(client, user)
    assert "data-no-recovery-codes" in html
    assert reverse("mfa_view_recovery_codes") in html

    Authenticator.objects.create(user=user, type=Authenticator.Type.RECOVERY_CODES, data={})
    assert "data-no-recovery-codes" not in account_page(client, user)


def test_the_count_is_what_the_page_shows(client, user):
    Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={})
    Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={})
    html = account_page(client, user)

    assert 'data-passkeys="2"' in html
    assert "2 passkeys on this account" in html


# ------------------------------------------------------------------ the form


def test_the_switch_that_matters_is_named_for_what_it_does(client, user):
    """allauth calls it *Passwordless* and says nothing about what turning it off means.

    It is the difference between a way in and a second step after a password, and a key
    added with it off does not appear on the sign-in page at all.
    """
    client.force_login(user)
    response = client.get(reverse("mfa_add_webauthn"), follow=True)
    if "reauthenticate" in response.request["PATH_INFO"]:
        client.post(reverse("account_reauthenticate"), {"password": "not-a-real-password"})
        response = client.get(reverse("mfa_add_webauthn"), follow=True)
    html = response.content.decode()

    assert "Let this passkey sign me in on its own" in html
    assert "Passwordless" not in html
    assert "What to call it" in html


def test_signing_in_with_a_passkey_is_offered_and_signing_up_with_one_is_not(client, settings):
    """Who may register here is the operator's decision; a passkey does not change it."""
    assert settings.MFA_PASSKEY_LOGIN_ENABLED is True
    assert settings.MFA_PASSKEY_SIGNUP_ENABLED is False

    html = client.get(reverse("account_login")).content.decode()
    assert "Sign in with a passkey" in html


def test_the_passkey_is_registered_against_the_instances_own_name(rf, settings):
    """What a password manager shows in its list. Django's site name is example.com."""
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "postulo.example.org"]

    from allauth.core import context
    from allauth.mfa.adapter import get_adapter

    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Alex's job search"})

    request = rf.get("/", HTTP_HOST="postulo.example.org")
    with context.request_context(request):
        entity = get_adapter().get_public_key_credential_rp_entity()

    assert entity["name"] == "Alex's job search"
    assert entity["id"] == "postulo.example.org", "the host, so an existing key keeps working"
