"""Somebody who changes a password is mailed about it (#793)."""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import mail
from django.urls import reverse

from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

PASSWORD = "a-fairly-long-password-42"


def test_the_setting_is_on():
    assert settings.ACCOUNT_EMAIL_NOTIFICATIONS is True


def test_changing_a_password_mails_the_address_on_the_account(client, user):
    user.set_password(PASSWORD)
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    signed_in_recently(client, user)
    mail.outbox.clear()
    response = client.post(
        reverse("account_change_password"),
        {
            "oldpassword": PASSWORD,
            "password1": "another-long-password-77",
            "password2": "another-long-password-77",
        },
    )
    assert response.status_code == 302, getattr(response, "context", None)
    assert any(user.email in m.to for m in mail.outbox)
