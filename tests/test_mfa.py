"""Two-factor authentication: a code from an app after the password, and a way back."""

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal import auth as totp
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


def current_code(secret: str) -> str:
    counter = next(totp.yield_hotp_counters_from_time())
    return totp.format_hotp_value(totp.hotp_value(secret, counter))


@pytest.fixture
def person(db):
    user = User.objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex", first_name="A", last_name="M"
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def sign_in(client, login="alex"):
    response = client.post(reverse("account_login"), {"login": login, "password": PASSWORD})
    assert response.status_code == 302
    return response


def activate_totp(client) -> str:
    """Walk the activation page as a person would; return the secret that was enrolled."""
    response = client.get(reverse("mfa_activate_totp"))
    assert response.status_code == 200, "signed in a moment ago, so no reauthentication"
    secret = response.context["form"].secret
    response = client.post(reverse("mfa_activate_totp"), {"code": current_code(secret)})
    assert response.status_code == 302
    return secret


def test_setting_up_needs_a_sign_in(client):
    response = client.get(reverse("mfa_index"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_the_pages_sit_inside_settings(client, person):
    sign_in(client)
    html = client.get(reverse("mfa_index")).content.decode()
    assert 'aria-label="Settings sections"' in html
    assert html.count('aria-current="page"') == 1
    assert reverse("settings:account") in html


def test_account_shows_the_state_and_the_door(client, person):
    sign_in(client)
    html = client.get(reverse("settings:account")).content.decode()
    assert 'data-mfa-status="off"' in html
    assert reverse("mfa_index") in html

    activate_totp(client)
    html = client.get(reverse("settings:account")).content.decode()
    assert 'data-mfa-status="on"' in html


def test_activating_enrols_the_app_and_hands_out_recovery_codes(client, person):
    sign_in(client)
    response = client.get(reverse("mfa_activate_totp"))
    assert response.status_code == 200
    secret = response.context["form"].secret
    assert "Postulo" in response.context["totp_url"], "the issuer names the instance"

    response = client.post(reverse("mfa_activate_totp"), {"code": "000000"})
    assert response.status_code == 200, "a wrong code is refused, not enrolled"
    assert not Authenticator.objects.filter(user=person).exists()

    response = client.post(reverse("mfa_activate_totp"), {"code": current_code(secret)})
    assert response.status_code == 302
    assert response.url == reverse("mfa_view_recovery_codes")
    types = set(Authenticator.objects.filter(user=person).values_list("type", flat=True))
    assert types == {Authenticator.Type.TOTP, Authenticator.Type.RECOVERY_CODES}

    html = client.get(reverse("mfa_view_recovery_codes")).content.decode()
    assert 'aria-label="Settings sections"' in html


def test_signing_in_then_asks_for_the_code(client, person):
    sign_in(client)
    secret = activate_totp(client)
    client.logout()

    response = sign_in(client)
    assert response.url == reverse("mfa_authenticate"), "the password alone is not enough now"
    assert not response.wsgi_request.user.is_authenticated

    response = client.post(reverse("mfa_authenticate"), {"code": "000000"})
    assert response.status_code == 200
    response = client.post(reverse("mfa_authenticate"), {"code": current_code(secret)})
    assert response.status_code == 302
    # One more question: trust this browser for a while? Declining still signs in.
    assert response.url == reverse("mfa_trust")
    response = client.post(reverse("mfa_trust"), {"action": "dont_trust"})
    assert response.status_code == 302
    assert client.get(reverse("core:home")).wsgi_request.user.is_authenticated


def test_a_trusted_browser_is_not_asked_again(client, person):
    sign_in(client)
    secret = activate_totp(client)
    client.logout()

    sign_in(client)
    client.post(reverse("mfa_authenticate"), {"code": current_code(secret)})
    response = client.post(reverse("mfa_trust"), {"action": "trust"})
    assert response.status_code == 302
    assert client.get(reverse("core:home")).wsgi_request.user.is_authenticated
    # Sign out the way a person does: the test client's logout() would drop every
    # cookie, the trust cookie included, which is not what closing a session does.
    client.post(reverse("account_logout"))
    assert not client.get(reverse("core:home")).wsgi_request.user.is_authenticated

    response = sign_in(client)
    assert response.url == "/", "the cookie remembers this browser"
    assert client.get(reverse("core:home")).wsgi_request.user.is_authenticated


def test_a_recovery_code_signs_in_once(client, person):
    sign_in(client)
    activate_totp(client)
    codes = Authenticator.objects.get(user=person, type=Authenticator.Type.RECOVERY_CODES)
    unused = codes.wrap().get_unused_codes()
    assert len(unused) == 10
    client.logout()

    sign_in(client)
    response = client.post(reverse("mfa_authenticate"), {"code": unused[0]})
    assert response.status_code == 302
    client.post(reverse("mfa_trust"), {"action": "dont_trust"})
    assert client.get(reverse("core:home")).wsgi_request.user.is_authenticated
    codes.refresh_from_db()
    assert len(codes.wrap().get_unused_codes()) == 9

    client.logout()
    sign_in(client)
    response = client.post(reverse("mfa_authenticate"), {"code": unused[0]})
    assert response.status_code == 200, "spent"


def test_the_reset_command_is_the_way_back_without_the_phone(client, person, capsys):
    sign_in(client)
    activate_totp(client)
    assert Authenticator.objects.filter(user=person).count() == 2

    call_command("mfa_reset", "Alex")
    assert not Authenticator.objects.filter(user=person).exists()
    assert "a password alone signs in now" in capsys.readouterr().out

    call_command("mfa_reset", "alex")
    assert "nothing to remove" in capsys.readouterr().out

    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="No account"):
        call_command("mfa_reset", "nobody")

    client.logout()
    response = sign_in(client)
    assert response.url == "/", "a password alone signs in again"
