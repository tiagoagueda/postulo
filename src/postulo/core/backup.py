"""Instance backup and restore: the database and the media directory in one archive.

The per-person export (:mod:`postulo.core.export`) is the portable copy — readable JSON,
useful in ten years. This is the operator's copy: everything on the instance, taken
consistently while it runs, and put back the same way. One `.tar.gz` holding a manifest,
the database, and the media directory file by file.

The database is copied through the engine's own mechanism — SQLite's online backup API,
which is consistent while the application is being used, or ``pg_dump`` — never by copying
a file that is being written to. Media is streamed into the archive rather than read into
memory, because a directory of PDFs is small and a directory of videos is not.

A backup that was never opened is a hope, so every archive is verified after it is
written: the manifest is read back and the database's checksum compared.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from postulo import __version__

#: Bumped when the archive's shape changes.
BACKUP_FORMAT = 1
MANIFEST = "manifest.json"
MEDIA_PREFIX = "media"


class BackupError(Exception):
    """Something that stops a backup being taken, or a restore being trusted."""


@dataclass
class BackupReport:
    path: Path
    bytes: int
    counts: dict[str, int] = field(default_factory=dict)
    media_files: int = 0
    media_bytes: int = 0


@dataclass
class RestoreReport:
    counts: dict[str, int] = field(default_factory=dict)
    media_files: int = 0
    media_skipped: int = 0


def database_vendor() -> str:
    """``sqlite`` or ``postgresql``: the engines Postulo supports."""
    return connection.vendor


def _not_in_a_transaction() -> None:
    """SQLite's backup API waits forever on a connection that holds a transaction.

    A management command runs in autocommit, so this never fires in use; it is here so
    that a caller inside ``atomic()`` gets an error instead of a hang.
    """
    if connection.in_atomic_block:
        raise BackupError("A backup cannot be taken or restored from inside a transaction.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts() -> dict[str, int]:
    from postulo.applications.models import Application
    from postulo.documents.models import RenderedDocument, UploadedDocument
    from postulo.jobs.models import Company, JobPosting

    return {
        "users": get_user_model().objects.count(),
        "companies": Company.objects.count(),
        "postings": JobPosting.objects.count(),
        "applications": Application.objects.count(),
        "uploads": UploadedDocument.objects.count(),
        "rendered": RenderedDocument.objects.count(),
    }


def _postgres_env() -> tuple[dict[str, str], str]:
    """Environment for the PostgreSQL tools, and the database name."""
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    if db.get("HOST"):
        env["PGHOST"] = str(db["HOST"])
    if db.get("PORT"):
        env["PGPORT"] = str(db["PORT"])
    if db.get("USER"):
        env["PGUSER"] = str(db["USER"])
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    return env, str(db["NAME"])


def dump_database(target: Path) -> str:
    """Write a consistent copy of the database to ``target``; return the member name."""
    vendor = database_vendor()
    if vendor == "sqlite":
        _not_in_a_transaction()
        connection.ensure_connection()
        copy = sqlite3.connect(str(target))
        try:
            connection.connection.backup(copy)
        finally:
            copy.close()
        return "database.sqlite3"
    if vendor == "postgresql":
        tool = shutil.which("pg_dump")
        if tool is None:
            raise BackupError(
                "pg_dump is not on the PATH. Install the PostgreSQL client tools where "
                "Postulo runs, or take the backup with pg_dump yourself."
            )
        env, name = _postgres_env()
        result = subprocess.run(  # noqa: S603 - a fixed tool with fixed arguments
            [tool, "--format=custom", "--no-owner", "--no-privileges", f"--file={target}", name],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError(f"pg_dump failed: {result.stderr.strip()}")
        return "database.dump"
    raise BackupError(f"Backups are not supported for the {vendor!r} database engine.")


def load_database(source: Path, member: str) -> None:
    """Replace the database's contents with the copy in ``source``."""
    vendor = database_vendor()
    if vendor == "sqlite":
        if member != "database.sqlite3":
            raise BackupError("This archive holds a PostgreSQL dump; this instance runs SQLite.")
        _not_in_a_transaction()
        copy = sqlite3.connect(str(source))
        try:
            connection.ensure_connection()
            copy.backup(connection.connection)
        finally:
            copy.close()
        return
    if vendor == "postgresql":
        if member != "database.dump":
            raise BackupError(
                "This archive holds an SQLite database; this instance runs PostgreSQL."
            )
        tool = shutil.which("pg_restore")
        if tool is None:
            raise BackupError("pg_restore is not on the PATH.")
        env, name = _postgres_env()
        connection.close()
        result = subprocess.run(  # noqa: S603 - a fixed tool with fixed arguments
            [
                tool,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                f"--dbname={name}",
                str(source),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError(f"pg_restore failed: {result.stderr.strip()}")
        return
    raise BackupError(f"Restores are not supported for the {vendor!r} database engine.")


def default_target() -> Path:
    return Path(settings.POSTULO_BACKUP_DIR)


def resolve_target(target: Path | str | None) -> Path:
    """A file to write: the path given, or a timestamped name inside a directory."""
    path = Path(target) if target else default_target()
    if path.is_dir() or (not path.suffix and not path.exists()):
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        path = path / f"postulo-backup-{stamp}.tar.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _media_stats(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for entry in root.rglob("*"):
        if entry.is_file():
            files += 1
            size += entry.stat().st_size
    return files, size


def write_backup(target: Path | str | None = None, *, include_media: bool = True) -> BackupReport:
    """Take a backup, verify it, and say what it holds."""
    path = resolve_target(target)
    media_root = Path(settings.MEDIA_ROOT)
    with_media = include_media and media_root.is_dir()
    media_files, media_bytes = _media_stats(media_root) if with_media else (0, 0)

    with tempfile.TemporaryDirectory(prefix="postulo-backup-") as scratch:
        dump = Path(scratch) / "database"
        member = dump_database(dump)
        manifest = {
            "postulo": {
                "version": __version__,
                "backup_format": BACKUP_FORMAT,
                "created_at": timezone.now().isoformat(),
            },
            "database": {
                "engine": database_vendor(),
                "member": member,
                "sha256": _sha256(dump),
                "bytes": dump.stat().st_size,
            },
            "media": {"included": with_media, "files": media_files, "bytes": media_bytes},
            "counts": _counts(),
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(manifest_bytes)
            info.mtime = int(timezone.now().timestamp())
            archive.addfile(info, io.BytesIO(manifest_bytes))
            archive.add(dump, arcname=member)
            if with_media:
                archive.add(media_root, arcname=MEDIA_PREFIX, recursive=True)

    verify_backup(path)
    return BackupReport(
        path=path,
        bytes=path.stat().st_size,
        counts=manifest["counts"],
        media_files=media_files,
        media_bytes=media_bytes,
    )


def read_manifest(archive: tarfile.TarFile) -> dict:
    try:
        member = archive.getmember(MANIFEST)
    except KeyError as exc:
        raise BackupError("Not a Postulo backup: the archive has no manifest.") from exc
    handle = archive.extractfile(member)
    if handle is None:
        raise BackupError("Not a Postulo backup: the manifest cannot be read.")
    try:
        manifest = json.loads(handle.read().decode("utf-8"))
    except ValueError as exc:
        raise BackupError("Not a Postulo backup: the manifest is not valid JSON.") from exc
    fmt = (manifest.get("postulo") or {}).get("backup_format")
    if fmt != BACKUP_FORMAT:
        raise BackupError(
            f"This archive is backup format {fmt!r}; this version of Postulo reads {BACKUP_FORMAT}."
        )
    return manifest


def _hash_member(archive: tarfile.TarFile, name: str) -> str:
    handle = archive.extractfile(name)
    if handle is None:
        raise BackupError(f"The archive has no {name!r}.")
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def verify_backup(path: Path | str) -> dict:
    """Open the archive, read the manifest, and check the database against its checksum."""
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"{path} does not exist.")
    try:
        with tarfile.open(path, "r:gz") as archive:
            manifest = read_manifest(archive)
            member = manifest["database"]["member"]
            try:
                archive.getmember(member)
            except KeyError as exc:
                raise BackupError(f"The archive has no {member!r}.") from exc
            if _hash_member(archive, member) != manifest["database"]["sha256"]:
                raise BackupError("The database in the archive does not match its checksum.")
    except tarfile.TarError as exc:
        raise BackupError(f"Not a readable archive: {exc}") from exc
    return manifest


def _safe_media_path(name: str) -> PurePosixPath:
    """The path under the media root a member may be written to, or an error."""
    relative = PurePosixPath(name)
    parts = relative.parts
    if not parts or parts[0] != MEDIA_PREFIX:
        raise BackupError(f"Unexpected member outside media/: {name!r}")
    rest = parts[1:]
    if not rest:
        raise BackupError("bare media/")
    if relative.is_absolute() or any(part in ("..", "") for part in rest):
        raise BackupError(f"Refusing a member that escapes the media directory: {name!r}")
    return PurePosixPath(*rest)


def restore_backup(path: Path | str, *, force: bool = False) -> RestoreReport:
    """Put an archive back: database, then media, then migrations."""
    path = Path(path)
    manifest = verify_backup(path)
    engine = manifest["database"]["engine"]
    if engine != database_vendor():
        raise BackupError(
            f"This archive came from a {engine} database; this instance runs {database_vendor()}."
        )
    if get_user_model().objects.exists() and not force:
        raise BackupError(
            "This instance is not empty. Restoring would replace everything on it; "
            "pass --force if that is what you mean."
        )

    media_root = Path(settings.MEDIA_ROOT)
    report = RestoreReport()
    with (
        tarfile.open(path, "r:gz") as archive,
        tempfile.TemporaryDirectory(prefix="postulo-restore-") as scratch,
    ):
        member = manifest["database"]["member"]
        dump = Path(scratch) / "database"
        handle = archive.extractfile(member)
        if handle is None:
            raise BackupError(f"The archive has no {member!r}.")
        with dump.open("wb") as out:
            shutil.copyfileobj(handle, out)

        # Every media member is checked before anything is written, so a hostile archive
        # writes nothing at all rather than half of something.
        media_members = []
        for entry in archive.getmembers():
            if entry.name == MANIFEST or entry.name == member:
                continue
            if entry.isdir():
                if entry.name != MEDIA_PREFIX:
                    _safe_media_path(entry.name)
                continue
            if not entry.isfile():
                raise BackupError(f"Refusing a member that is not a plain file: {entry.name!r}")
            media_members.append((entry, _safe_media_path(entry.name)))

        load_database(dump, member)

        for entry, relative in media_members:
            destination = media_root / Path(*relative.parts)
            if destination.exists() and not force:
                report.media_skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(entry)
            if source is None:
                continue
            with destination.open("wb") as out:
                shutil.copyfileobj(source, out)
            report.media_files += 1

    # An older backup lands on a newer Postulo: bring it forward.
    call_command("migrate", interactive=False, verbosity=0)
    report.counts = _counts()
    return report


def as_dict(report: BackupReport | RestoreReport) -> dict:
    data = asdict(report)
    if "path" in data:
        data["path"] = str(data["path"])
    return data
