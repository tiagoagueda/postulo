"""Instance backup and restore: the database, the media and the plugins in one archive.

The per-person export (:mod:`postulo.core.export`) is the portable copy — readable JSON,
useful in ten years. This is the operator's copy: everything on the instance, taken
consistently while it runs, and put back the same way. One `.tar.gz` holding a manifest,
the database, the media directory file by file, and the plugins directory beside it.

The database is copied through the engine's own mechanism — SQLite's online backup API,
which is consistent while the application is being used, or ``pg_dump`` — never by copying
a file that is being written to. Media is streamed into the archive rather than read into
memory, because a directory of PDFs is small and a directory of videos is not.

**The plugins directory is part of the instance.** It holds the record of what is
installed and the packages themselves, on the data volume rather than in the environment,
and a restore without it leaves connection rows belonging to plugins that are not there.
It is small — pure-Python wheels — so it goes in whole rather than as a list to be fetched
again from a catalogue that may no longer carry the version this instance ran.

**The key the connection secrets are under is not in the archive, and must not be.** What
the manifest carries is a one-way mark of it, so that a restore can say the one thing that
matters: whether the secrets it just put back can still be read here. They cannot be, if
the instance was rebuilt with a new ``SECRET_KEY`` and no ``POSTULO_FIELD_KEY`` — and
finding that out from a connection failing weeks later is the failure this prevents.

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
from postulo.config import sqlite as sqlite_options

#: Bumped when the archive's shape changes. Anything up to this is read: format 2 only
#: adds members and manifest keys, so a format 1 archive taken before plugins and the key
#: fingerprint existed still restores, without them.
BACKUP_FORMAT = 2
MANIFEST = "manifest.json"
MEDIA_PREFIX = "media"
PLUGINS_PREFIX = "plugins"


class BackupError(Exception):
    """Something that stops a backup being taken, or a restore being trusted."""


@dataclass
class BackupReport:
    path: Path
    bytes: int
    counts: dict[str, int] = field(default_factory=dict)
    media_files: int = 0
    media_bytes: int = 0
    plugin_files: int = 0
    plugin_bytes: int = 0
    plugins: list[str] = field(default_factory=list)


@dataclass
class RestoreReport:
    counts: dict[str, int] = field(default_factory=dict)
    media_files: int = 0
    media_skipped: int = 0
    plugin_files: int = 0
    plugins_skipped: int = 0
    plugins: list[str] = field(default_factory=list)
    #: Whether the secrets just restored are readable under this instance's key. ``None``
    #: when the archive predates the fingerprint and there is nothing to compare.
    key_matches: bool | None = None
    #: How many connections in the restored database actually hold an encrypted secret,
    #: so that a mismatch can be reported as a number of broken things and not a worry.
    connections_with_secrets: int = 0


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


def _connections_with_secrets() -> int:
    from postulo.plugins.models import Connection

    return Connection.objects.exclude(secrets_encrypted="").count()


def busy_reason() -> str | None:
    """What else is using the database right now, in words, or ``None`` if nothing is.

    A restore does not write a new database and swap it in: it overwrites the one that is
    there, through SQLite's backup API or ``pg_restore --clean``. A gunicorn worker or the
    scheduler reading through that is reading a database that is changing underneath it,
    and the answers it gives are nobody's.

    What can be seen differs by engine, and neither engine sees everything:

    * **PostgreSQL** lists every backend connected to the database, so both the web
      service and the scheduler show up whether or not they are doing anything.
    * **SQLite** in WAL mode keeps ``-wal`` and ``-shm`` beside the file for exactly as
      long as a connection is open, and removes them when the last one closes. So this
      closes Postulo's own connection and looks: if ``-shm`` is still there, somebody else
      has the database open. The scheduler's loop holds a connection between passes and is
      caught; a gunicorn that has served nothing for a while has closed its own and is
      not. A ``-shm`` left behind by a process that was killed reads as busy, which is the
      safe way round to be wrong.

    Which is why the documented procedure stops the services rather than trusting this:
    the check is a second pair of eyes, not the lock.
    """
    vendor = database_vendor()
    if vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
            others = cursor.fetchone()[0]
        if others:
            word = "connection" if others == 1 else "connections"
            return f"{others} other {word} to this database {'is' if others == 1 else 'are'} open"
        return None
    if vendor == "sqlite":
        database = settings.DATABASES["default"]
        if not sqlite_options.is_file(database):
            return None
        path = Path(str(database["NAME"]))
        # Our own connection keeps the sidecars alive, so it has to go first. Django opens
        # a new one on the next query.
        connection.close()
        if path.with_name(f"{path.name}-shm").exists():
            return f"another process has {path.name} open"
        return None
    return None


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


def _plain_files_only(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Leave out anything that is neither a plain file nor a directory.

    A restore refuses such a member, because a symlink in an archive is a way of writing
    outside the directory it claims to belong to. An installer that left one in the plugins
    directory would otherwise produce an archive this same Postulo will not put back, which
    is the worst moment to find out.
    """
    return info if info.isfile() or info.isdir() else None


