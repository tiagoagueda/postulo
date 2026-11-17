"""What a notification may contain (#1259).

A notification that reaches a person is a line on a lock screen, a push through a browser
maker's service, a message on a public ntfy topic: a title, a few short lines, and a link
back to this instance. Never the text of a document, never a contact's details.

No event carries either today, but three bodies are a person's own free text with no bound
of their own: the note typed with a status change, an offer's terms, and the note a plugin
puts on a reminder it placed. The core cuts every body to `BODY_LIMIT` before a notifier
that reaches a person sees it; a machine's webhook is the person's own receiver and keeps
the whole text. Each event is built here by the code that builds it for real, with a
document and a contact beside the application, and every message is held to the rule.
"""

from __future__ import annotations

import datetime as dt
from unittest import mock

import pytest
from django.utils import timezone

from postulo.applications.models import Application, Reminder, Status
from postulo.documents.models import UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.notifications import base, slow
from postulo.notifications.base import BODY_LIMIT, EVENTS, Notification
from postulo.notifications.service import notify
from postulo.plugins.email import EmailNotifier
from postulo.plugins.models import Connection
from postulo.plugins.webhook import WebhookNotifier

pytestmark = pytest.mark.django_db

#: Words that belong to a document and a contact, and to nothing a notification says.
DOCUMENT_TEXT = "Confidential-cover-letter-wording"
CONTACT_EMAIL = "cave.johnson@aperture.example"
CONTACT_NAME = "Cave Johnson Private"

#: A person's own free text, far past the limit, in a script with spaces and in one without.
LONG_NOTE = "They asked about my salary history and I said " + "nothing more " * 200
LONG_TERMS = "年薪" * 800


@pytest.fixture
def sent(user, monkeypatch):
    """Every message the person's e-mail notifier is handed, with every event switched on."""
    from allauth.account.models import EmailAddress

    EmailAddress.objects.get_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=True,
        config={"to": user.email, **{f"event_{event}": True for event in EVENTS}},
    )
    handed: list[Notification] = []
    monkeypatch.setattr(
        EmailNotifier, "send", lambda self, notification, config, user: handed.append(notification)
    )
    return handed


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    Contact.objects.create(owner=user, company=company, name=CONTACT_NAME, email=CONTACT_EMAIL)
    UploadedDocument.objects.create(owner=user, title=DOCUMENT_TEXT, notes=DOCUMENT_TEXT)
    return application


def held_to_the_rule(notification: Notification) -> None:
    assert len(notification.body) <= BODY_LIMIT, notification.event
    for text in (notification.title, notification.body, notification.url):
        for secret in (DOCUMENT_TEXT, CONTACT_EMAIL, CONTACT_NAME):
            assert secret not in text, f"{notification.event} carried {secret!r}"


def test_a_status_change_with_a_long_note_reaches_a_person_cut(
    user, application, sent, django_capture_on_commit_callbacks
):
    from postulo.applications.services import change_status

    with (
        mock.patch("postulo.core.errands.send") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        change_status(application, Status.INTERVIEWING, note=LONG_NOTE)
    payload = queued.call_args.kwargs

    notify(user, lambda: slow.BUILDERS["status_changed"](payload))

    (notification,) = sent
    held_to_the_rule(notification)
    assert notification.body.startswith("They asked about my salary history")
    assert notification.body.endswith("…"), "a cut body says it goes on"
    assert not notification.body[:-1].endswith(" "), "cut at a word, not inside one"


def test_an_offers_long_terms_reach_a_person_cut_even_without_spaces(user, application, sent):
    payload = {
        "offer_id": 1,
        "event_id": 2,
        "application_id": application.pk,
        "role": "Test Engineer",
        "company": "Aperture Science",
        "terms": LONG_TERMS,
    }

    notify(user, lambda: slow.BUILDERS["offer_recorded"](payload))

    (notification,) = sent
    held_to_the_rule(notification)
    assert len(notification.body) == BODY_LIMIT, "a script without spaces is cut at the limit"


def test_a_plugins_reminder_with_a_long_note_reaches_a_person_cut(user, application, sent):
    from postulo.notifications.management.commands.send_due_reminders import (
        announce_due_reminders,
    )

    Reminder.objects.create(
        owner=user,
        summary="Renew the registration",
        due_at=timezone.now() - dt.timedelta(minutes=1),
        source="francetravail",
        note=LONG_NOTE,
    )

    announce_due_reminders()

    (notification,) = sent
    held_to_the_rule(notification)


def test_every_other_event_is_held_to_the_rule(user, application, sent):
    """The events whose bodies are Postulo's own words: short already, and kept so."""
    from postulo.applications import quiet
    from postulo.jobs import closing, sharing

    now = timezone.now()
    application.last_activity_at = now - dt.timedelta(days=30)
    posting = application.posting
    posting.closes_at = now.date() + dt.timedelta(days=2)
    builders = [
        lambda: slow.BUILDERS["capture_received"](
            {"capture_id": 1, "title": "Research Engineer", "where": "Lyon"}
        ),
        lambda: slow.BUILDERS["capture_batch"](
            {"capture_id": 1, "count": 40, "host": "example.com", "title": "Research Engineer"}
        ),
        lambda: slow.BUILDERS["interview_scheduled"](
            {
                "interview_id": 1,
                "application_id": application.pk,
                "role": "Test Engineer",
                "company": "Aperture Science",
                "starts_at": now.isoformat(),
            }
        ),
        lambda: quiet._announcement([application] * 7, now),
        lambda: closing._announcement([posting] * 7, now.date()),
    ]
    for build in builders:
        notify(user, build)
    sharing.tell_gone(user, "Aperture Science")

    assert {notification.event for notification in sent} >= {
        "capture_received",
        "interview_scheduled",
        "went_quiet",
        "posting_closing",
        "share_ended",
    }
    for notification in sent:
        held_to_the_rule(notification)


def test_a_body_at_the_limit_is_left_as_it_is():
    exact = Notification(event="reminder_due", title="x", body="a" * BODY_LIMIT)
    assert exact.brief() is exact


def test_a_machines_webhook_keeps_the_whole_text(user, monkeypatch):
    """The webhook is the person's own receiver: nothing is cut for it (#1259)."""
    assert getattr(WebhookNotifier, "delivers_later", False)
    handed: list[Notification] = []
    monkeypatch.setattr(
        WebhookNotifier,
        "send",
        lambda self, notification, config, user: handed.append(notification),
    )
    Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="webhook",
        label="My automation",
        enabled=True,
        config={"url": "https://hooks.example.net/in", "event_status_changed": True},
    )

    notify(user, Notification(event="status_changed", title="Moved", body=LONG_NOTE))

    (notification,) = handed
    assert notification.body == LONG_NOTE


def test_the_limit_is_the_one_the_maintainer_chose():
    assert base.BODY_LIMIT == 500
