"""Invitation-only registration.

A self-hosted instance holding someone's employment history should not accept strangers
by default, and an invitation addressed to one person should not be redeemable by
whoever else ends up holding the link.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.adapter import INVITE_SESSION_KEY
from postulo.accounts.models import Invite


@pytest.fixture
def staff_user(db, django_user_model):
    return django_user_model.objects.create_user(
        email="operator@example.org", password="not-a-real-password", is_staff=True
    )


@pytest.fixture
def invite(db, staff_user):
    return Invite.objects.create(created_by=staff_user, note="A friend")


# --------------------------------------------------------------------- model rules


def test_a_fresh_invitation_is_valid(invite):
    assert invite.is_valid()
    assert not invite.is_accepted
    assert not invite.is_expired


def test_an_expired_invitation_is_not_valid(db, staff_user):
    expired = Invite.objects.create(
        created_by=staff_user, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert expired.is_expired
    assert not expired.is_valid()


def test_an_accepted_invitation_cannot_be_reused(invite, user):
    invite.accept(user)
    invite.refresh_from_db()

    assert invite.is_accepted
    assert not invite.is_valid()


def test_an_invitation_bound_to_an_address_rejects_a_different_one(db, staff_user):
    bound = Invite.objects.create(created_by=staff_user, email="wanted@example.org")

    assert bound.is_valid("wanted@example.org")
    assert bound.is_valid("WANTED@EXAMPLE.ORG"), "matching should ignore case"
    assert not bound.is_valid("someone.else@example.org")


def test_tokens_are_unpredictable(db, staff_user):
    tokens = {Invite.objects.create(created_by=staff_user).token for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(token) > 30 for token in tokens)


def test_pending_excludes_accepted_and_expired(db, staff_user, user):
    live = Invite.objects.create(created_by=staff_user)
    Invite.objects.create(created_by=staff_user, expires_at=timezone.now() - timedelta(days=1))
    Invite.objects.create(created_by=staff_user).accept(user)

    assert list(Invite.objects.pending()) == [live]


# ------------------------------------------------------------------ signup gating


def test_signup_is_closed_without_an_invitation(client, db, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert b'name="password1"' not in response.content, "the signup form must not be offered"


def test_signup_is_open_when_the_operator_says_so(client, db, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert b'name="password1"' in response.content


def test_following_an_invitation_opens_signup(client, invite, settings):
    settings.POSTULO_REGISTRATION_OPEN = False

    accept = client.get(reverse("accounts:invite_accept", args=[invite.token]))
    assert accept.status_code == 302
    assert accept["Location"] == reverse("account_signup")
    assert client.session[INVITE_SESSION_KEY] == invite.token

    signup = client.get(reverse("account_signup"))
    assert b'name="password1"' in signup.content


def test_an_expired_invitation_link_is_not_found(client, db, staff_user, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    expired = Invite.objects.create(
        created_by=staff_user, expires_at=timezone.now() - timedelta(days=1)
    )

    assert client.get(reverse("accounts:invite_accept", args=[expired.token])).status_code == 404


def test_an_unknown_token_is_not_found(client, db, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    response = client.get(reverse("accounts:invite_accept", args=["not-a-real-token"]))

    assert response.status_code == 404


def test_signing_up_through_an_invitation_spends_it(client, invite, settings, django_user_model):
    settings.POSTULO_REGISTRATION_OPEN = False
    client.get(reverse("accounts:invite_accept", args=[invite.token]))

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "New",
            "last_name": "Comer",
            "username": "newcomer",
            "email": "newcomer@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 302, getattr(response, "context_data", {}).get("form")
    created = django_user_model.objects.filter(email="newcomer@example.org").first()
    assert created is not None, "the invited person should have an account"
    assert created.username == "newcomer"
    assert created.get_full_name() == "New Comer"

    invite.refresh_from_db()
    assert invite.is_accepted
    assert invite.accepted_by == created
    assert INVITE_SESSION_KEY not in client.session


def test_an_invitation_for_one_address_cannot_be_used_by_another(client, db, staff_user, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    bound = Invite.objects.create(created_by=staff_user, email="wanted@example.org")
    client.get(reverse("accounts:invite_accept", args=[bound.token]))

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "Gate",
            "last_name": "Crasher",
            "username": "gatecrasher",
            "email": "gatecrasher@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 200, "the form should be redisplayed with an error"
    assert "only be used with the address it was sent to" in response.content.decode()
    bound.refresh_from_db()
    assert not bound.is_accepted


# ------------------------------------------------------------------- management


@pytest.mark.parametrize(
    "url_name,args",
    [("accounts:invite_list", []), ("accounts:invite_create", [])],
)
def test_invitation_management_requires_staff(client, user, url_name, args):
    client.force_login(user)
    response = client.get(reverse(url_name, args=args))

    assert response.status_code == 403


def test_invitation_management_requires_login(client, db):
    response = client.get(reverse("accounts:invite_list"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_staff_can_create_an_invitation(client, staff_user):
    client.force_login(staff_user)
    response = client.post(
        reverse("accounts:invite_create"), {"email": "friend@example.org", "note": "A friend"}
    )

    assert response.status_code == 302
    created = Invite.objects.get(email="friend@example.org")
    assert created.created_by == staff_user


def test_staff_can_revoke_a_pending_invitation(client, staff_user, invite):
    client.force_login(staff_user)
    response = client.post(reverse("accounts:invite_revoke", args=[invite.pk]))

    assert response.status_code == 302
    assert not Invite.objects.filter(pk=invite.pk).exists()


def test_an_accepted_invitation_cannot_be_revoked(client, staff_user, invite, user):
    invite.accept(user)
    client.force_login(staff_user)

    client.post(reverse("accounts:invite_revoke", args=[invite.pk]))

    assert Invite.objects.filter(pk=invite.pk).exists(), "history should not be erasable"
