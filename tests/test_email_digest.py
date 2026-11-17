"""The built-in e-mail notifier, once a day (#1259).

A connection sends each notification as it happens, as it always did, or keeps them and sends
one mail at an hour of the person's choosing in their own time zone, eight by default. The
keeping is the core's (`hold_until`, `send_many`); what is held here is what a person sees:
nothing before the hour, one mail for everything kept, in the order it happened and the
language it was worded in, the Test button as it was, and a connection whose address is no
longer theirs switched off rather than mailing somebody else.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from postulo.notifications import webhooks
from postulo.notifications.base import Notification
from postulo.notifications.models import DeliveryStatus, WebhookDelivery
from postulo.notifications.service import notify
from postulo.plugins.email import DAILY, EmailNotifier, next_mail_at
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

TOKYO = zoneinfo.ZoneInfo("Asia/Tokyo")


def verified(user, address=None) -> str:
    from allauth.account.models import EmailAddress

    address = address or user.email
    EmailAddress.objects.get_or_create(
        user=user, email=address, defaults={"verified": True, "primary": address == user.email}
    )
    return address


def daily(user, **config) -> Connection:
    return Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Every morning",
        enabled=True,
        config={"to": verified(user), "when": DAILY, **config},
    )


def a_message(n: int, title: str | None = None) -> Notification:
    return Notification(
        event="reminder_due",
        title=title or f"Chase them {n}",
        body=f"Role {n} at Aperture Science",
        url=f"https://postulo.example.org/applications/{n}/",
        key=f"reminder:{n}:2026-11-17T09:00:00+00:00",
    )


def at(when: dt.datetime):
    """The clock at ``when``, for everything that asks Django the time."""
    return mock.patch.object(timezone, "now", return_value=when)


@pytest.fixture
def in_tokyo(user):
    user.profile.time_zone = "Asia/Tokyo"
    user.profile.save(update_fields=["time_zone"])
    return user


def test_kept_until_eight_in_the_persons_own_zone(in_tokyo):
    daily(in_tokyo)
    evening = dt.datetime(2026, 11, 17, 21, 30, tzinfo=TOKYO)

    with at(evening):
        assert notify(in_tokyo, a_message(1)) == 1

    assert mail.outbox == []
    row = WebhookDelivery.objects.get()
    assert row.next_attempt_at == dt.datetime(2026, 11, 18, 8, 0, tzinfo=TOKYO)


def test_the_hour_is_the_connections_and_today_if_it_has_not_come_yet(in_tokyo):
    daily(in_tokyo, hour="18")
    morning = dt.datetime(2026, 11, 17, 9, 0, tzinfo=TOKYO)

    with at(morning):
        notify(in_tokyo, a_message(1))

    assert WebhookDelivery.objects.get().next_attempt_at == dt.datetime(
        2026, 11, 17, 18, 0, tzinfo=TOKYO
    )


def test_eight_stays_eight_across_a_change_of_clocks():
    lisbon = zoneinfo.ZoneInfo("Europe/Lisbon")
    with timezone.override(lisbon):
        # The clocks go back an hour in the night of 24 to 25 October 2026.
        evening = dt.datetime(2026, 10, 24, 20, 0, tzinfo=lisbon)
        nxt = next_mail_at({"when": DAILY}, evening)
    assert (nxt.day, nxt.hour, nxt.minute) == (25, 8, 0)
    assert nxt.utcoffset() == dt.timedelta(0), "winter time by then"


def test_three_events_are_one_mail_in_the_order_they_happened(in_tokyo):
    daily(in_tokyo)
    evening = dt.datetime(2026, 11, 17, 21, 30, tzinfo=TOKYO)
    with at(evening):
        for n in (1, 2, 3):
            notify(in_tokyo, a_message(n))

    with at(dt.datetime(2026, 11, 18, 7, 59, tzinfo=TOKYO)):
        webhooks.send_pending()
    assert mail.outbox == [], "not before the hour"

    with at(dt.datetime(2026, 11, 18, 8, 5, tzinfo=TOKYO)):
        webhooks.send_pending()
        webhooks.send_pending()

    (message,) = mail.outbox
    assert message.to == [in_tokyo.email]
    assert message.subject.endswith("3 notifications")
    body = message.body
    assert body.index("Chase them 1") < body.index("Chase them 2") < body.index("Chase them 3")
    assert "https://postulo.example.org/applications/2/" in body
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {DeliveryStatus.SENT}


def test_one_kept_message_is_mailed_as_it_would_have_been(in_tokyo):
    daily(in_tokyo)
    with at(dt.datetime(2026, 11, 17, 21, 30, tzinfo=TOKYO)):
        notify(in_tokyo, a_message(1, title="Call Cave"))
    with at(dt.datetime(2026, 11, 18, 8, 5, tzinfo=TOKYO)):
        webhooks.send_pending()

    (message,) = mail.outbox
    assert message.subject.endswith("Call Cave")


def test_the_mail_is_worded_in_the_language_the_messages_were(in_tokyo, monkeypatch):
    """Kept in the reader's language; handed over in it, whatever the pass runs in."""
    from postulo.core import languages

    in_tokyo.profile.language = "fr-FR"
    in_tokyo.profile.save(update_fields=["language"])
    daily(in_tokyo)
    with at(dt.datetime(2026, 11, 17, 21, 30, tzinfo=TOKYO)):
        notify(in_tokyo, a_message(1))
        notify(in_tokyo, a_message(2))
    seen = []
    real = EmailNotifier.send_many

    def watching(self, notifications, config, user):
        seen.append(languages.current())
        return real(self, notifications, config, user)

    monkeypatch.setattr(EmailNotifier, "send_many", watching)

    with at(dt.datetime(2026, 11, 18, 8, 5, tzinfo=TOKYO)):
        webhooks.send_pending()

    assert seen == ["fr-FR"]


def test_as_it_happens_is_what_it_always_was(user):
    Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=True,
        config={"to": verified(user)},
    )

    notify(user, a_message(1))

    (message,) = mail.outbox
    assert message.subject.endswith("Chase them 1")
    assert not WebhookDelivery.objects.exists()


def test_the_test_button_mails_at_once_whatever_the_connection_says(user):
    result = EmailNotifier().test({"to": verified(user), "when": DAILY}, user)

    assert result.ok and len(mail.outbox) == 1


def test_an_address_no_longer_verified_retires_the_connection_at_the_hour(in_tokyo):
    from allauth.account.models import EmailAddress

    connection = daily(in_tokyo)
    with at(dt.datetime(2026, 11, 17, 21, 30, tzinfo=TOKYO)):
        notify(in_tokyo, a_message(1))
        notify(in_tokyo, a_message(2))
    EmailAddress.objects.filter(user=in_tokyo).update(verified=False)

    with at(dt.datetime(2026, 11, 18, 8, 5, tzinfo=TOKYO)):
        webhooks.send_pending()

    assert mail.outbox == [], "nobody else is mailed"
    connection.refresh_from_db()
    assert connection.enabled is False
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {
        DeliveryStatus.GIVEN_UP
    }


def test_the_form_offers_once_a_day_and_the_hour(client, user):
    verified(user)
    client.force_login(user)

    page = client.get(reverse("connections:create", args=["notifier", "email"])).content.decode()

    assert 'name="plugin_when"' in page and 'value="daily"' in page
    assert 'name="plugin_hour"' in page and 'value="08" selected' in page
