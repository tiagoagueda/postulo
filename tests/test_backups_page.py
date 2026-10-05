"""Server settings > Backups: the list, taking one, the schedule and its retention (#242).

The archive writer and the restore are tested in `tests/test_backup.py`; what is tested here
is everything around them. Archives are built by hand, a tar with a manifest and a database
member, because the page only ever reads them and a real one needs a transaction-free
database.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import tarfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from postulo.core import backups, metrics
from postulo.core.backup import BackupError
from postulo.core.memo import forget_current
from postulo.core.models import Errand, ErrandState, SiteSettings

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def admin(db):
    person = User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )
    EmailAddress.objects.create(user=person, email=person.email, verified=True, primary=True)
    return person


@pytest.fixture(autouse=True)
def _a_directory_of_its_own(settings, tmp_path):
    settings.POSTULO_BACKUP_DIR = tmp_path / "backups"
    settings.POSTULO_BACKUP_DIR.mkdir()
    settings.POSTULO_BACKGROUND_WORK = False
    return settings.POSTULO_BACKUP_DIR


def make_archive(
    stamp: str = "20260101-030000", *, engine="sqlite", broken=False, prefix="backup"
) -> Path:
    """A file the page will list: a manifest and a database member that match, or do not."""
    database = b"not really a database"
    manifest = {
        "postulo": {
            "version": "0.9.0",
            "backup_format": 2,
            "created_at": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T03:00:00+00:00",
        },
        "database": {
            "engine": engine,
            "member": "database.sqlite3",
            "sha256": "0" * 64 if broken else hashlib.sha256(database).hexdigest(),
            "bytes": len(database),
        },
        "media": {"included": True, "files": 3, "bytes": 30},
        "plugins": {"included": True, "files": 0, "bytes": 0, "installed": []},
        "counts": {"users": 2, "applications": 5},
    }
    path = backups.directory() / f"postulo-{prefix}-{stamp}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in (
            ("manifest.json", json.dumps(manifest).encode()),
            ("database.sqlite3", database),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


# ------------------------------------------------------------------------ the list


def test_the_list_says_what_each_archive_holds(client, admin):
    make_archive("20260101-030000")
    make_archive("20260102-030000")
    client.force_login(admin)

    html = client.get(reverse("server:backups")).content.decode()

    assert html.index("20260102-030000") < html.index("20260101-030000"), "newest first"
    assert "0.9.0" in html and "sqlite" in html and "5 applications" in html
    assert "<table" in html and "Not checked yet" in html


def test_a_page_load_never_hashes_an_archive(client, admin, monkeypatch):
    make_archive()
    client.force_login(admin)

    def hashed(path):
        raise AssertionError("a listing must not verify")

    monkeypatch.setattr(backups, "verify_backup", hashed)
    assert client.get(reverse("server:backups")).status_code == 200


def test_checking_remembers_the_answer_for_the_file_as_it_is(client, admin):
    good = make_archive("20260101-030000")
    bad = make_archive("20260102-030000", broken=True)
    client.force_login(admin)

    client.post(reverse("server:backup_verify", args=[good.name]))
    client.post(reverse("server:backup_verify", args=[bad.name]))
    html = client.get(reverse("server:backups")).content.decode()

    assert "Verified" in html
    assert "Does not verify" in html, "said in words and not only in colour"
    assert "checksum" in html

    # A file that changed is not trusted for having been checked.
    good.write_bytes(good.read_bytes() + b"x")
    assert backups.archives()[1].checked == ""


def test_things_that_are_not_archives_are_not_listed(admin, settings):
    make_archive()
    (backups.directory() / "notes.txt").write_text("hello")
    (backups.directory() / "postulo-backup-oops.tar.gz").write_bytes(b"x")
    (backups.directory() / "postulo-backup-20260101-030000.tar.gz.part").write_bytes(b"x")

    assert [a.name for a in backups.archives()] == ["postulo-backup-20260101-030000.tar.gz"]


def test_an_unreadable_archive_is_listed_and_says_so(client, admin):
    (backups.directory() / "postulo-backup-20260105-030000.tar.gz").write_bytes(b"not a tar")
    client.force_login(admin)

    html = client.get(reverse("server:backups")).content.decode()

    assert "Does not verify" in html


def test_the_page_warns_when_the_next_backup_may_not_fit(client, admin, monkeypatch):
    make_archive()
    client.force_login(admin)
    monkeypatch.setattr(backups, "free_space", lambda: 10)

    html = client.get(reverse("server:backups")).content.decode()

    assert 'data-backup-room="low"' in html


def test_no_warning_without_an_archive_to_judge_by():
    assert backups.room_warning([], 5) is False


# ------------------------------------------------------------------------ back up now


def test_back_up_now_is_an_errand_and_the_page_shows_it(client, admin, monkeypatch):
    path = make_archive("20260110-030000")
    monkeypatch.setattr(backups, "take", lambda: path)
    client.force_login(admin)

    response = client.post(reverse("server:backup_run"))

    errand = Errand.objects.get(kind="backup")
    assert errand.owner == admin and errand.state == ErrandState.DONE
    assert response["Location"].endswith(f"?errand={errand.pk}")
    page = client.get(response["Location"]).content.decode()
    assert 'role="status"' in page and "The backup is written and checked." in page
    assert SiteSettings.get().backup_last_ok_at is not None


def test_a_failed_backup_says_why_and_is_counted(client, admin, monkeypatch):
    def fails():
        raise BackupError("pg_dump is not installed")

    monkeypatch.setattr(backups, "take", fails)
    client.force_login(admin)

    client.post(reverse("server:backup_run"))

    errand = Errand.objects.get(kind="backup")
    assert errand.state == ErrandState.FAILED and "pg_dump" in errand.error
    row = SiteSettings.get()
    assert row.backup_failures == 1 and "pg_dump" in row.backup_last_error


def test_a_second_press_while_one_runs_says_so(client, admin):
    cache.set(backups.LOCK_KEY, "someone", 60)
    client.force_login(admin)

    response = client.post(reverse("server:backup_run"), follow=True)

    assert not Errand.objects.filter(kind="backup").exists()
    assert "already running" in response.content.decode()


def test_the_lock_is_held_by_one_backup_only(monkeypatch):
    cache.set(backups.LOCK_KEY, "someone", 60)
    with pytest.raises(backups.Busy):
        backups.take()


def test_taking_one_releases_the_lock(monkeypatch):
    path = make_archive()
    monkeypatch.setattr(backups, "write_backup", lambda: type("R", (), {"path": path})())

    assert backups.take() == path
    assert not backups.running()
    assert backups.archives()[0].checked == "ok"


# ------------------------------------------------------------------------ delete


def test_the_only_archive_known_to_open_cannot_be_deleted(client, admin):
    only = make_archive("20260101-030000")
    backups.verify(only)

    assert backups.delete_blocked(only.name) != ""

    other = make_archive("20260102-030000")
    assert backups.delete_blocked(only.name) != "", "an unchecked other is no comfort"
    backups.verify(other)
    assert backups.delete_blocked(only.name) == ""


def test_a_broken_archive_may_always_go(admin):
    broken = make_archive(broken=True)
    backups.verify(broken)

    assert backups.delete_blocked(broken.name) == ""


# ------------------------------------------------------------------------ restore


def test_the_restore_page_verifies_and_writes_out_the_commands(client, admin):
    path = make_archive()
    client.force_login(admin)

    html = client.get(reverse("server:backup_restore", args=[path.name])).content.decode()

    assert 'data-restore="verified"' in html
    assert "manage.py restore" in html and path.name in html
    assert "docker compose" in html and "--force" in html
    assert backups.archives()[0].checked == "ok"


def test_the_restore_page_refuses_an_archive_that_does_not_verify(client, admin):
    path = make_archive(broken=True)
    client.force_login(admin)

    html = client.get(reverse("server:backup_restore", args=[path.name])).content.decode()

    assert 'data-restore="refused"' in html and "manage.py restore" not in html


def test_the_restore_page_says_when_the_engine_is_the_other_one(client, admin):
    path = make_archive(engine="postgresql")
    client.force_login(admin)

    html = client.get(reverse("server:backup_restore", args=[path.name])).content.decode()

    assert 'data-restore="engine"' in html


# ------------------------------------------------------------------------ schedule


def a_row(**fields) -> SiteSettings:
    return SiteSettings(
        **{"backup_schedule": "daily", "backup_hour": 3, "backup_weekday": 0, **fields}
    )


def paris(*when) -> dt.datetime:
    return dt.datetime(*when, tzinfo=ZoneInfo("Europe/Paris"))


def test_a_daily_schedule_is_due_once_a_day_at_its_hour(settings):
    settings.TIME_ZONE = "Europe/Paris"
    row = a_row(backup_last_run_at=paris(2026, 3, 10, 3, 0))

    assert not backups.is_due(row, paris(2026, 3, 10, 14, 0))
    assert backups.is_due(row, paris(2026, 3, 11, 3, 5))
    assert backups.next_slot(row, paris(2026, 3, 10, 14, 0)) == paris(2026, 3, 11, 3, 0)


def test_a_weekly_schedule_waits_for_its_weekday(settings):
    settings.TIME_ZONE = "Europe/Paris"
    row = a_row(backup_schedule="weekly", backup_weekday=2, backup_last_run_at=paris(2026, 3, 4, 3))

    # 2026-03-04 is a Wednesday (2).
    assert not backups.is_due(row, paris(2026, 3, 10, 12))
    assert backups.is_due(row, paris(2026, 3, 11, 3, 1))
    assert backups.next_slot(row, paris(2026, 3, 5, 12)) == paris(2026, 3, 11, 3)


def test_the_hour_is_in_the_instances_time_zone(settings):
    settings.TIME_ZONE = "Europe/Paris"
    row = a_row()
    # 02:30 UTC is 03:30 in Paris in winter: past the slot.
    now = dt.datetime(2026, 1, 15, 2, 30, tzinfo=dt.UTC)

    assert backups.last_slot(row, now) == paris(2026, 1, 15, 3, 0)


def test_off_is_never_due():
    assert not backups.is_due(SiteSettings(), timezone.now())


def test_a_slot_nobody_took_is_missed_after_a_grace(settings):
    settings.TIME_ZONE = "Europe/Paris"
    row = a_row(backup_last_run_at=paris(2026, 3, 8, 3))

    assert not backups.is_missed(row, paris(2026, 3, 10, 3, 30))
    assert backups.is_missed(row, paris(2026, 3, 10, 6, 0))


def test_the_scheduler_takes_a_due_slot_once(monkeypatch):
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={"backup_schedule": "daily", "backup_hour": 0, "backup_last_run_at": None},
    )
    taken = []
    monkeypatch.setattr(backups, "take", lambda: taken.append(1) or make_archive())

    assert backups.start_if_due() is True
    assert backups.start_if_due() is False, "the slot is claimed"

    assert taken == [1]
    row = SiteSettings.get()
    assert row.backup_last_run_ok is True and row.backup_last_ok_at is not None


def test_a_scheduled_failure_is_written_down_and_does_not_raise(monkeypatch):
    SiteSettings.objects.update_or_create(pk=1, defaults={"backup_schedule": "daily"})

    def fails():
        raise BackupError("the disk is full")

    monkeypatch.setattr(backups, "take", fails)
    backups.run_scheduled()

    row = SiteSettings.get()
    assert row.backup_last_run_ok is False and "disk is full" in row.backup_last_error
    assert row.backup_failures == 1


def test_retention_keeps_the_newest_and_spares_uploads(settings):
    for day in range(1, 6):
        make_archive(f"2026010{day}-030000")
    uploaded = make_archive("20250101-030000", prefix="upload")

    gone = backups.apply_retention(2)

    assert sorted(gone) == [
        "postulo-backup-20260101-030000.tar.gz",
        "postulo-backup-20260102-030000.tar.gz",
        "postulo-backup-20260103-030000.tar.gz",
    ]
    assert uploaded.exists()


def test_old_ones_go_only_after_the_new_one_verified(monkeypatch):
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"backup_schedule": "daily", "backup_keep": 1}
    )
    old = make_archive("20250101-030000")

    def fails():
        raise BackupError("did not verify")

    monkeypatch.setattr(backups, "take", fails)
    backups.run_scheduled()

    assert old.exists()


def test_saving_the_schedule_starts_its_clock(client, admin):
    client.force_login(admin)

    client.post(
        reverse("server:backups"),
        {"backup_schedule": "daily", "backup_hour": 4, "backup_weekday": 0, "backup_keep": 3},
    )

    row = SiteSettings.get()
    assert row.backup_schedule == "daily" and row.backup_keep == 3
    assert row.backup_last_run_at is not None
    assert not backups.is_due(row), "switching it on does not run a slot from this morning"


def test_the_schedule_refuses_keeping_none(client, admin):
    client.force_login(admin)

    response = client.post(
        reverse("server:backups"),
        {"backup_schedule": "daily", "backup_hour": 4, "backup_weekday": 0, "backup_keep": 0},
    )

    assert response.status_code == 200
    assert SiteSettings.get().backup_schedule == "off"


# ------------------------------------------------------------------------ the rest


def test_overview_says_so_when_the_last_backup_failed(client, admin):
    SiteSettings.objects.update_or_create(pk=1, defaults={"backup_last_error": "disk full"})
    client.force_login(admin)

    html = client.get(reverse("server:overview")).content.decode()

    assert "data-backup-failed" in html


def test_the_metrics_carry_the_two_backup_numbers():
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"backup_failures": 2, "backup_last_ok_at": timezone.now()}
    )
    forget_current()
    text = metrics.render()

    assert "postulo_backup_failures_total 2" in text
    assert "postulo_backup_last_success_timestamp_seconds " in text
    assert "# TYPE postulo_backup_failures_total counter" in text


def test_listing_reads_the_manifest_without_unpacking_the_archive():
    """`getmember` indexes the whole archive; the list does this for every file on every load."""
    from postulo.core.backup import read_manifest

    path = make_archive()
    with tarfile.open(path, "r:gz") as opened:
        read_manifest(opened)
        assert not opened._loaded
