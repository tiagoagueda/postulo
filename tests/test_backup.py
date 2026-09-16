"""Instance backup and restore: one archive, taken consistently, verified, put back."""

import io
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from postulo.applications.models import Application, Status
from postulo.core import backup as backup_module
from postulo.core.backup import (
    BACKUP_FORMAT,
    BackupError,
    database_vendor,
    restore_backup,
    verify_backup,
    write_backup,
)
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import secrets
from postulo.plugins.installing import Installed, read_record, write_record
from postulo.plugins.models import Connection

# Transactional throughout: SQLite's backup API cannot work through the transaction the
# ordinary test wrapper holds open, pg_dump connects for itself and would not see it
# either, and a command runs in autocommit anyway.
pytestmark = pytest.mark.django_db(transaction=True)

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

#: What the archive's database member is called on the engine this run is using. The whole
#: file used to say "sqlite" out loud, because that is the only engine the suite had ever
#: been pointed at -- so the PostgreSQL half of `core/backup.py` was covered by nothing but
#: a monkeypatched `subprocess.run`, and shipped in an image with no pg_dump in it (#219).
#: Set POSTULO_TEST_DATABASE_URL to run all of this against a real PostgreSQL; CI does.
MEMBER = {"sqlite": "database.sqlite3", "postgresql": "database.dump"}
THE_OTHER_ENGINE = {"sqlite": "postgresql", "postgresql": "sqlite"}

#: The real busy check, held on to here because the autouse fixture below replaces it for
#: every test that is not about it.
busy_reason = backup_module.busy_reason


@pytest.fixture(autouse=True)
def _own_media_root(settings, tmp_path):
    # The suite shares one media directory, and other tests leave files in it. These
    # tests count files, so they get a fresh one each.
    settings.MEDIA_ROOT = str(tmp_path / "media")
    Path(settings.MEDIA_ROOT).mkdir()


@pytest.fixture(autouse=True)
def _own_plugins_dir(settings, tmp_path):
    # The plugins directory is part of the archive now, and its default is a real
    # directory in the checkout. A restore writes into it, so these tests get their own.
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    Path(settings.POSTULO_PLUGINS_DIR).mkdir()


@pytest.fixture(autouse=True)
def _nothing_else_is_connected(monkeypatch):
    """The busy check, neutralised for every test that is not about the busy check.

    A test *is* another connection to the test database — that is what running one means —
    so the guard cannot be left armed here without the suite refusing its own restores.
    It is exercised on its own, below.
    """
    monkeypatch.setattr(backup_module, "busy_reason", lambda: None)


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


