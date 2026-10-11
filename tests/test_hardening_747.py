"""Links in a message are on the address the operator named, not on the Host header (#747)."""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import reverse

from postulo.notifications.base import absolute_url

pytestmark = pytest.mark.django_db

PUBLIC = "https://postulo.example.org"


@override_settings(POSTULO_PUBLIC_URL=PUBLIC)
def test_a_notification_link_ignores_what_the_request_claims():
    request = RequestFactory().get("/", HTTP_HOST="attacker.example")

    assert absolute_url("/applications/1/", request) == f"{PUBLIC}/applications/1/"


@override_settings(POSTULO_PUBLIC_URL="")
def test_without_one_the_request_is_the_answer_as_before():
    request = RequestFactory().get("/", HTTP_HOST="testserver")

    assert absolute_url("/x/", request) == "http://testserver/x/"
    assert absolute_url("/x/") == "/x/"


@override_settings(POSTULO_PUBLIC_URL=PUBLIC, ALLOWED_HOSTS=["testserver", "attacker.example"])
def test_the_password_reset_mail_is_not_poisoned_by_the_host_header(client, user):
    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    client.post(
        reverse("account_reset_password"), {"email": user.email}, HTTP_HOST="attacker.example"
    )

    [message] = mail.outbox
    assert "attacker.example" not in message.body and "attacker.example" not in message.subject
    assert "postulo.example.org" in message.body
    assert f"{PUBLIC}/accounts/password/reset/key/" in message.body
