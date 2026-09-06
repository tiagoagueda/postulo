"""When Postulo asks for a second factor after somebody has already proved who they are.

A password plus a code is two factors. A passkey is two factors on its own, and single
sign-on may be more than two at the identity provider — but Postulo cannot see which,
because how the provider authenticated somebody is not in what it sends back.

So one of these is settled and the other is the operator's to decide, and this file is
where that distinction is written down as behaviour.
"""

from __future__ import annotations

import pytest
from allauth.mfa.models import Authenticator
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.urls import reverse

from postulo.accounts import stages

pytestmark = pytest.mark.django_db

PASSWORD = "not-a-real-password"


def with_totp(user):
    """An account that would be asked for a code."""
    Authenticator.objects.create(
        user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"}
    )
    return user


def a_request(rf):
    request = rf.get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


# ------------------------------------------------------- which method was used


def test_the_last_method_used_is_the_one_that_counts(rf):
    """A session opened with a password and later linked to a provider was not opened by it."""
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY

    request = a_request(rf)
    assert not stages.arrived_through_the_provider(request), "nothing has happened yet"

    request.session[AUTHENTICATION_METHODS_SESSION_KEY] = [{"method": "socialaccount"}]
    assert stages.arrived_through_the_provider(request)

    request.session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {"method": "socialaccount"},
        {"method": "password"},
    ]
    assert not stages.arrived_through_the_provider(request), "the password was the way in"


# ------------------------------------------------------------- a password login


def test_a_password_sign_in_is_always_asked_for_the_code(client, user, settings):
    """Whatever else is turned on. This is the case the setting must never touch."""
    settings.POSTULO_OIDC_IS_SECOND_FACTOR = True
    with_totp(user)

    from allauth.account.models import EmailAddress

    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )

    response = client.post(
        reverse("account_login"), {"login": user.username, "password": PASSWORD}, follow=True
    )

    assert "2fa/authenticate" in response.request["PATH_INFO"]
    assert not response.wsgi_request.user.is_authenticated


# ------------------------------------------------------------------ a passkey


def test_a_passkey_needs_no_setting_because_it_is_already_two_factors(rf, user):
    """The device you have, released by something you are or know.

    Asking for a code afterwards is a second lock on a door that already has one, and it
    is the friction that makes people switch two-factor authentication off altogether.
    allauth skips it after a passwordless passkey sign-in, so there is nothing to
    configure — and a switch to put the prompt back would be a feature that only makes
    things worse.
    """
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY
    from allauth.mfa.webauthn.internal.flows import did_use_passwordless_login

    with_totp(user)
    Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={})

    request = a_request(rf)
    # Exactly the record allauth writes when somebody signs in with a passkey and nothing
    # else, which is the condition its own stage checks.
    request.session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {"method": "mfa", "type": "webauthn", "passwordless": True}
    ]
    assert did_use_passwordless_login(request), "the record this test rests on"

    stage = stages.SecondFactorStage.__new__(stages.SecondFactorStage)
    stage.login = type("L", (), {"user": user})()
    assert stage._should_handle(request) is False, "no code asked for after a passkey"


# --------------------------------------------------------- single sign-on


def test_single_sign_on_is_asked_for_a_code_unless_the_operator_says_otherwise(rf, user):
    """Off by default: Postulo cannot see what the provider actually checked."""
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY

    with_totp(user)
    request = a_request(rf)
    request.session[AUTHENTICATION_METHODS_SESSION_KEY] = [{"method": "socialaccount"}]

    stage = stages.SecondFactorStage.__new__(stages.SecondFactorStage)
    stage.login = type("L", (), {"user": user})()

    assert stage._should_handle(request) is True, "asked, because nobody has said not to"


def test_an_operator_can_say_the_provider_did_the_checking(rf, user, settings):
    settings.POSTULO_OIDC_IS_SECOND_FACTOR = True
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY

    with_totp(user)
    request = a_request(rf)
    request.session[AUTHENTICATION_METHODS_SESSION_KEY] = [{"method": "socialaccount"}]

    stage = stages.SecondFactorStage.__new__(stages.SecondFactorStage)
    stage.login = type("L", (), {"user": user})()

    assert stage._should_handle(request) is False


def test_saying_so_never_removes_anybodys_authenticator_app(rf, user, settings):
    """It changes when a code is asked for, not whether the account has one."""
    settings.POSTULO_OIDC_IS_SECOND_FACTOR = True
    with_totp(user)

    from allauth.mfa.utils import is_mfa_enabled

    assert is_mfa_enabled(user), "still set up, still there, still asked for on a password"


# ------------------------------------------------------------- the setting


def test_the_switch_is_on_the_sign_in_settings_page(client, admin_user):
    client.force_login(admin_user)
    html = client.get(reverse("server:signin")).content.decode()

    assert "sso_is_second_factor" in html
    assert "Single sign-on counts as the second factor" in html
    assert "A passkey already" in html, "and says why there is no switch for that"


def test_an_administrator_can_turn_it_on_from_the_page(client, admin_user):
    from postulo.core import site

    client.force_login(admin_user)
    assert site.sso_is_second_factor() is False

    client.post(reverse("server:signin"), {"registration_open": "", "sso_is_second_factor": "true"})
    assert site.sso_is_second_factor() is True


def test_the_environment_still_wins(client, admin_user, settings, monkeypatch):
    """A .env written for an earlier release goes on meaning what it meant."""
    from postulo.core import site

    monkeypatch.setenv("POSTULO_OIDC_IS_SECOND_FACTOR", "true")
    settings.POSTULO_OIDC_IS_SECOND_FACTOR = True

    client.force_login(admin_user)
    client.post(
        reverse("server:signin"), {"registration_open": "", "sso_is_second_factor": "false"}
    )

    assert site.sso_is_second_factor() is True, "the environment pins it"
    html = client.get(reverse("server:signin")).content.decode()
    assert "POSTULO_OIDC_IS_SECOND_FACTOR" in html, "and the page says so rather than lying"
