"""The five things that were moved off the request, and what stayed behind (#247).

Each of these used to be done while somebody watched. What is checked here is that the work
still happens and still produces the same record, that the parts which deliberately did *not*
move are still where they were -- the rate limit, the duplicate question, the report's GET --
and that the export archive is looked after rather than left on disk.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.core import errands, export
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


@pytest.fixture
def volumes(tmp_path, settings, monkeypatch):
    """A data volume and a system temporary directory of this test's own, both empty."""
    media = tmp_path / "media"
    temporary = tmp_path / "tmp"
    media.mkdir()
    temporary.mkdir()
    settings.MEDIA_ROOT = str(media)
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    return media, temporary


def files_in(directory: Path) -> list[str]:
    return sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    )


@pytest.fixture
def while_writing(monkeypatch):
    """Run something in the middle of an export: after some of the archive is written."""
    write_archive = export.write_archive
    during = []

    def interrupted(owner, target=None):
        target.write(b"PK half an archive")
        target.flush()
        for look in during:
            look(target)
        target.seek(0)
        target.truncate()
        return write_archive(owner, target)

    monkeypatch.setattr(export, "write_archive", interrupted)
    return during


def test_an_export_is_written_once_on_the_data_volume_and_moved_into_place(
    user, volumes, while_writing, monkeypatch
):
    """An export was built in the system's temporary directory, which the hardened compose
    file keeps in memory, and then copied into the store: every archive was written twice,
    and held in the worker's memory allowance while it was (#1125)."""
    from django.core.files.storage import filesystem

    from postulo.core import slow

    media, temporary = volumes
    seen = {}

    def look(target):
        seen["temporary"] = files_in(temporary)
        seen["media"] = files_in(media)
        seen["inode"] = os.stat(target.name).st_ino

    while_writing.append(look)
    moves, copies = [], []
    move = filesystem.file_move_safe
    monkeypatch.setattr(
        filesystem, "file_move_safe", lambda *a, **kw: moves.append(a[:2]) or move(*a, **kw)
    )
    monkeypatch.setattr(slow._Finished, "chunks", lambda self, size=None: copies.append(1) or [])

    errand = errands.send("export", user)

    assert errand.state == ErrandState.DONE, errand.message
    archive = ExportArchive.objects.for_user(user).get()
    assert seen["temporary"] == [], "nothing is built in the system's temporary directory"
    (scratch,) = seen["media"]
    folder, _slash, name = scratch.rpartition("/")
    assert folder == f"exports/{user.pk}", "beside the archives, in the person's own folder"
    assert name.startswith(".postulo-export-") and name.endswith(".partial")
    # Moved, not copied: the store renamed the very file that was written.
    assert len(moves) == 1 and copies == []
    assert os.stat(archive.file.path).st_ino == seen["inode"]
    # And nothing is left but the archive, which is whole.
    assert files_in(temporary) == []
    assert files_in(media) == [archive.file.name]
    assert archive.size == os.path.getsize(archive.file.path)
    with zipfile.ZipFile(archive.file.path) as written:
        assert written.testzip() is None and export.MANIFEST_NAME in written.namelist()


def test_an_export_that_fails_removes_what_it_had_written(user, volumes, monkeypatch):
    """The half-written archive is on the data volume now, where a failure must not leave
    it (#1125)."""
    media, temporary = volumes

    def fails(owner, target=None):
        target.write(b"PK half an archive")
        raise OSError("No space left on device")

    monkeypatch.setattr(export, "write_archive", fails)

    errand = errands.send("export", user)

    assert errand.state == ErrandState.FAILED
    assert not ExportArchive.objects.exists()
    assert files_in(media) == [] and files_in(temporary) == []