def _tree_stats(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for entry in root.rglob("*"):
        if entry.is_file():
            files += 1
            size += entry.stat().st_size
    return files, size


def _installed_plugins() -> list[str]:
    """``name==version`` for every plugin the record lists, for the manifest to carry.

    Read even when the directory itself is left out, because knowing what was installed is
    what lets a restore onto a fresh instance say why a connection has nothing behind it.
    """
    from postulo.plugins.installing import read_record

    return [f"{entry.name}=={entry.version}" for entry in read_record()]


def write_backup(
    target: Path | str | None = None,
    *,
    include_media: bool = True,
    include_plugins: bool = True,
) -> BackupReport:
    """Take a backup, verify it, and say what it holds."""
    from postulo.plugins import secrets

    path = resolve_target(target)
    media_root = Path(settings.MEDIA_ROOT)
    with_media = include_media and media_root.is_dir()
    media_files, media_bytes = _tree_stats(media_root) if with_media else (0, 0)

    plugins_root = Path(settings.POSTULO_PLUGINS_DIR)
    with_plugins = include_plugins and plugins_root.is_dir()
    plugin_files, plugin_bytes = _tree_stats(plugins_root) if with_plugins else (0, 0)
    plugins = _installed_plugins()

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
            "plugins": {
                "included": with_plugins,
                "files": plugin_files,
                "bytes": plugin_bytes,
                "installed": plugins,
            },
            # The mark of the key, never the key: an archive is a copy of the database and
            # this is what says whether the secrets in it are still readable.
            "secrets": {
                "field_key_sha256": secrets.fingerprint(),
                "field_key_source": secrets.key_source(),
                "connections": _connections_with_secrets(),
            },
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
                archive.add(
                    media_root, arcname=MEDIA_PREFIX, recursive=True, filter=_plain_files_only
                )
            if with_plugins:
                archive.add(
                    plugins_root, arcname=PLUGINS_PREFIX, recursive=True, filter=_plain_files_only
                )

    verify_backup(path)
    return BackupReport(
        path=path,
        bytes=path.stat().st_size,
        counts=manifest["counts"],
        media_files=media_files,
        media_bytes=media_bytes,
        plugin_files=plugin_files,
        plugin_bytes=plugin_bytes,
        plugins=plugins,
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
    # Anything up to the current format, because every change so far has added members and
    # manifest keys rather than moved them: an archive older than this Postulo is exactly
    # the archive somebody restores from, and refusing it on the version line would be the
    # one refusal that arrives when it is too late to take another.
    if not isinstance(fmt, int) or isinstance(fmt, bool) or not 1 <= fmt <= BACKUP_FORMAT:
        raise BackupError(
            f"This archive is backup format {fmt!r}; this version of Postulo reads "
            f"1 to {BACKUP_FORMAT}."
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


def _safe_member_path(name: str, prefix: str) -> PurePosixPath:
    """The path under ``prefix``'s root a member may be written to, or an error."""
    relative = PurePosixPath(name)
    parts = relative.parts
    if not parts or parts[0] != prefix:
        raise BackupError(f"Unexpected member outside {prefix}/: {name!r}")
    rest = parts[1:]
    if not rest:
        raise BackupError(f"bare {prefix}/")
    if relative.is_absolute() or any(part in ("..", "") for part in rest):
        raise BackupError(f"Refusing a member that escapes the {prefix} directory: {name!r}")
    return PurePosixPath(*rest)


def _prefix_of(name: str) -> str | None:
    """Which directory a member belongs to, or ``None`` if it belongs to neither."""
    first = PurePosixPath(name).parts[:1]
    if first and first[0] in (MEDIA_PREFIX, PLUGINS_PREFIX):
        return first[0]
    return None


def restore_backup(path: Path | str, *, force: bool = False) -> RestoreReport:
    """Put an archive back: database, then media and plugins, then migrations."""
    from postulo.plugins import secrets

    path = Path(path)
    manifest = verify_backup(path)
    engine = manifest["database"]["engine"]
    if engine != database_vendor():
        raise BackupError(
            f"This archive came from a {engine} database; this instance runs {database_vendor()}."
        )
    # Asked before anything else touches the database, because on SQLite the question is
    # answered by closing every connection and seeing whether the sidecars go with them.
    if not force and (reason := busy_reason()):
        raise BackupError(
            f"Something else is using the database: {reason}. A restore overwrites it "
            "underneath whatever is reading it. Stop the web service and the scheduler "
            "first — in the container, `docker compose stop postulo scheduler`, then "
            "`docker compose run --rm -e POSTULO_SKIP_MIGRATE=1 postulo python manage.py "
            "restore ...` — or pass --force if you are certain nothing else is connected."
        )
    if get_user_model().objects.exists() and not force:
        raise BackupError(
            "This instance is not empty. Restoring would replace everything on it; "
            "pass --force if that is what you mean."
        )

    roots = {
        MEDIA_PREFIX: Path(settings.MEDIA_ROOT),
        PLUGINS_PREFIX: Path(settings.POSTULO_PLUGINS_DIR),
    }
    report = RestoreReport(plugins=list((manifest.get("plugins") or {}).get("installed") or []))
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

        # Every member is checked before anything is written, so a hostile archive writes
        # nothing at all rather than half of something.
        files = []
        for entry in archive.getmembers():
            if entry.name == MANIFEST or entry.name == member:
                continue
            prefix = _prefix_of(entry.name)
            if prefix is None:
                raise BackupError(f"Unexpected member outside media/ and plugins/: {entry.name!r}")
            if entry.isdir():
                if entry.name != prefix:
                    _safe_member_path(entry.name, prefix)
                continue
            if not entry.isfile():
                raise BackupError(f"Refusing a member that is not a plain file: {entry.name!r}")
            files.append((entry, prefix, _safe_member_path(entry.name, prefix)))

        load_database(dump, member)

        for entry, prefix, relative in files:
            destination = roots[prefix] / Path(*relative.parts)
            if destination.exists() and not force:
                if prefix == MEDIA_PREFIX:
                    report.media_skipped += 1
                else:
                    report.plugins_skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(entry)
            if source is None:
                continue
            with destination.open("wb") as out:
                shutil.copyfileobj(source, out)
            if prefix == MEDIA_PREFIX:
                report.media_files += 1
            else:
                report.plugin_files += 1

    # An older backup lands on a newer Postulo: bring it forward.
    call_command("migrate", interactive=False, verbosity=0)
    report.counts = _counts()
    report.connections_with_secrets = _connections_with_secrets()
    recorded = (manifest.get("secrets") or {}).get("field_key_sha256")
    if recorded:
        report.key_matches = recorded == secrets.fingerprint()
    return report


def as_dict(report: BackupReport | RestoreReport) -> dict:
    data = asdict(report)
    if "path" in data:
        data["path"] = str(data["path"])
    return data
