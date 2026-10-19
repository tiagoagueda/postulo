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


@override_settings(POSTULO_PUBLIC_URL=PUBLIC)
def test_a_capture_notification_link_is_on_the_operators_address_not_the_stored_host(user):
    """The errand carries the Host the capture request had; the operator's address wins (#962)."""
    from types import SimpleNamespace
    from unittest import mock

    from postulo.notifications import service, slow

    seen = []

    def notify(owner, build):
        seen.append(build())
        return 1

    for event, extra in (("capture_received", {"capture_id": 3}), ("capture_batch", {"count": 2})):
        payload = {"event": event, "base": "https://attacker.example/", **extra}
        with mock.patch.object(service, "notify", notify):
            slow.deliver(SimpleNamespace(payload=payload, owner=user))
    assert len(seen) == 2
    for notification in seen:
        assert notification.url.startswith(PUBLIC + "/"), notification.url


@override_settings(POSTULO_PUBLIC_URL="")
def test_without_a_public_address_a_capture_link_keeps_the_stored_base():
    from postulo.notifications import slow

    assert slow.link({"base": "http://testserver/"}, "/x/") == "http://testserver/x/"
    assert slow.link({}, "/x/") == "/x/"
