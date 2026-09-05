"""Instance backup and restore: one archive, taken consistently, verified, put back."""

import io
import json
import tarfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from postulo.applications.models import Application, Status
from postulo.core import backup as backup_module
from postulo.core.backup import (
    BACKUP_FORMAT,
    BackupError,
    restore_backup,
    verify_backup,
    write_backup,
)
from postulo.jobs.models import Company, JobPosting

# Transactional throughout: SQLite's backup API cannot work through the transaction the
# ordinary test wrapper holds open, and a command runs in autocommit anyway.
pytestmark = pytest.mark.django_db(transaction=True)

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture(autouse=True)
def _own_media_root(settings, tmp_path):
    # The suite shares one media directory, and other tests leave files in it. These
    # tests count files, so they get a fresh one each.
    settings.MEDIA_ROOT = str(tmp_path / "media")
    Path(settings.MEDIA_ROOT).mkdir()


def a_search(username="alex"):
    user = User.objects.create_user(email=f"{username}@example.org", password=PASSWORD)
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    return user


def a_media_file(settings, relative="documents/cv.pdf", content=b"%PDF-1.4 fake"):
    path = Path(settings.MEDIA_ROOT) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def members_of(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


# ------------------------------------------------------------------- taking one


def test_a_backup_holds_the_manifest_the_database_and_the_media(tmp_path, settings):
    a_search()
    a_media_file(settings)
    report = write_backup(tmp_path / "instance.tar.gz")

    assert report.path == tmp_path / "instance.tar.gz"
    names = members_of(report.path)
    assert "manifest.json" in names
    assert "database.sqlite3" in names
    assert "media/documents/cv.pdf" in names
    assert report.counts == {
        "users": 1,
        "companies": 1,
        "postings": 1,
        "applications": 1,
        "uploads": 0,
        "rendered": 0,
    }
    assert report.media_files == 1 and report.media_bytes == len(b"%PDF-1.4 fake")

    manifest = verify_backup(report.path)
    assert manifest["postulo"]["backup_format"] == BACKUP_FORMAT
    assert manifest["database"]["engine"] == "sqlite"
    assert manifest["media"] == {"included": True, "files": 1, "bytes": 13}


def test_a_directory_target_gets_a_timestamped_file(tmp_path):
    report = write_backup(tmp_path / "backups")
    assert report.path.parent == tmp_path / "backups"
    assert report.path.name.startswith("postulo-backup-") and report.path.suffix == ".gz"

    # And no target at all means the configured directory.
    from django.test import override_settings

    with override_settings(POSTULO_BACKUP_DIR=tmp_path / "default"):
        report = write_backup()
    assert report.path.parent == tmp_path / "default"


def test_media_can_be_left_out(tmp_path, settings):
    a_media_file(settings)
    report = write_backup(tmp_path / "db-only.tar.gz", include_media=False)
    assert not any(name.startswith("media/") for name in members_of(report.path))
    assert verify_backup(report.path)["media"]["included"] is False


def test_verification_notices_a_database_that_does_not_match(tmp_path):
    good = write_backup(tmp_path / "good.tar.gz").path
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(good, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
        for member in source.getmembers():
            handle = source.extractfile(member)
            if member.name == "manifest.json":
                manifest = json.loads(handle.read())
                manifest["database"]["sha256"] = "0" * 64
                data = json.dumps(manifest).encode()
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
            else:
                target.addfile(member, handle)
    with pytest.raises(BackupError, match="does not match its checksum"):
        verify_backup(tampered)


def test_something_that_is_not_a_backup_is_refused(tmp_path):
    with pytest.raises(BackupError, match="does not exist"):
        verify_backup(tmp_path / "missing.tar.gz")
    not_gz = tmp_path / "plain.tar.gz"
    not_gz.write_bytes(b"hello")
    with pytest.raises(BackupError, match="Not a readable archive"):
        verify_backup(not_gz)
    no_manifest = tmp_path / "empty.tar.gz"
    with tarfile.open(no_manifest, "w:gz"):
        pass
    with pytest.raises(BackupError, match="no manifest"):
        verify_backup(no_manifest)


# ----------------------------------------------------------------- putting back


def test_a_backup_restores_onto_an_empty_instance(tmp_path, settings):
    a_search()
    cv = a_media_file(settings)
    archive = write_backup(tmp_path / "instance.tar.gz").path

    User.objects.all().delete()
    cv.unlink()
    assert not Company.objects.exists()

    report = restore_backup(archive)
    assert report.counts["users"] == 1 and report.counts["applications"] == 1
    assert report.media_files == 1
    assert User.objects.get().username == "alex"
    assert Company.objects.get().name == "Aperture Science"
    assert cv.read_bytes() == b"%PDF-1.4 fake"


def test_restore_refuses_an_instance_that_is_not_empty_unless_forced(tmp_path, settings):
    a_search()
    archive = write_backup(tmp_path / "instance.tar.gz").path
    a_search("someone")

    with pytest.raises(BackupError, match="not empty"):
        restore_backup(archive)
    assert User.objects.count() == 2

    report = restore_backup(archive, force=True)
    assert report.counts["users"] == 1
    assert User.objects.get().username == "alex"


def test_existing_media_is_kept_unless_forced(tmp_path, settings):
    a_media_file(settings, content=b"original")
    archive = write_backup(tmp_path / "instance.tar.gz").path
    path = a_media_file(settings, content=b"changed since")

    report = restore_backup(archive)
    assert report.media_skipped == 1 and path.read_bytes() == b"changed since"

    report = restore_backup(archive, force=True)
    assert report.media_files == 1 and path.read_bytes() == b"original"


def test_a_hostile_archive_writes_nothing(tmp_path, settings):
    archive = write_backup(tmp_path / "instance.tar.gz").path
    hostile = tmp_path / "hostile.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(hostile, "w:gz") as target:
        for member in source.getmembers():
            target.addfile(member, source.extractfile(member))
        evil = tarfile.TarInfo("media/../escaped.txt")
        evil.size = 4
        target.addfile(evil, io.BytesIO(b"boom"))

    with pytest.raises(BackupError, match="escapes the media directory"):
        restore_backup(hostile)
    assert not (Path(settings.MEDIA_ROOT).parent / "escaped.txt").exists()

    linked = tmp_path / "linked.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(linked, "w:gz") as target:
        for member in source.getmembers():
            target.addfile(member, source.extractfile(member))
        link = tarfile.TarInfo("media/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        target.addfile(link)
    with pytest.raises(BackupError, match="not a plain file"):
        restore_backup(linked)


def test_an_archive_from_the_other_engine_is_refused(tmp_path, monkeypatch):
    archive = write_backup(tmp_path / "instance.tar.gz").path
    monkeypatch.setattr(backup_module, "database_vendor", lambda: "postgresql")
    with pytest.raises(BackupError, match="came from a sqlite database"):
        restore_backup(archive)


# ------------------------------------------------------------------ PostgreSQL


def test_postgres_needs_pg_dump_and_calls_it_the_right_way(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "database_vendor", lambda: "postgresql")
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: None)
    with pytest.raises(BackupError, match="pg_dump is not on the PATH"):
        write_backup(tmp_path / "pg.tar.gz")

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-2].removeprefix("--file=")).write_bytes(b"PGDMP fake")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr(backup_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)
    report = write_backup(tmp_path / "pg.tar.gz")
    assert "database.dump" in members_of(report.path)
    args, kwargs = calls[0]
    assert args[0] == "/usr/bin/pg_dump" and "--format=custom" in args
    assert kwargs["env"] is not None


# ------------------------------------------------------------------ the commands


def test_the_commands_wrap_it_all(tmp_path, capsys):
    a_search()
    call_command("backup", str(tmp_path / "cli.tar.gz"))
    out = capsys.readouterr().out
    assert "Backed up to" in out and "1 users" in out and "verified" in out

    with pytest.raises(CommandError, match="not empty"):
        call_command("restore", str(tmp_path / "cli.tar.gz"))

    User.objects.all().delete()
    call_command("restore", str(tmp_path / "cli.tar.gz"))
    out = capsys.readouterr().out
    assert "Restored" in out and "1 users" in out
    assert User.objects.get().username == "alex"

    with pytest.raises(CommandError, match="does not exist"):
        call_command("restore", str(tmp_path / "nope.tar.gz"))
