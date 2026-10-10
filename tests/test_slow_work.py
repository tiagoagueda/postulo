"""The five things that were moved off the request, and what stayed behind (#247).

Each of these used to be done while somebody watched. What is checked here is that the work
still happens and still produces the same record, that the parts which deliberately did *not*
move are still where they were -- the rate limit, the duplicate question, the report's GET --
and that the export archive is looked after rather than left on disk.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.core import errands
from postulo.core.models import Errand, ErrandState, ExportArchive
from postulo.jobs.models import Capture, CaptureStatus, Company

pytestmark = pytest.mark.django_db


@pytest.fixture
def drawn(monkeypatch):
    """A renderer that draws nothing, so these run where WeasyPrint is not installed."""
    monkeypatch.setattr(
        "postulo.documents.rendering.html_to_pdf", lambda html, backend=None: b"%PDF-1.7 fake"
    )


def finished(response, client):
    """Follow a press to the page that watches it, and hand back the errand."""
    assert response.status_code == 302, f"expected the watching page, got {response.status_code}"
    assert "/working/" in response.url, response.url
    return Errand.objects.get(pk=response.url.rstrip("/").split("/")[-1])


# ------------------------------------------------------------------ capturing a page


def test_capturing_sends_the_fetch_off_and_still_leaves_a_capture(client, user, monkeypatch):
    from postulo.plugins.base import JobPostingData

    class Source:
        name = "test"
        version = "1"

    monkeypatch.setattr(
        "postulo.plugins.registry.parse_page",
        lambda url, html, **_: (JobPostingData(title="Tester", company_name="Aperture"), Source()),
    )
    client.force_login(user)

    response = client.post(
        reverse("jobs:capture_create"),
        {"url": "https://example.org/job", "html": "<html>a posting</html>"},
    )

    errand = finished(response, client)
    assert errand.kind == "capture" and errand.state == ErrandState.DONE
    capture = Capture.objects.for_user(user).get()
    assert capture.status == CaptureStatus.PENDING
    assert reverse("jobs:capture_review", args=[capture.pk]) == errand.url


def test_the_rate_limit_is_still_spent_by_asking_not_by_doing(client, user, monkeypatch):
    """Counting it in the worker would let one permit queue a thousand fetches."""
    from postulo.core import throttle

    spent: list[int] = []

    def count(person):
        spent.append(person.pk)
        raise throttle.TooOften(throttle.Rate(times=5, seconds=60), 60)

    monkeypatch.setattr("postulo.core.throttle.capture", count)
    client.force_login(user)

    response = client.post(reverse("jobs:capture_create"), {"url": "https://example.org/job"})

    assert response.status_code == 200, "refused on the form, before any errand exists"
    assert spent == [user.pk]
    assert not Errand.objects.exists(), "refused before any errand exists"


def test_the_duplicate_question_is_still_asked_before_the_work(client, user):
    """A question asked after the fetch is a question asked too late."""
    from postulo.jobs.models import JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    JobPosting.objects.create(
        owner=user, company=company, title="Tester", url="https://example.org/job"
    )
    client.force_login(user)

    response = client.post(reverse("jobs:capture_create"), {"url": "https://example.org/job"})

    assert response.status_code == 200, "asked, not fetched"
    assert Capture.objects.for_user(user).count() == 0


# ---------------------------------------------------------------------- a logo


def test_finding_a_logo_is_sent_off_and_removing_one_is_not(client, user, monkeypatch):
    """Removing is a database write; the other two read a website and up to six images."""
    company = Company.objects.create(owner=user, name="Aperture", website="https://example.org")
    monkeypatch.setattr(
        "postulo.jobs.logos.find_on_website", lambda c: "https://example.org/logo.png"
    )
    client.force_login(user)

    sent = client.post(reverse("jobs:company_logo_action", args=[company.pk, "website"]))
    errand = finished(sent, client)
    assert errand.kind == "logo" and errand.state == ErrandState.DONE
    assert errand.subject == company

    removed = client.post(reverse("jobs:company_logo_action", args=[company.pk, "remove"]))
    assert removed.status_code == 302 and "/working/" not in removed.url


def test_a_company_deleted_while_the_fetch_waited_is_said_plainly(user, settings):
    settings.POSTULO_BACKGROUND_WORK = True
    company = Company.objects.create(owner=user, name="Aperture")
    errand = errands.send("logo", user, subject=company, company_id=company.pk, action="website")
    company.delete()

    errands.perform(errand.pk)

    errand.refresh_from_db()
    assert errand.state == ErrandState.FAILED
    assert "no longer here" in errand.message


# ------------------------------------------------------------------ the export archive


def test_an_export_becomes_a_file_with_an_owner_and_an_expiry(client, user, settings):
    settings.POSTULO_EXPORT_KEEP_HOURS = 6
    client.force_login(user)

    errand = finished(client.post(reverse("core:export_download")), client)

    archive = ExportArchive.objects.for_user(user).get()
    assert archive.file and archive.size > 0
    assert archive.filename.endswith(".zip")
    assert dt.timedelta(hours=5) < archive.expires_at - timezone.now() < dt.timedelta(hours=7)
    assert errand.url == reverse("core:export_archive", args=[archive.pk])


def test_an_archive_is_served_to_its_owner_and_nobody_else(client, user, other_user):
    client.force_login(user)
    client.post(reverse("core:export_download"))
    archive = ExportArchive.objects.for_user(user).get()

    client.force_login(other_user)
    assert client.get(reverse("core:export_archive", args=[archive.pk])).status_code == 404


def test_an_expired_archive_says_so_rather_than_handing_over_nothing(client, user):
    client.force_login(user)
    client.post(reverse("core:export_download"))
    archive = ExportArchive.objects.for_user(user).get()
    ExportArchive.objects.filter(pk=archive.pk).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )

    assert client.get(reverse("core:export_archive", args=[archive.pk])).status_code == 404


def test_the_reaper_takes_the_bytes_with_the_row(client, user, django_capture_on_commit_callbacks):
    """One per export, each the size of a whole job search: leaving them is not an option."""
    from django.core.files.storage import default_storage

    from postulo.core.slow import reap_archives

    client.force_login(user)
    client.post(reverse("core:export_download"))
    archive = ExportArchive.objects.for_user(user).get()
    path = archive.file.name
    assert default_storage.exists(path)

    assert reap_archives() == 0, "not yet: it has not expired"

    ExportArchive.objects.filter(pk=archive.pk).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert reap_archives() == 1

    assert not ExportArchive.objects.filter(pk=archive.pk).exists()
    assert not default_storage.exists(path), "the file goes with the row"


def test_deleting_archives_by_queryset_takes_their_files_too(
    client, user, django_capture_on_commit_callbacks
):
    """No `delete()` of the model runs for a queryset, a cascade or the admin (#355)."""
    from django.core.files.storage import default_storage

    client.force_login(user)
    client.post(reverse("core:export_download"))
    path = ExportArchive.objects.for_user(user).get().file.name
    assert default_storage.exists(path)

    with django_capture_on_commit_callbacks(execute=True):
        ExportArchive.objects.for_user(user).delete()

    assert not default_storage.exists(path)


# ------------------------------------------------------------------ the report


def test_the_report_get_is_still_a_draft_served_from_the_request(client, user, drawn):
    """Opening the address hands back a PDF and files nothing; only the press is an errand."""
    from postulo.documents.models import RenderedDocument

    client.force_login(user)

    response = client.get(reverse("applications:report_pdf"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert not RenderedDocument.objects.for_user(user).exists()
    response.close()


# ------------------------------------------------------------------ notifications


def test_the_api_stops_waiting_on_every_notifier_before_it_answers(user, monkeypatch, settings):
    """A batch of forty used to wait on each connection's timeout, forty times over."""
    settings.POSTULO_BACKGROUND_WORK = True
    told: list[str] = []
    monkeypatch.setattr(
        "postulo.notifications.service.notify",
        lambda owner, notification: (
            told.append((notification() if callable(notification) else notification).title) or 1
        ),
    )

    capture = Capture.objects.create(
        owner=user, url="https://example.org/j", data={"title": "Tester"}
    )
    errand = errands.send(
        "notify",
        user,
        subject=capture,
        event="capture_received",
        capture_id=capture.pk,
        title="Tester",
        where="Aperture · Cambridge",
    )
    assert told == [], "nothing was delivered in the request"

    errands.perform(errand.pk)

    assert told == ["Captured: Tester"], "and the words are written at delivery"


def test_an_event_this_version_does_not_know_is_not_delivered_blank(user, monkeypatch):
    told: list[str] = []
    monkeypatch.setattr(
        "postulo.notifications.service.notify", lambda owner, notification: told.append(1) or 1
    )

    errand = errands.send("notify", user, event="from_the_future")

    assert errand.state == ErrandState.DONE
    assert told == [], "silence beats an empty message"


def test_a_stop_asked_for_during_a_task_ends_the_worker_after_it(user):
    """The loop around upstream's worker used to forget a SIGTERM and start another pass (#474)."""
    import signal

    from django.core.management import call_command

    from postulo.core.tasks import perform_errand

    saved = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}

    ran: list[int] = []

    @errands.handler("test_stop", working="Stopping…")
    def run(errand) -> dict:
        ran.append(errand.pk)
        signal.raise_signal(signal.SIGTERM)
        return {}

    try:
        first = Errand.objects.create(owner=user, kind="test_stop")
        second = Errand.objects.create(owner=user, kind="test_stop")
        perform_errand.enqueue(first.pk)
        perform_errand.enqueue(second.pk)

        call_command("work", "--every", "0.1")
    finally:
        errands.HANDLERS.pop("test_stop", None)
        for number, handler in saved.items():
            signal.signal(number, handler)

    assert ran == [first.pk], "it stopped after the task it was running"


def test_a_report_filed_by_the_worker_covers_the_owners_period_in_their_language(
    user, settings, monkeypatch
):
    """At 22:30 on 30 September in New York it is already October in Paris (#383)."""
    from unittest import mock

    from django.utils import translation

    from postulo.applications.models import Application, Status
    from postulo.applications.services import change_status
    from postulo.documents.models import RenderedDocument
    from postulo.jobs.models import JobPosting

    monkeypatch.setattr(
        "postulo.documents.rendering.html_to_pdf", lambda html, backend=None: b"%PDF-1.7 fake"
    )
    settings.TIME_ZONE = "Europe/Paris"
    user.profile.language = "fr-FR"
    user.profile.time_zone = "America/New_York"
    user.profile.save()
    moment = dt.datetime(2026, 10, 1, 2, 30, tzinfo=dt.UTC)
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=moment)
    Application.objects.filter(pk=application.pk).update(applied_at=moment)

    translation.activate("en-gb")
    timezone.deactivate()
    errand = Errand.objects.create(owner=user, kind="report_pdf", payload={"query": {}})
    with mock.patch("django.utils.timezone.now", return_value=moment):
        errands.perform(errand.pk)

    errand.refresh_from_db()
    assert errand.state == "done", errand.error
    filed = RenderedDocument.objects.for_user(user).get()
    assert "septembre 2026" in filed.title
    assert "Test Engineer" in filed.source_text, "the application is in the month the page shows"


def test_a_letter_frozen_by_the_worker_carries_the_owners_date_and_language(
    user, settings, monkeypatch
):
    """At 22:30 on 30 September in New York it is already October in Paris (#383)."""
    from unittest import mock

    from django.utils import translation

    from postulo.applications.models import Application, EventKind, Status
    from postulo.documents.models import CoverLetter, RenderedDocument
    from postulo.jobs.models import JobPosting

    class Drawn:
        name = "fake"
        install_hint = ""

        def is_available(self):
            return True

        def render(self, html):
            return b"%PDF-1.7 fake"

    monkeypatch.setattr("postulo.documents.pdf.get_pdf_backend", lambda name=None: Drawn())
    settings.TIME_ZONE = "Europe/Paris"
    user.profile.language = "fr-FR"
    user.profile.time_zone = "America/New_York"
    user.profile.save()
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    letter = CoverLetter.objects.create(owner=user, name="Cover", body="Written on {{ date }}.")

    translation.activate("en-gb")
    timezone.deactivate()
    errand = Errand.objects.create(
        owner=user,
        kind="sent_documents",
        payload={"application_id": application.pk, "letter_id": letter.pk},
    )
    moment = dt.datetime(2026, 10, 1, 2, 30, tzinfo=dt.UTC)
    with mock.patch("django.utils.timezone.now", return_value=moment):
        errands.perform(errand.pk)

    errand.refresh_from_db()
    assert errand.state == "done", errand.error
    filed = RenderedDocument.objects.for_user(user).get()
    assert "30 septembre 2026" in filed.source_text and "1 octobre" not in filed.source_text
    with translation.override("fr-FR"):
        sent_to = translation.gettext("%(role)s at %(company)s") % {
            "role": "Test Engineer",
            "company": "Aperture Science",
        }
        sent = translation.gettext("Documents sent")
    assert filed.sent_to == sent_to
    event = application.events.get(kind=EventKind.NOTE)
    assert event.summary == sent