def test_a_scratch_file_is_left_alone_while_it_is_written_and_swept_once_it_is_old(
    user, volumes, while_writing, settings
):
    """A worker killed half way leaves its scratch file on the data volume, where nothing
    but the sweep of unnamed files would ever remove it (#1125). The sweep takes it once it
    is past the grace period, and never one an export is still writing."""
    from postulo.core import slow
    from postulo.documents import scrub

    media, _temporary = volumes
    settings.POSTULO_MEDIA_SWEEP_GRACE_HOURS = 1  # the shortest an operator can make it
    swept = []
    while_writing.append(lambda target: swept.append((scrub.sweep(), files_in(media))))

    errand = errands.send("export", user)

    assert errand.state == ErrandState.DONE, errand.message
    ((removed, there),) = swept
    assert removed == [] and len(there) == 1, "the sweep came round and left it"

    # The same file, as a killed worker leaves it: no export, no row, nobody to remove it.
    leftover = slow._scratch(f"exports/{user.pk}/")
    leftover.write_bytes(b"PK half an archive")
    assert scrub.sweep() == [], "inside the grace period"
    then = (timezone.now() - dt.timedelta(hours=2)).timestamp()
    os.utime(leftover, (then, then))
    assert scrub.sweep() == [f"exports/{user.pk}/{leftover.name}"]
    assert not leftover.exists()
    assert files_in(media) == [ExportArchive.objects.get().file.name], "and the archive stays"


def test_a_store_that_is_not_a_directory_is_still_handed_the_archive_to_copy(
    user, volumes, settings
):
    """Only a store on this machine can take a file by renaming it; any other reads it, from
    the temporary directory as before, and nothing is left there afterwards (#1125)."""
    media, temporary = volumes
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }

    errand = errands.send("export", user)

    assert errand.state == ErrandState.DONE, errand.message
    archive = ExportArchive.objects.for_user(user).get()
    with archive.file.open("rb") as kept, zipfile.ZipFile(kept) as written:
        assert written.testzip() is None and export.MANIFEST_NAME in written.namelist()
    assert archive.size == archive.file.size
    assert files_in(temporary) == [] and files_in(media) == []
    assert archive.file.storage.listdir(f"exports/{user.pk}")[1] == [], "and no scratch file"


def test_a_finished_file_can_be_read_by_a_store_that_reads_and_then_let_go(tmp_path):
    """What the store is handed is a file like any other to one that copies it (#1125)."""
    from postulo.core.slow import _Finished

    path = tmp_path / "whole.zip"
    path.write_bytes(b"PK" + b"x" * 200_000)
    whole = _Finished(path, "export.zip")

    assert whole.temporary_file_path() == str(path)
    assert whole.size == 200_002 and whole.name == "export.zip"
    assert b"".join(whole.chunks(64 * 1024)) == path.read_bytes()
    path.rename(tmp_path / "moved.zip")  # nothing holds it open after `chunks`
    (tmp_path / "moved.zip").rename(path)
    whole.seek(0)
    assert whole.read(2) == b"PK"
    whole.close()
    path.unlink()


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


@pytest.fixture
def signals_put_back():
    """The worker takes SIGINT and SIGTERM for itself; the next test gets them back."""
    import signal

    saved = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)}
    yield
    for number, handler in saved.items():
        signal.signal(number, handler)


def idle_passes(monkeypatch, passes: int, *, every: float = 2.0) -> list[int]:
    """Send the worker round an empty queue `passes` times; the passes that wrote a heartbeat.

    On a clock that only the loop's own wait moves, so three minutes of going round take no
    time at all and the counts do not depend on how busy the machine is.
    """
    import types

    from django.core.management import call_command

    from postulo.core import scheduler
    from postulo.core.management.commands import work

    class Enough(Exception):
        pass

    clock = types.SimpleNamespace(now=1000.0, waits=0)

    def wait(seconds: float) -> None:
        clock.waits += 1
        clock.now += seconds
        if clock.waits == passes:
            raise Enough

    monkeypatch.setattr(
        work, "time", types.SimpleNamespace(monotonic=lambda: clock.now, sleep=wait)
    )
    written: list[int] = []
    really = scheduler.worker_beat
    monkeypatch.setattr(
        scheduler, "worker_beat", lambda when=None: written.append(clock.waits + 1) or really(when)
    )
    with pytest.raises(Enough):
        call_command("work", "--every", str(every))
    return written


