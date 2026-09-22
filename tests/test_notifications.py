"""Notifications: events, the dispatcher, the built-in email notifier, and the scheduler."""

import datetime as dt
import re
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.jobs.models import Company, JobPosting
from postulo.notifications import base
from postulo.notifications.base import Notification, absolute_url
from postulo.notifications.management.commands.send_due_reminders import announce_due_reminders
from postulo.notifications.service import notify
from postulo.plugins import registry
from postulo.plugins.email import EmailNotifier
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

PAGE = """<html><head><title>Job</title>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting",
"title":"Research Engineer","hiringOrganization":{"@type":"Organization","name":"Black Mesa"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Lyon"}}}</script></head></html>"""


def verified(user, address=None) -> str:
    """Make `address` -- the account's own by default -- one of the person's verified ones."""
    from allauth.account.models import EmailAddress

    address = address or user.email
    EmailAddress.objects.get_or_create(
        user=user,
        email=address,
        defaults={"verified": True, "primary": address == user.email},
    )
    return address


def email_connection(user, to=None, *, enabled=True, **config):
    # The notifier writes only to the person's own verified addresses since #232, so the
    # address a test asks for is made one of theirs here.
    to = verified(user, to)
    connection = Connection(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=enabled,
        config={"to": to, **config},
    )
    connection.save()
    return connection


def an_application(user, title="Test Engineer"):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title=title)
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


# ------------------------------------------------------------------- the model


def test_a_notification_names_a_known_event():
    Notification(event="reminder_due", title="x")
    with pytest.raises(ValueError, match="Unknown notification event"):
        Notification(event="birthday", title="x")


def test_a_notification_says_what_it_is_when_it_happened_and_what_it_is_about():
    """The four fields a notifier needed and had to guess at (#229).

    `key` is what makes two sends of the same thing one thing: the words are translated at
    the moment of sending and differ between two recipients of the same event, so a notifier
    deduplicating on the title would deliver twice to a household and never twice to one
    person who reads Postulo in two languages.
    """
    when = timezone.now()
    notification = Notification(
        event="reminder_due",
        title="Chase Aperture",
        key="reminder:41",
        occurred_at=when,
        data={"reminder_id": 41},
    )

    assert notification.key == "reminder:41"
    assert notification.occurred_at == when
    assert notification.data["reminder_id"] == 41

    plain = Notification(event="reminder_due", title="Chase Aperture")
    assert plain.key == "", "no identity claimed, so nothing may be deduplicated on it"
    assert plain.language == "" and plain.occurred_at is None
    assert dict(plain.data) == {}


def test_what_a_notification_is_about_cannot_be_written_to_by_a_notifier():
    """It is handed to every notifier a person has, one after another."""
    notification = Notification(event="reminder_due", title="x", data={"reminder_id": 41})

    with pytest.raises(TypeError):
        notification.data["reminder_id"] = 42


def test_a_notification_is_stamped_with_the_language_it_came_out_in(user):
    """Set by `notify` rather than by every sender, because it is the one place that knows.

    A notifier rendering around the words -- a subject line, a footer, a push payload --
    matches them instead of matching whatever request happens to be in flight (#223, #229).
    """
    seen = []
    email_connection(user)
    user.profile.language = "pt-PT"
    user.profile.save(update_fields=["language"])

    def remember(self, notification, config, recipient):
        seen.append(notification)

    with mock.patch.object(EmailNotifier, "send", remember):
        notify(user, Notification(event="reminder_due", title="Chase them"))

    assert seen and seen[0].language == "pt-PT"


def test_a_reminder_falling_due_is_the_same_message_however_often_it_is_announced(user):
    """A key a notifier can retry against, built from the reminder rather than the words."""
    application = an_application(user)
    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase them", due_at=timezone.now()
    )
    seen = []
    email_connection(user)

    def remember(self, notification, config, recipient):
        seen.append(notification)

    with mock.patch.object(EmailNotifier, "send", remember):
        announce_due_reminders()
        Reminder.objects.filter(pk=reminder.pk).update(notified_at=None)
        announce_due_reminders()

    assert len(seen) == 2, "announced twice, because the stamp was cleared between passes"
    assert seen[0].key == seen[1].key == f"reminder:{reminder.pk}"
    assert seen[0].occurred_at == reminder.due_at, "when it fell due, not when it was sent"
    assert seen[0].data["reminder_id"] == reminder.pk
    assert seen[0].data["application_id"] == application.pk


