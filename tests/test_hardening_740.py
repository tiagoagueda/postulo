"""Recovery links, demotion and deletion of an account ask for a recent sign-in (#740)."""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.accounts.models import RecoveryLink
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def admin():
    user = User.objects.create_user(
        email="admin@example.org", username="admin", password=PASSWORD, is_staff=True
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


@pytest.fixture
def member():
    return User.objects.create_user(email="m@example.org", username="member", password=PASSWORD)


def asks(response) -> bool:
    return response.status_code == 302 and "reauthenticate" in response["Location"]


def test_a_session_only_forced_open_cannot_make_a_link_appoint_or_delete(client, admin, member):
    client.force_login(admin)

    link = client.post(reverse("server:person_recovery", args=[member.pk]))
    appoint = client.post(reverse("server:person_admin", args=[member.pk]))
    delete = client.post(
        reverse("server:person_delete", args=[member.pk]), {"confirm_username": "member"}
    )

    assert asks(link) and asks(appoint) and asks(delete)
    assert not RecoveryLink.objects.filter(person=member).exists()
    member.refresh_from_db()
    assert not member.is_staff and User.objects.filter(pk=member.pk).exists()


def test_after_a_recent_sign_in_they_work(client, admin, member):
    signed_in_recently(client, admin)

    made = client.post(reverse("server:person_recovery", args=[member.pk]))
    appointed = client.post(reverse("server:person_admin", args=[member.pk]))

    assert made.status_code == 200 and RecoveryLink.objects.filter(person=member).exists()
    assert appointed.status_code == 302 and "reauthenticate" not in appointed["Location"]
    member.refresh_from_db()
    assert member.is_staff


def test_looking_at_the_pages_needs_nothing_more(client, admin, member):
    client.force_login(admin)

    for name in ("server:person_recovery", "server:person_delete"):
        assert client.get(reverse(name, args=[member.pk])).status_code == 200