def test_an_idle_worker_writes_its_heartbeat_once_a_minute_not_every_pass(
    monkeypatch, signals_put_back
):
    """Every pass wrote the heartbeat file: 43,200 writes a day to say it was still there (#1117).

    Its readers allow five minutes, so once a minute tells them as much. Ninety passes two
    seconds apart are three minutes: three writes where there were ninety.
    """
    from postulo.core import scheduler

    scheduler.worker_heartbeat_path().unlink(missing_ok=True)

    written = idle_passes(monkeypatch, 90)

    assert written == [1, 31, 61], f"{len(written)} heartbeats in 90 passes"
    assert scheduler.worker_last_pass() is not None, "and what is written is still the file"


def test_a_worker_that_has_just_started_is_seen_at_once(monkeypatch, signals_put_back):
    """The first pass writes whatever the clock says, and so does each run made by cron (#1117)."""
    from django.core.management import call_command

    from postulo.core import scheduler

    scheduler.worker_heartbeat_path().unlink(missing_ok=True)
    assert idle_passes(monkeypatch, 1) == [1]
    assert scheduler.worker_last_pass() is not None

    scheduler.worker_heartbeat_path().unlink()
    call_command("work", "--once")
    assert scheduler.worker_last_pass() is not None, "one pass and stop still leaves its mark"


def test_what_watches_the_worker_waits_longer_than_it_takes_to_write():
    """A heartbeat a minute old is a worker going round, to everything that reads it (#1117).

    The page that watches slow work calls the worker silent after five minutes, and so does
    the container's healthcheck. Both have to stay well clear of the minute between writes.
    """
    import re
    from pathlib import Path

    from postulo.core import scheduler, views_errands
    from postulo.core.management.commands import work

    between = dt.timedelta(seconds=work.HEARTBEAT_EVERY)
    assert between == dt.timedelta(minutes=1)
    assert views_errands.WORKER_SILENT_AFTER >= 2 * between

    scheduler.worker_beat(timezone.now() - between - dt.timedelta(seconds=10))
    assert not views_errands.worker_silent(), "the oldest an idle worker's heartbeat gets"

    docker = Path(__file__).resolve().parent.parent / "docker"
    for name in ("compose.yml", "compose.postgres.yml"):
        (minutes,) = re.findall(
            r"worker-heartbeat -mmin -(\d+)", (docker / name).read_text(encoding="utf-8")
        )
        assert dt.timedelta(minutes=int(minutes)) >= 2 * between, name


def test_an_idle_worker_says_it_started_and_then_nothing(monkeypatch, caplog, signals_put_back):
    """An idle worker logged two lines every pass: 86,400 a day, about 14.5 MB (#1117).

    They were the library's, "Starting worker" and "No more tasks to run", and the kept log
    is 20 MB shared with the web process, whose errors they pushed out in a day and a half.
    The library is now heard from WARNING up, and the command says once that it came up.
    """
    import logging

    from postulo.core.management.commands import work

    with caplog.at_level(logging.INFO):
        idle_passes(monkeypatch, 5)

    theirs = [record.getMessage() for record in caplog.records if record.name == "django_tasks_db"]
    assert theirs == [], f"{len(theirs)} lines from the library in 5 idle passes"
    ours = [record for record in caplog.records if record.name == work.__name__]
    assert len(ours) == 1, "one line of Postulo's own, so the container's log shows it came up"
    assert ours[0].levelno == logging.INFO and "default" in ours[0].getMessage()
    assert logging.getLogger("django_tasks_db").isEnabledFor(logging.WARNING), (
        "a stop and a task that failed are still reported"
    )


def test_a_single_pass_for_cron_says_nothing_about_starting(caplog, signals_put_back):
    """Run from cron every minute, a line each time would be the noise back again (#1117)."""
    import logging

    from django.core.management import call_command

    from postulo.core.management.commands import work

    with caplog.at_level(logging.INFO):
        call_command("work", "--once")

    assert [r for r in caplog.records if r.name in (work.__name__, "django_tasks_db")] == []


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