def test_links_come_from_the_request_the_public_url_or_stay_bare(rf, settings):
    request = rf.get("/", HTTP_HOST="testserver")
    assert absolute_url("/a/", request) == "http://testserver/a/"
    settings.POSTULO_PUBLIC_URL = "https://jobs.example.org"
    assert absolute_url("/a/") == "https://jobs.example.org/a/"
    settings.POSTULO_PUBLIC_URL = ""
    assert absolute_url("/a/") == "/a/"
    assert absolute_url("https://x.example/y") == "https://x.example/y"


def test_the_email_notifier_ships_in_the_box(client, user):
    assert registry.find_plugin("notifier", "email").label == "Email"
    client.force_login(user)
    html = client.get(reverse("connections:pick")).content.decode()
    assert reverse("connections:create", args=["notifier", "email"]) in html


def test_a_notifier_connection_carries_a_switch_per_event(client, user):
    verified(user)
    client.force_login(user)
    url = reverse("connections:create", args=["notifier", "email"])
    html = client.get(url).content.decode()
    for event in base.EVENTS:
        assert f'name="plugin_event_{event}"' in html
        assert re.search(rf'name="plugin_event_{event}"[^>]*checked', html), "on by default"

    response = client.post(
        url,
        {
            "label": "Mail me",
            "enabled": "on",
            "plugin_to": user.email,
            "plugin_event_reminder_due": "on",
            # capture_received left unticked
        },
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert connection.config == {
        "to": user.email,
        "event_reminder_due": True,
        "event_capture_received": False,
        "event_went_quiet": False,
        "event_posting_closing": False,
    }


def test_the_address_is_chosen_among_the_persons_own_and_not_typed(client, user):
    """A notifier sends to the person, not to anybody they name (#232)."""
    from allauth.account.models import EmailAddress

    verified(user)
    verified(user, "second@example.org")
    EmailAddress.objects.create(user=user, email="unproven@example.org", verified=False)
    client.force_login(user)
    url = reverse("connections:create", args=["notifier", "email"])

    html = client.get(url).content.decode()
    select = re.search(r'<select[^>]*name="plugin_to"[^>]*>(.*?)</select>', html, re.S)
    assert select, "a choice, not a text field"
    assert user.email in select.group(1) and "second@example.org" in select.group(1)
    assert "unproven@example.org" not in select.group(1)

    response = client.post(
        url, {"label": "Mail", "enabled": "on", "plugin_to": "stranger@example.org"}
    )
    assert response.status_code == 200
    assert "plugin_to" in response.context["form"].errors
    assert not Connection.objects.filter(owner=user).exists()


def test_with_nothing_verified_there_is_no_address_to_choose(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:create", args=["notifier", "email"])).content.decode()
    assert 'name="plugin_to"' not in html


# ---------------------------------------------------------------- dispatching


def test_notify_reaches_every_enabled_connection_that_wants_the_event(user, other_user):
    email_connection(user, "one@example.org")
    email_connection(user, "two@example.org", event_capture_received=False)
    email_connection(user, "off@example.org", enabled=False)
    email_connection(other_user, "them@example.org")

    delivered = notify(user, Notification(event="capture_received", title="Captured: X"))

    assert delivered == 1
    assert [m.to for m in mail.outbox] == [["one@example.org"]]
    assert mail.outbox[0].subject == "[Postulo] Captured: X"

    mail.outbox.clear()
    delivered = notify(user, Notification(event="reminder_due", title="Chase them", url="/a/1/"))
    assert delivered == 2
    assert sorted(m.to[0] for m in mail.outbox) == ["one@example.org", "two@example.org"]
    assert "/a/1/" in mail.outbox[0].body


def test_a_failing_notifier_is_recorded_and_never_fails_the_caller(user, monkeypatch):
    connection = email_connection(user)

    def broken(self, notification, config, recipient):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(EmailNotifier, "send", broken)
    assert notify(user, Notification(event="reminder_due", title="x")) == 0
    connection.refresh_from_db()
    assert connection.last_error == "ConnectionError: smtp down"


def test_the_email_notifier_tests_itself_and_falls_back_to_the_primary_address(user):
    from postulo.plugins.api import ConnectionUnusable

    plugin = EmailNotifier()
    refused = plugin.test({"to": user.email}, user=user)
    assert refused.ok is False and "verified" in refused.message, "nothing verified yet"
    assert not mail.outbox

    verified(user)
    verified(user, "second@example.org")
    result = plugin.test({"to": "second@example.org"}, user=user)
    assert result.ok and "second@example.org" in result.message
    assert mail.outbox[-1].to == ["second@example.org"]

    plugin.send(Notification(event="reminder_due", title="Ping"), {}, user)
    assert mail.outbox[-1].to == [user.email], "no address given: the primary one"

    # An address that is not theirs -- typed into an old connection, or one they removed
    # since -- is refused rather than mailed, and the connection is switched off for it.
    assert plugin.test({"to": "stranger@example.org"}, user=user).ok is False
    with pytest.raises(ConnectionUnusable, match="no longer one of your verified"):
        plugin.send(
            Notification(event="reminder_due", title="Ping"), {"to": "stranger@example.org"}, user
        )
    assert mail.outbox[-1].to == [user.email], "nothing went to the stranger"


def test_a_title_never_breaks_the_subject_line():
    from postulo.plugins.email import _subject

    assert _subject("Captured: Engineer\r\nBcc: everyone@example.org") == (
        "[Postulo] Captured: Engineer Bcc: everyone@example.org"
    )


def test_the_test_button_is_bounded_per_account(client, user, settings):
    """A test is a real message at a press of a button, and the button had no bound (#232)."""
    from django.core.cache import cache

    cache.clear()
    settings.POSTULO_CONNECTION_TEST_RATE = "2/h"
    connection = email_connection(user)
    client.force_login(user)
    url = reverse("connections:test", args=[connection.pk])

    client.post(url)
    client.post(url)
    assert len(mail.outbox) == 2
    response = client.post(url, follow=True)
    assert "That is a lot of tests" in response.content.decode()
    assert len(mail.outbox) == 2, "the third press sent nothing"


# ---------------------------------------------------------- the capture event


def test_a_capture_through_the_api_is_announced(client, user):
    email_connection(user)
    _record, raw = ApiToken.issue(user, "Extension")
    response = client.post(
        "/api/v1/captures",
        {"url": "https://example.org/jobs/7", "html": PAGE},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 201
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "[Postulo] Captured: Research Engineer"
    assert "Black Mesa" in message.body and "Lyon" in message.body
    assert "http://testserver/jobs/captures/" in message.body


# ------------------------------------------------------------ the scheduler


def test_due_reminders_are_announced_once_and_stamped(user, settings):
    settings.POSTULO_PUBLIC_URL = "https://jobs.example.org"
    email_connection(user)
    application = an_application(user)
    now = timezone.now()
    due = Reminder.objects.create(
        owner=user,
        application=application,
        summary="Chase them",
        due_at=now - dt.timedelta(hours=1),
    )
    later = Reminder.objects.create(
        owner=user, application=application, summary="Later", due_at=now + dt.timedelta(days=1)
    )
    loose = Reminder.objects.create(
        owner=user, summary="Loose end", due_at=now - dt.timedelta(days=1)
    )

    stamped, delivered = announce_due_reminders()
    assert (stamped, delivered) == (2, 2)
    subjects = sorted(m.subject for m in mail.outbox)
    assert subjects == ["[Postulo] Chase them", "[Postulo] Loose end"]
    chase = next(m for m in mail.outbox if "Chase" in m.subject)
    assert "Test Engineer at Aperture Science" in chase.body
    assert f"https://jobs.example.org{application.get_absolute_url()}" in chase.body

    due.refresh_from_db()
    later.refresh_from_db()
    loose.refresh_from_db()
    assert due.notified_at and loose.notified_at and later.notified_at is None

    mail.outbox.clear()
    assert announce_due_reminders() == (0, 0), "announced once"


def test_a_due_reminder_is_stamped_even_with_nobody_to_tell(user):
    Reminder.objects.create(
        owner=user, summary="x", due_at=timezone.now() - dt.timedelta(minutes=5)
    )
    assert announce_due_reminders() == (1, 0)
    assert Reminder.objects.get().notified_at is not None, (
        "adding a notifier later must not replay it"
    )


def test_the_command_runs_one_pass(user, capsys):
    call_command("send_due_reminders")
    assert "Nothing due." in capsys.readouterr().out
    Reminder.objects.create(
        owner=user, summary="x", due_at=timezone.now() - dt.timedelta(minutes=5)
    )
    call_command("send_due_reminders")
    assert "1 reminders due, 0 deliveries" in capsys.readouterr().out
