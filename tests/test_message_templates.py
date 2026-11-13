"""Messages written from templates and sent from your own address (#1094).

A job search means writing the same few messages many times. A template is a subject and a
body with the letter's placeholders; it is written to an application, edited, and then sent
from the person's own outbox, copied, or said to have been sent -- and the timeline is told
whichever way it went, but only once something has.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.urls import reverse

from postulo.applications.models import (
    Application,
    EventKind,
    Interview,
    InterviewKind,
    Status,
)
from postulo.core import correspondence, export, importer
from postulo.documents import message_templates
from postulo.documents.models import MessagePurpose, MessageTemplate
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa", email="jobs@blackmesa.example")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Research Engineer", location="Nevada"
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture
def chell(user, application):
    contact = Contact.objects.create(owner=user, name="Chell", email="chell@example.net")
    application.contact = contact
    application.save(update_fields=["contact"])
    return contact


@pytest.fixture
def follow_up(user):
    return MessageTemplate.objects.create(
        owner=user,
        name="Nudge",
        purpose=MessagePurpose.FOLLOW_UP,
        subject="About {{ role }}",
        body="Dear {{ contact }}, any news from {{ company }}? {{ name }}",
    )


class _Server:
    """An outbox plugin that keeps what it was asked to send, or refuses to."""

    name = "own-mail"

    def __init__(self, refuses=None):
        self.sent = []
        self.refuses = refuses

    def send(self, message, config):
        if self.refuses:
            raise self.refuses
        self.sent.append(message)
        return 1


@pytest.fixture
def server(monkeypatch, user):
    from postulo.plugins.models import Connection

    Connection.objects.create(
        owner=user,
        kind="outbox",
        plugin="own-mail",
        label="Mine",
        enabled=True,
        config={"from_address": "alex@example.org", "host": "mail.example.org"},
    )
    plugin = _Server()
    monkeypatch.setattr(correspondence, "outbox_for", lambda person: plugin)
    return plugin


def write_url(application, **query):
    url = reverse("documents:message_write", args=[application.pk])
    return f"{url}?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else url


def post(client, application, action, **over):
    fields = {
        "recipient": "hr@example.net",
        "subject": "Hello",
        "body": "Words.",
        "purpose": "other",
        "action": action,
        **over,
    }
    return client.post(write_url(application), fields)


# ----------------------------------------------------------------------- filling it in


def test_a_template_is_filled_in_for_an_application(user, application, chell, follow_up):
    subject, body = message_templates.written_for(follow_up, application)

    assert subject == "About Research Engineer"
    assert body.startswith("Dear Chell, any news from Black Mesa?")


def test_the_recipient_is_the_contact_else_the_company(user, application):
    assert message_templates.recipient_for(application) == "jobs@blackmesa.example"

    contact = Contact.objects.create(owner=user, name="Chell", email="chell@example.net")
    application.contact = contact
    assert message_templates.recipient_for(application) == "chell@example.net"


def test_the_person_met_at_an_interview_is_who_the_thank_you_goes_to(user, application, chell):
    import datetime as dt

    from django.utils import timezone

    met = Contact.objects.create(owner=user, name="GLaDOS", email="glados@example.net")
    interview = Interview.objects.create(
        owner=user,
        application=application,
        kind=InterviewKind.VIDEO,
        starts_at=timezone.now(),
        ends_at=timezone.now() + dt.timedelta(hours=1),
    )
    interview.contacts.add(met)

    assert message_templates.recipient_for(application, interview) == "glados@example.net"


def test_the_starters_are_added_once_and_only_when_asked(client, user):
    client.force_login(user)
    assert not MessageTemplate.objects.exists()

    client.post(reverse("documents:message_starters"))
    client.post(reverse("documents:message_starters"))

    purposes = set(MessageTemplate.objects.values_list("purpose", flat=True))
    assert purposes == {"follow_up", "thank_you", "withdrawal", "availability"}
    assert MessageTemplate.objects.count() == 4, "pressing it twice adds nothing"


def test_a_starter_is_added_for_a_purpose_the_person_has_no_message_for(user, follow_up):
    added = message_templates.add_starters(user)

    assert {template.purpose for template in added} == {"thank_you", "withdrawal", "availability"}
    assert MessageTemplate.objects.for_user(user).get(purpose="follow_up") == follow_up


# ----------------------------------------------------------------------- the page


def test_the_page_starts_from_the_template_for_the_purpose(
    client, user, application, chell, follow_up
):
    client.force_login(user)

    page = client.get(write_url(application, purpose="follow_up")).content.decode()

    assert 'value="About Research Engineer"' in page
    assert "any news from Black Mesa" in page
    assert 'value="chell@example.net"' in page


def test_the_page_starts_from_the_template_chosen(client, user, application, follow_up):
    other = MessageTemplate.objects.create(
        owner=user, name="Thanks", purpose="thank_you", subject="Thanks", body="Thank you."
    )
    client.force_login(user)

    page = client.get(write_url(application, template=other.pk)).content.decode()

    assert 'value="Thanks"' in page and "Thank you." in page


def test_a_template_of_somebody_else_is_not_used(client, user, other_user, application):
    theirs = MessageTemplate.objects.create(
        owner=other_user, name="Theirs", subject="Secret subject", body="Secret words"
    )
    client.force_login(user)

    page = client.get(write_url(application, template=theirs.pk)).content.decode()

    assert "Secret" not in page


def test_an_application_of_somebody_else_is_not_found(client, user, other_user, application):
    client.force_login(other_user)

    assert client.get(write_url(application)).status_code == 404
    assert post(client, application, "record").status_code == 404
    assert not application.events.filter(kind=EventKind.EMAIL_SENT).exists()


def test_an_interview_of_another_application_is_not_attached(client, user, application, chell):
    import datetime as dt

    from django.utils import timezone

    posting = JobPosting.objects.create(
        owner=user, company=application.posting.company, title="Other"
    )
    other_application = Application.objects.create(
        owner=user, posting=posting, status=Status.APPLIED
    )
    elsewhere = Interview.objects.create(
        owner=user,
        application=other_application,
        starts_at=timezone.now(),
        ends_at=timezone.now() + dt.timedelta(hours=1),
    )
    client.force_login(user)

    post(client, application, "record", interview=elsewhere.pk)

    assert application.events.get(kind=EventKind.EMAIL_SENT).interview_id is None


def test_the_page_offers_to_send_only_to_somebody_with_an_outbox(client, user, application):
    client.force_login(user)

    page = client.get(write_url(application)).content.decode()

    assert 'value="send"' not in page
    assert 'value="copy"' in page and 'value="record"' in page


def test_the_page_offers_to_send_when_there_is_an_outbox(client, user, application, server):
    client.force_login(user)

    assert 'value="send"' in client.get(write_url(application)).content.decode()


# ----------------------------------------------------------------------- what happens to it


def test_sending_mails_it_from_the_outbox_and_records_it(client, user, application, server):
    client.force_login(user)

    response = post(client, application, "send", purpose="thank_you")

    assert response.status_code == 302
    (message,) = server.sent
    assert message.from_email == "alex@example.org"
    assert message.to == ["hr@example.net"]
    assert (message.subject, message.body) == ("Hello", "Words.")
    event = application.events.get(kind=EventKind.EMAIL_SENT)
    assert event.summary == "Hello"
    assert "hr@example.net" in event.body and "Words." in event.body


def test_a_follow_up_is_recorded_as_followed_up(client, user, application, server):
    client.force_login(user)

    post(client, application, "send", purpose="follow_up")

    assert application.events.filter(kind=EventKind.FOLLOW_UP, summary="Hello").exists()


def test_a_refused_send_records_nothing(client, user, application, server):
    server.refuses = OSError("down")
    client.force_login(user)

    response = post(client, application, "send")

    assert response.status_code == 200
    assert "nothing was sent or recorded" in response.content.decode()
    assert not application.events.filter(kind=EventKind.EMAIL_SENT).exists()


def test_sending_needs_somebody_to_send_it_to(client, user, application, server):
    client.force_login(user)

    response = post(client, application, "send", recipient="")

    assert response.status_code == 200
    assert not server.sent
    assert not application.events.filter(kind=EventKind.EMAIL_SENT).exists()


def test_without_an_outbox_a_send_is_refused_and_records_nothing(client, user, application):
    client.force_login(user)

    response = post(client, application, "send")

    assert response.status_code == 200
    assert "no outbox" in response.content.decode()
    assert not application.events.filter(kind=EventKind.EMAIL_SENT).exists()


def test_recording_as_sent_works_without_an_outbox(client, user, application):
    client.force_login(user)

    response = post(client, application, "record", recipient="")

    assert response.status_code == 302
    event = application.events.get(kind=EventKind.EMAIL_SENT)
    assert event.summary == "Hello" and event.body == "Words."


def test_copying_shows_the_words_and_records_nothing(client, user, application):
    client.force_login(user)

    response = post(client, application, "copy", subject="A subject", body="The words")

    page = response.content.decode()
    assert response.status_code == 200
    assert "data-copy-source" in page
    assert "A subject\n\nThe words" in page
    assert (
        not application.events.exists()
        or not application.events.filter(kind=EventKind.EMAIL_SENT).exists()
    )


def test_a_subject_is_one_line(client, user, application, server):
    client.force_login(user)

    response = post(client, application, "send", subject="Hello\nBcc: x@example.net")

    assert response.status_code == 200
    assert not server.sent


# ----------------------------------------------------------------------- where it is offered


def test_the_quiet_action_offers_the_follow_up(client, user, application):
    """An application that has gone quiet links to a message written from the follow-up."""
    from datetime import timedelta

    from django.utils import timezone

    from postulo.applications.services import change_status

    Application.objects.filter(pk=application.pk).update(status=Status.DRAFT)
    application.refresh_from_db()
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - timedelta(days=30))
    client.force_login(user)

    page = client.get(reverse("core:home")).content.decode()

    assert f"/documents/applications/{application.pk}/message/?purpose=follow_up" in page


def test_an_application_links_to_writing_a_message(client, user, application):
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert reverse("documents:message_write", args=[application.pk]) in page


# ----------------------------------------------------------------------- the templates


def test_a_template_is_made_changed_and_deleted(client, user):
    client.force_login(user)

    made = client.post(
        reverse("documents:message_create"),
        {
            "name": "Mine",
            "purpose": "withdrawal",
            "subject": "Bye",
            "body": "Goodbye.",
            "language": "",
        },
    )
    template = MessageTemplate.objects.get(owner=user)
    changed = client.post(
        reverse("documents:message_update", args=[template.pk]),
        {"name": "Mine", "purpose": "other", "subject": "Bye", "body": "Changed.", "language": ""},
    )
    listed = client.get(reverse("documents:message_list")).content.decode()
    gone = client.post(reverse("documents:message_delete", args=[template.pk]))

    assert made.status_code == changed.status_code == gone.status_code == 302
    assert "Mine" in listed
    assert not MessageTemplate.objects.exists()


def test_a_template_of_somebody_else_is_not_reachable(client, user, other_user):
    theirs = MessageTemplate.objects.create(owner=other_user, name="Theirs", body="x")
    client.force_login(user)

    assert client.get(reverse("documents:message_update", args=[theirs.pk])).status_code == 404
    assert client.post(reverse("documents:message_delete", args=[theirs.pk])).status_code == 404
    assert "Theirs" not in client.get(reverse("documents:message_list")).content.decode()
    assert MessageTemplate.objects.filter(pk=theirs.pk).exists()


def test_a_subject_with_a_line_break_is_refused_in_a_template(client, user):
    client.force_login(user)

    response = client.post(
        reverse("documents:message_create"),
        {"name": "Mine", "purpose": "other", "subject": "a\nb", "body": "x", "language": ""},
    )

    assert response.status_code == 200
    assert not MessageTemplate.objects.exists()


# ----------------------------------------------------------------------- the archive


def test_the_archive_carries_them_and_restores_them(user, other_user, follow_up):
    follow_up.language = "pt-PT"
    follow_up.save()

    buffer = export.write_archive(user)
    archive = zipfile.ZipFile(io.BytesIO(buffer.read()) if hasattr(buffer, "read") else buffer)
    report = importer.load(other_user, archive)

    assert export.FORMAT_VERSION >= 85
    assert export.counts(user)["message_templates"] == 1
    assert report.message_templates == 1
    restored = MessageTemplate.objects.get(owner=other_user)
    assert (
        restored.name,
        restored.purpose,
        restored.subject,
        restored.body,
        restored.language,
    ) == (
        follow_up.name,
        "follow_up",
        follow_up.subject,
        follow_up.body,
        "pt-PT",
    )


def test_an_archive_from_before_them_still_loads(user, other_user, follow_up):
    document = export.build_document(user)
    del document["documents"]["message_templates"]
    del document["counts"]["message_templates"]
    document["postulo"]["format"] = 84

    import json

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        written.writestr(export.MANIFEST_NAME, json.dumps(document))
    report = importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    assert report.message_templates == 0
    assert not MessageTemplate.objects.filter(owner=other_user).exists()


def test_a_purpose_or_subject_the_archive_gets_wrong_is_held_to_the_column(user, other_user):
    import json

    document = export.build_document(user)
    document["documents"]["message_templates"] = [
        {
            "id": 1,
            "name": "Odd",
            "purpose": "shout",
            "subject": "a\nb",
            "body": "x",
            "language": "??",
        },
        {"id": 2, "name": "", "purpose": "other", "subject": "", "body": "x"},
        "not an entry",
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        written.writestr(export.MANIFEST_NAME, json.dumps(document))

    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    odd = MessageTemplate.objects.get(owner=other_user)
    assert (odd.purpose, odd.subject, odd.language) == ("other", "a b", "")


def test_deleting_the_account_takes_them(user, follow_up):
    user.delete()

    assert not MessageTemplate.objects.exists()
