"""A document speaks its own language, and a message speaks the reader's (#223).

Nothing in the code ever switched translation away from the request's language, so the two
places where the request is the wrong answer both got it wrong: a document, which is read by
an employer and declares a language of its own, and a notification, which is read by the
person it is addressed to and may be sent from a scheduler with no request at all.

These tests run the interface in one language and assert the other. They need compiled
catalogues to see a real translation, and the suite runs without them, so they assert the
*mechanism* — which language is active while the words are made — rather than French words.
That is the thing that was broken: the words were always made under the wrong language.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import formats, timezone, translation

from postulo.applications.models import Application, Reminder, Status
from postulo.documents import rendering
from postulo.documents.models import CV, CoverLetter, DocumentKind, UploadedDocument
from postulo.documents.stores import metadata_for
from postulo.jobs.models import Company, JobPosting
from postulo.notifications.base import Notification
from postulo.notifications.service import language_for, notify

pytestmark = pytest.mark.django_db


@pytest.fixture
def french_cv(user):
    return CV.objects.create(owner=user, name="Backend, English", language="fr-fr")


def active_language_seen_by(monkeypatch, target: str) -> list[str]:
    """Record the language active each time the renderer builds a page."""
    seen: list[str] = []
    real = rendering.render_to_string

    def watching(*args, **kwargs):
        seen.append(translation.get_language() or "")
        return real(*args, **kwargs)

    monkeypatch.setattr(rendering, "render_to_string", watching)
    return seen


# ------------------------------------------------------------------- the document


def test_a_cv_is_rendered_in_its_own_language_not_the_reader_s(user, french_cv, monkeypatch):
    seen = active_language_seen_by(monkeypatch, "cv")

    with translation.override("en-gb"):
        rendering.render_cv_html(french_cv)

    assert seen == ["fr-fr"], "the CV says French, so the page around it is French"


def test_a_letter_is_rendered_in_its_own_language(user, monkeypatch):
    letter = CoverLetter.objects.create(
        owner=user, name="Standard", subject="Bonjour", body="{{ date }}", language="fr-fr"
    )
    seen = active_language_seen_by(monkeypatch, "letter")

    with translation.override("en-gb"):
        rendering.render_letter_html(letter)

    assert seen == ["fr-fr"]


def test_the_date_placeholder_follows_the_letter_and_not_the_c_locale(user):
    """It was built with `strftime("%B")`, which names the month in English whatever is set."""
    letter = CoverLetter.objects.create(
        owner=user, name="Standard", subject="x", body="{{ date }}", language="fr-fr"
    )

    with translation.override("en-gb"):
        text = rendering.letter_text(letter)

    with translation.override("fr-fr"):
        expected = formats.date_format(timezone.localdate(), "j F Y")

    assert text.endswith(expected), "the date is written the way the letter's language writes it"


def test_a_document_with_no_language_of_its_own_follows_its_owner(user, monkeypatch):
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    cv = CV.objects.create(owner=user, name="Sem nome")
    seen = active_language_seen_by(monkeypatch, "cv")

    with translation.override("en-gb"):
        rendering.render_cv_html(cv)

    assert seen == ["pt-pt"]


# ------------------------------------------------------ what the employer's viewer shows


def test_the_title_names_the_person_and_not_the_private_filing_name(user, french_cv):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])

    title = rendering.document_title(french_cv)

    assert "Alex Morgan" in title
    assert "Backend, English" not in title, "that name is for the shelf, not for the employer"


def test_a_cv_with_nobody_named_keeps_something_to_show(user, french_cv):
    """Falling back rather than a PDF whose title bar is empty."""
    assert rendering.document_title(french_cv)


# ---------------------------------------------------------------- the store's label


def test_a_render_is_filed_under_the_document_s_language(user, french_cv):
    user.profile.language = "en-gb"
    user.profile.save(update_fields=["language"])
    document = rendering.snapshot_cv(french_cv, backend=_FakeBackend())

    assert metadata_for(document).language == "fr-fr", "the CV is French whoever owns it"


def test_an_upload_has_no_language_of_its_own_so_it_follows_the_person(user):
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    upload = UploadedDocument(owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE)
    upload.file.save("diploma.pdf", _a_file(), save=False)
    upload.save()

    assert metadata_for(upload).language == "pt-pt"


# ------------------------------------------------------------------ the message


def test_a_notification_is_worded_in_the_language_the_reader_chose(user, monkeypatch):
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    seen = []

    def building() -> Notification:
        seen.append(translation.get_language() or "")
        return Notification(event="reminder_due", title="x", body="", url="/")

    with translation.override("en-gb"):
        notify(user, building)

    assert seen == ["pt-pt"], "built in their language, not in the sender's"


def test_a_reminder_from_the_scheduler_speaks_to_the_person_it_is_for(user, monkeypatch):
    """The scheduler has no request at all, so this used to be the instance default."""
    from postulo.notifications.management.commands import send_due_reminders

    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    Reminder.objects.create(
        owner=user,
        application=application,
        summary="Chase them",
        due_at=timezone.now() - dt.timedelta(hours=1),
    )
    seen = []
    from postulo.notifications import service

    def watching(user, notification):
        seen.append(translation.get_language() or "")
        return 0

    # Below `notify`, so the real override is exercised and the message really is built
    # inside it -- stubbing `notify` itself would have tested nothing but the stub.
    monkeypatch.setattr(service, "_deliver", watching)

    with translation.override("en-gb"):
        send_due_reminders.announce_due_reminders()

    assert seen == ["pt-pt"], "worded for the person it is addressed to"


def test_the_language_of_a_person_who_has_chosen_nothing_is_the_instance_default(user):
    user.profile.language = ""
    user.profile.save(update_fields=["language"])

    assert language_for(user), "never empty: an override needs something to switch to"


# ------------------------------------------------------------------------- helpers


class _FakeBackend:
    name = "fake"

    def is_available(self):
        return True

    def render(self, html):
        return b"%PDF-1.7 fake"


def _a_file():
    from django.core.files.base import ContentFile

    return ContentFile(b"%PDF-1.7 diploma")
