"""A recovery link ends when the person gets in or changes the password themselves (#739)."""

from __future__ import annotations

import pytest
from allauth.account.signals import password_changed
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from postulo.accounts import recovery
from postulo.accounts.models import RecoveryLink

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def person(db):
    return User.objects.create_user(email="p@example.org", password=PASSWORD, username="person")


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="a@example.org", password=PASSWORD, username="admin", is_staff=True
    )


def live_links(person) -> int:
    return RecoveryLink.objects.live().filter(person=person).count()


def test_changing_the_password_closes_the_link(person, admin):
    recovery.issue(person, by=admin)
    assert live_links(person) == 1

    password_changed.send(sender=User, request=RequestFactory().get("/"), user=person)

    assert live_links(person) == 0


def test_signing_in_closes_the_link(client, person, admin):
    link, token = recovery.issue(person, by=admin)

    client.force_login(person)

    assert live_links(person) == 0
    link.refresh_from_db()
    assert link.revoked_at is not None and link.used_at is None
    with pytest.raises(recovery.Unusable):
        recovery.find(token)


def test_somebody_elses_link_is_untouched(person, admin):
    other = User.objects.create_user(email="o@example.org", password=PASSWORD, username="other")
    recovery.issue(other, by=admin)

    recovery.revoke_for(person)

    assert live_links(other) == 1


def test_a_link_still_works_when_nobody_has_got_in(person, admin):
    link, token = recovery.issue(person, by=admin)

    recovery.spend(recovery.find(token), "another-fairly-long-password-43")

    link.refresh_from_db()
    assert link.used_at is not None
    person.refresh_from_db()
    assert person.check_password("another-fairly-long-password-43")
