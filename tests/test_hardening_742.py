"""API tokens do not outlive the password they were made under (#742)."""

from __future__ import annotations

import pytest
from allauth.account.signals import password_changed, password_reset
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from postulo.accounts import recovery
from postulo.api.models import ApiToken
from tests.test_sso import configured  # noqa: F401

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def person():
    return User.objects.create_user(email="p@example.org", username="person", password=PASSWORD)


@pytest.fixture
def admin():
    return User.objects.create_user(
        email="a@example.org", username="admin", password=PASSWORD, is_staff=True
    )


def make_token(owner, name="script"):
    record = ApiToken(owner=owner, name=name, token_hash=ApiToken.hash_token(f"raw-{name}"))
    record.save()
    return record


def active(owner) -> int:
    return ApiToken.objects.active().filter(owner=owner).count()


def test_a_password_change_ends_the_tokens(person):
    make_token(person)
    assert active(person) == 1

    password_changed.send(sender=User, request=RequestFactory().get("/"), user=person)

    assert active(person) == 0


def test_a_reset_ends_them_too(person):
    make_token(person)

    password_reset.send(sender=User, request=RequestFactory().get("/"), user=person)

    assert active(person) == 0


def test_spending_a_recovery_link_ends_them(person, admin):
    make_token(person)
    _link, token = recovery.issue(person, by=admin)

    recovery.spend(recovery.find(token), "another-fairly-long-password-43")

    assert active(person) == 0


def test_the_single_sign_on_wipe_ends_them(person, configured):  # noqa: F811
    from allauth.account.models import EmailAddress

    from tests.test_sso import SocialAccountAdapter, social_login

    EmailAddress.objects.create(user=person, email=person.email, verified=True, primary=True)
    make_token(person)

    found = SocialAccountAdapter().authenticate_by_email(social_login(person.email))

    assert found is not None and found[0] == person
    assert active(person) == 0


def test_somebody_elses_tokens_stay(person, admin):
    make_token(admin, "theirs")

    password_changed.send(sender=User, request=RequestFactory().get("/"), user=person)

    assert active(admin) == 1
