"""Notifications: events, the dispatcher, the built-in email notifier, and the scheduler."""

import datetime as dt
import re

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
from postulo.notifications.email import EmailNotifier
from postulo.notifications.management.commands.send_due_reminders import announce_due_reminders
from postulo.notifications.service import notify
from postulo.plugins import registry
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

PAGE = """<html><head><title>Job</title>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting",
"title":"Research Engineer","hiringOrganization":{"@type":"Organization","name":"Black Mesa"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Lyon"}}}</script></head></html>"""


def email_connection(user, to="me@example.org", *, enabled=True, **config):
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
            "plugin_to": "me@example.org",
            "plugin_event_reminder_due": "on",
            # capture_received left unticked
        },
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert connection.config == {
        "to": "me@example.org",
        "event_reminder_due": True,
        "event_capture_received": False,
    }


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


def test_the_email_notifier_tests_itself_and_falls_back_to_the_owner(user):
    plugin = EmailNotifier()
    assert plugin.test({}).ok is False
    result = plugin.test({"to": "me@example.org"})
    assert result.ok and "me@example.org" in result.message
    assert mail.outbox[-1].to == ["me@example.org"]

    plugin.send(Notification(event="reminder_due", title="Ping"), {}, user)
    assert mail.outbox[-1].to == [user.email], "no address given: the owner's"


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