def manifest_of(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as archive:
        return json.loads(archive.extractfile("manifest.json").read())


def an_installed_plugin(settings, name="postulo-imap", version="0.3.0"):
    """A plugin on the data volume: its line in the record, and a file of its own."""
    write_record([Installed(name=name, version=version, origin="upload")])
    package = Path(settings.POSTULO_PLUGINS_DIR) / name.replace("-", "_") / "__init__.py"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text("# the installed package\n", encoding="utf-8")
    return package


def a_connection(user, *, with_secrets=True):
    connection = Connection(kind="notifier", plugin="imap", label="Mailbox", owner=user)
    if with_secrets:
        connection.secrets = {"password": "hunter2"}
    connection.save()
    return connection


def rewritten_manifest(source: Path, target: Path, change) -> Path:
    """A copy of ``source`` whose manifest has been through ``change``."""
    with tarfile.open(source, "r:gz") as old, tarfile.open(target, "w:gz") as new:
        for member in old.getmembers():
            handle = old.extractfile(member)
            if member.name == "manifest.json":
                data = json.dumps(change(json.loads(handle.read()))).encode()
                member.size = len(data)
                new.addfile(member, io.BytesIO(data))
            elif handle is None:
                new.addfile(member)
            else:
                new.addfile(member, handle)
    return target


# ------------------------------------------------------------------- taking one


def test_a_backup_holds_the_manifest_the_database_and_the_media(tmp_path, settings):
    a_search()
    a_media_file(settings)
    report = write_backup(tmp_path / "instance.tar.gz")

    assert report.path == tmp_path / "instance.tar.gz"
    names = members_of(report.path)
    assert "manifest.json" in names
    assert MEMBER[database_vendor()] in names
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
    assert manifest["database"]["engine"] == database_vendor()
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
    """Whichever engine this run is on, the archive from the other one has to be refused.

    Restoring a PostgreSQL dump into SQLite, or the reverse, cannot half-work: it either
    does nothing or leaves the instance in a state nobody can reason about.
    """
    here = database_vendor()
    archive = write_backup(tmp_path / "instance.tar.gz").path
    monkeypatch.setattr(backup_module, "database_vendor", lambda: THE_OTHER_ENGINE[here])
    with pytest.raises(BackupError, match=f"came from a {here} database"):
        restore_backup(archive)


# ---------------------------------------------------- plugins and the field key


def test_the_archive_carries_the_plugins_and_a_mark_of_the_key(tmp_path, settings):
    """What a restore needs and the database does not hold (#234)."""
    an_installed_plugin(settings)
    report = write_backup(tmp_path / "instance.tar.gz")

    names = members_of(report.path)
    assert "plugins/plugins.json" in names
    assert "plugins/postulo_imap/__init__.py" in names
    assert report.plugins == ["postulo-imap==0.3.0"]

    manifest = manifest_of(report.path)
    assert manifest["plugins"]["included"] is True
    assert manifest["plugins"]["installed"] == ["postulo-imap==0.3.0"]
    assert manifest["secrets"]["field_key_sha256"] == secrets.fingerprint()
    assert manifest["secrets"]["field_key_source"] == "SECRET_KEY"
    # The mark, and never the key itself, in any form.
    with tarfile.open(report.path, "r:gz") as archive:
        raw = archive.extractfile("manifest.json").read().decode()
    assert settings.SECRET_KEY not in raw


def test_plugins_can_be_left_out(tmp_path, settings):
    an_installed_plugin(settings)
    report = write_backup(tmp_path / "db-only.tar.gz", include_plugins=False)
    assert not any(name.startswith("plugins/") for name in members_of(report.path))
    # Still recorded, so a restore can say what was installed even when it cannot put it back.
    assert manifest_of(report.path)["plugins"] == {
        "included": False,
        "files": 0,
        "bytes": 0,
        "installed": ["postulo-imap==0.3.0"],
    }


def test_a_backup_never_writes_a_member_its_own_restore_would_refuse():
    """A symlink an installer left behind is left out rather than made unrestorable."""
    plain = tarfile.TarInfo("plugins/postulo_imap/__init__.py")
    assert backup_module._plain_files_only(plain) is plain
    directory = tarfile.TarInfo("plugins/postulo_imap")
    directory.type = tarfile.DIRTYPE
    assert backup_module._plain_files_only(directory) is directory
    link = tarfile.TarInfo("plugins/elsewhere")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    assert backup_module._plain_files_only(link) is None


def test_a_restore_into_a_fresh_data_directory_finds_the_plugins_and_the_key(tmp_path, settings):
    """The proposal's test: a fresh instance, and nothing missing but what cannot travel."""
    user = a_search()
    a_connection(user)
    an_installed_plugin(settings)
    archive = write_backup(tmp_path / "instance.tar.gz").path

    # A brand new instance: no accounts, and an empty data directory beside it.
    User.objects.all().delete()
    fresh = tmp_path / "fresh-plugins"
    fresh.mkdir()
    settings.POSTULO_PLUGINS_DIR = fresh

    report = restore_backup(archive)
    assert report.plugins == ["postulo-imap==0.3.0"]
    assert report.plugin_files == 2
    assert (fresh / "postulo_imap" / "__init__.py").is_file()
    assert [(entry.name, entry.version) for entry in read_record()] == [("postulo-imap", "0.3.0")]

    assert report.key_matches is True
    assert report.connections_with_secrets == 1
    assert Connection.objects.get().secrets == {"password": "hunter2"}


def test_a_restore_under_another_key_says_so_loudly(tmp_path, settings, capsys):
    user = a_search()
    a_connection(user)
    archive = write_backup(tmp_path / "instance.tar.gz").path

    User.objects.all().delete()
    # The instance was rebuilt and its secret key was not kept, which is the whole of the
    # failure: the rows come back, the passwords in them are ciphertext nobody can open.
    settings.POSTULO_FIELD_KEY = "an-entirely-different-key-42"

    report = restore_backup(archive)
    assert report.key_matches is False
    assert report.connections_with_secrets == 1

    call_command("restore", str(archive), "--force")
    out = capsys.readouterr().out
    assert "CANNOT be read here" in out and "POSTULO_FIELD_KEY" in out


def test_an_archive_from_before_the_plugins_went_in_still_restores(tmp_path, settings):
    """Format 1: no plugins, no fingerprint, and nothing to say about either."""
    a_search()
    archive = write_backup(tmp_path / "instance.tar.gz").path

    def to_format_one(manifest):
        manifest["postulo"]["backup_format"] = 1
        del manifest["plugins"]
        del manifest["secrets"]
        return manifest

    old = rewritten_manifest(archive, tmp_path / "old.tar.gz", to_format_one)
    User.objects.all().delete()
    report = restore_backup(old)
    assert report.counts["users"] == 1
    assert report.plugins == [] and report.key_matches is None

    newer = rewritten_manifest(
        archive,
        tmp_path / "newer.tar.gz",
        lambda manifest: {**manifest, "postulo": {**manifest["postulo"], "backup_format": 99}},
    )
    with pytest.raises(BackupError, match="reads 1 to"):
        verify_backup(newer)


# --------------------------------------------------- nothing else may be running


def test_a_restore_refuses_while_something_else_is_using_the_database(tmp_path, monkeypatch):
    a_search()
    archive = write_backup(tmp_path / "instance.tar.gz").path
    User.objects.all().delete()
    monkeypatch.setattr(backup_module, "busy_reason", lambda: "the scheduler has it open")

    with pytest.raises(BackupError, match="Something else is using the database"):
        restore_backup(archive)
    assert not User.objects.exists()

    # And --force is how somebody who knows better goes ahead anyway.
    assert restore_backup(archive, force=True).counts["users"] == 1


def test_sqlite_notices_another_process_by_the_files_wal_leaves_behind(tmp_path, monkeypatch):
    """WAL keeps `-shm` beside the database for as long as any connection is open (#206)."""
    database = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(tmp_path / "postulo.sqlite3"),
    }
    monkeypatch.setattr(backup_module, "database_vendor", lambda: "sqlite")
    monkeypatch.setattr(backup_module, "settings", SimpleNamespace(DATABASES={"default": database}))
    # Postulo's own connection is closed before it looks; here there is nothing to close.
    monkeypatch.setattr(backup_module, "connection", SimpleNamespace(close=lambda: None))

    assert busy_reason() is None

    other = sqlite3.connect(database["NAME"])
    try:
        assert other.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        other.execute("CREATE TABLE something (x)")
        other.commit()
        assert "postulo.sqlite3" in busy_reason()
    finally:
        other.close()
    assert busy_reason() is None


def test_an_in_memory_database_has_nothing_to_look_at(monkeypatch):
    monkeypatch.setattr(backup_module, "database_vendor", lambda: "sqlite")
    monkeypatch.setattr(
        backup_module,
        "settings",
        SimpleNamespace(
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        ),
    )
    assert busy_reason() is None


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
