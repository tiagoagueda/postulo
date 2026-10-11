"""Deleting an account leaves no invitation naming it and no session holding its imports (#750)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client

from postulo.accounts import deletion, sessions
from postulo.accounts.models import Invite

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def maria():
    return User.objects.create_user(
        email="maria@private.example", username="maria", password=PASSWORD
    )


@pytest.fixture
def staff():
    return User.objects.create_user(
        email="s@example.org", username="staff", password=PASSWORD, is_staff=True
    )


def signed_in_with_an_import(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session["csv_import"] = {"rows": [["Aperture", "Tester", "private notes"]]}
    session.save()
    return client


def test_the_invitation_that_named_her_goes_with_her(maria, staff):
    invite, _token = Invite.issue(
        created_by=staff, email=maria.email, note="Maria from the bootcamp"
    )
    invite.accept(maria)

    report = deletion.delete_account(maria)

    assert not Invite.objects.filter(pk=invite.pk).exists()
    assert report.rows["accepted invitations removed"] == 1


def test_her_sessions_and_what_they_hold_go_with_her(maria, staff):
    signed_in_with_an_import(maria)
    signed_in_with_an_import(maria)
    signed_in_with_an_import(staff)

    assert len(sessions.keys_of(maria)) == 2
    report = deletion.delete_account(maria)

    assert report.rows["sessions ended"] == 2
    assert sessions.keys_of(maria) == []
    assert Session.objects.count() == 1, "somebody else's session is left alone"


def test_a_row_that_cannot_be_read_is_skipped(maria):
    Session.objects.create(
        session_key="garbage", session_data="not-base64", expire_date="2099-01-01T00:00:00Z"
    )

    assert sessions.keys_of(maria) == []
