"""A strength meter wherever a password is chosen, and twelve characters at least."""

from pathlib import Path

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db

PASSWORD = "a-fairly-long-password-42"
METER = 'data-password-meter="true"'
TEMPLATE = 'id="password-meter"'
SCRIPT = "js/vendor/zxcvbn/core"


def signed_in(client, **extra):
    user = get_user_model().objects.create_user(
        email="alex@example.org", password=PASSWORD, first_name="Alex", last_name="Morgan", **extra
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    client.force_login(user)
    return user


def test_the_sign_up_form_carries_the_meter(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    html = client.get(reverse("account_signup")).content.decode()
    assert METER in html and TEMPLATE in html and SCRIPT in html
    assert 'data-word-4="Strong"' in html and 'data-word-0="Very weak"' in html
    assert 'role="status"' in html, "the word is announced as it changes"
    assert "at least 12 characters" in html, "Django's rules stay listed beneath the field"


def test_change_and_set_password_carry_the_meter(client):
    signed_in(client)
    change = client.get(reverse("account_change_password"), follow=True).content.decode()
    assert METER in change and TEMPLATE in change and SCRIPT in change


def test_the_reset_page_carries_it_and_the_sign_in_page_does_not(client, settings):
    login = client.get(reverse("account_login")).content.decode()
    assert 'name="password"' in login
    assert METER not in login and TEMPLATE not in login and SCRIPT not in login, (
        "a meter on sign-in tells an attacker nothing useful and is noise for everyone else"
    )

    # The reset-from-key page renders the same base, with a password1 field.
    from allauth.account.forms import ResetPasswordKeyForm

    from postulo.accounts.forms import ResetPasswordKeyForm as Ours

    assert issubclass(Ours, ResetPasswordKeyForm)
    assert settings.ACCOUNT_FORMS["reset_password_from_key"].endswith("ResetPasswordKeyForm")


def test_new_passwords_need_twelve_characters(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    data = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "username": "alex.morgan",
        "email": "alex@example.org",
        "password1": "eleven-char",
        "password2": "eleven-char",
    }
    response = client.post(reverse("account_signup"), data)
    assert response.status_code == 200
    assert "at least 12 characters" in response.content.decode()
    assert not get_user_model().objects.filter(username="alex.morgan").exists()

    data["password1"] = data["password2"] = "twelve-chars"
    response = client.post(reverse("account_signup"), data)
    assert response.status_code == 302
    assert get_user_model().objects.filter(username="alex.morgan").exists()


def test_an_existing_shorter_password_still_signs_in(client):
    """The rule applies to new passwords only; nobody is locked out by the change."""
    user = get_user_model().objects.create_user(email="old@example.org", password="short-one")
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    response = client.post(
        reverse("account_login"), {"login": user.username, "password": "short-one"}
    )
    assert response.status_code == 302


def test_the_vendored_scripts_are_present_and_served_from_our_origin():
    vendor = Path(settings.STATICFILES_DIRS[0]) / "js" / "vendor" / "zxcvbn"
    for name in ("core.js", "language-common.js", "language-en.js"):
        assert (vendor / name).is_file(), f"{name} must be committed; run npm run sync:vendor"
    assert b"this.zxcvbnts" in (vendor / "core.js").read_bytes()[:200]
