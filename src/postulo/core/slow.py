"""The slow work core asks for: building an export archive (#247).

An export reads every record and every file an account owns and zips the lot. Done inside
the request it was one long POST holding a gunicorn worker; done by a worker it becomes a
file, and a file is a thing somebody has to look after.

**Written to a file rather than to memory, and written once** (#1125). `write_archive` took
a target long before this, and a `BytesIO` in a worker process is the same megabytes held
for the same time with nobody waiting on them. The file is made where the archives are
kept, in the person's own folder of them, as a dot-file; when it is whole the store takes
it by renaming it. It used to be made in the system's temporary directory and copied from
there, which wrote every export twice, and where that directory is memory (the hardened
compose file mounts `/tmp` as tmpfs) held the whole archive in the worker's allowance
meanwhile.

**A worker killed half way leaves the dot-file, and not for good.** No row names it and
nothing serves it, so the sweep of files nothing names (`documents.scrub.sweep`) removes it
once it is older than the grace period. A file is as old as its last write and an export
writes as it goes, so one that is still being written is not that old: the grace is an
hour at the least, and a day unless the operator says otherwise.

**It expires.** One archive per export, holding the whole of somebody's job search; the
scheduler deletes what has run out, bytes and row together. `POSTULO_EXPORT_KEEP_HOURS`
says how long, and a day is the default because an export is something a person asks for
and then downloads.

**It is served like every other personal document**: through an ownership-checked view,
never from the media directory.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .errands import handler

logger = logging.getLogger(__name__)

#: What an export is called while it is being written (#1125): a dot-file in the person's
#: folder of archives, which no row names and nothing lists or serves.
SCRATCH_PREFIX = ".postulo-export-"
SCRATCH_SUFFIX = ".partial"


class _Finished(File):
    """A file that is whole and on this machine, for a store to take rather than to copy.

    `FileSystemStorage` renames whatever has a `temporary_file_path`, which is how Django
    keeps an upload it had spooled to disk. Any other store reads it, as it must, and gets a
    file like any other. Nothing is opened until something reads, and `chunks` closes what
    it opened, because Windows will not rename a file that is open.
    """

    def __init__(self, path: Path, name: str) -> None:
        self._path = path
        self._handle = None
        self.name = name

    def temporary_file_path(self) -> str:
        return str(self._path)

    @property
    def file(self):
        if self._handle is None:
            self._handle = self._path.open("rb")
        return self._handle

    @property
    def size(self) -> int:
        return self._path.stat().st_size

    def chunks(self, chunk_size=None):
        with self._path.open("rb") as reading:
            while piece := reading.read(chunk_size or self.DEFAULT_CHUNK_SIZE):
                yield piece

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _scratch(folder: str) -> Path:
    """An empty file to write an archive into, inside ``folder`` of the store when it can be.

    Made by the store itself, so the file and the folders above it have the modes every kept
    file has, and on the volume the archive will be kept on, so that keeping it is a rename.
    A store that is not a directory on this machine has no such place: there the file is in
    the system's temporary directory, as it always was, and the store copies it.
    """
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from postulo.documents import filestore

    name = f"{folder}{SCRATCH_PREFIX}{uuid.uuid4().hex}{SCRATCH_SUFFIX}"
    if filestore.local_path(name) is not None:
        made = default_storage.save(name, ContentFile(b""))
        path = filestore.local_path(made)
        if path is not None and path.is_file():
            return path
        # A store that names a path and keeps no file there, as the one in memory does.
        filestore.delete(made)
    handle, temporary = tempfile.mkstemp(prefix="postulo-export-", suffix=".zip")
    os.close(handle)
    return Path(temporary)


@handler("export", working=_("Packing up your data"))
def build_an_export(errand) -> dict:
    """Build the archive, keep it as a file, and say where to fetch it."""
    from django.urls import reverse

    from .export import suggested_filename, write_archive
    from .models import ExportArchive

    owner = errand.owner
    name = suggested_filename(owner)
    hours = int(getattr(settings, "POSTULO_EXPORT_KEEP_HOURS", 24) or 24)

    scratch = _scratch(ExportArchive(owner=owner).storage_prefix)
    whole = _Finished(scratch, name)
    try:
        with scratch.open("wb") as writing:
            write_archive(owner, writing)
        archive = ExportArchive(
            owner=owner,
            filename=name,
            size=scratch.stat().st_size,
            expires_at=timezone.now() + timedelta(hours=hours),
        )
        archive.file.save(name, whole, save=True)
    finally:
        whole.close()
        try:
            # Nothing to remove once the store has taken it; an export that failed has.
            scratch.unlink(missing_ok=True)
        except OSError:
            # Held open by something else, which only Windows minds. No row names it, so
            # the sweep removes it; the archive, if it was kept, is no worse for it.
            logger.warning("Could not remove an export's scratch file", exc_info=True)

    return {
        "message": str(_("Your data is ready to download.")),
        "url": reverse("core:export_archive", args=[archive.pk]),
        "archive_id": archive.pk,
    }


def _group_of(user, into: str):
    """One of ``user``'s groups, named ``group:<pk>``, or nobody: theirs (#896)."""
    if not (into or "").startswith("group:"):
        return None
    try:
        return user.groups.filter(pk=int(into.split(":", 1)[1])).first()
    except ValueError:
        return None


@handler("csv_import", working=_("Importing your spreadsheet"))
def import_a_spreadsheet(errand) -> dict:
    """Read the held sheet and bring it in, chunk by chunk, then say how it went (#555).

    The sheet travels in the errand's payload because a worker has no session; it is dropped
    from the row as soon as it has been read, so a finished errand holds only the report.
    """
    import base64

    from django.urls import reverse
    from django.utils.translation import ngettext

    from . import csv_import
    from .models import Errand

    asked = errand.payload
    sheet = csv_import.read_sheet(base64.b64decode(asked["data"]), asked["filename"])
    Errand.objects.filter(pk=errand.pk).update(payload={})
    report = csv_import.perform(
        errand.owner,
        sheet,
        asked["mapping"],
        day_first=asked["day_first"],
        currency=asked["currency"],
        into=_group_of(errand.owner, asked.get("into", "")),
    )
    return {
        "message": ngettext(
            "Imported %(count)s row from %(file)s.",
            "Imported %(count)s rows from %(file)s.",
            report.applications + report.listings,
        )
        % {"count": report.applications + report.listings, "file": report.filename},
        "url": reverse("core:import_csv_done", args=[errand.pk]),
        "filename": report.filename,
        "rows": report.rows,
        "applications": report.applications,
        "listings": report.listings,
        "companies_created": report.companies_created,
        "skipped": report.skipped,
    }


@handler("backup", working=_("Taking a backup"))
def take_a_backup(errand) -> dict:
    """Write a verified archive of the whole instance (#242).

    An errand rather than a request, because on SQLite a request holds the write lock for
    as long as it runs (#220) and an archive with media takes minutes.
    """
    from django.urls import reverse

    from . import backups
    from .backup import BackupError
    from .errands import Refused

    try:
        path = backups.take()
    except backups.Busy as busy:
        raise Refused(str(_("A backup is already running."))) from busy
    except BackupError as error:
        backups.record(False, str(error))
        raise Refused(str(error)) from error
    except Exception:
        backups.record(False, str(_("Something went wrong. The server log has the details.")))
        raise
    backups.record(True)
    return {
        "message": str(_("The backup is written and checked.")),
        "url": reverse("server:backups"),
        "archive": path.name,
    }


def fail_interrupted_backups() -> int:
    """Say so on a *Back up now* whose worker was killed. Called by the scheduler (#1131).

    Every errand is given a day before it is called interrupted, because nothing says
    whether its worker is alive. A backup does say: it renews its claim while it runs. So
    one that began longer ago than the claim lasts, with no claim held, is not running.
    """
    from . import backups
    from .errands import interrupt
    from .models import Errand, ErrandState

    if backups.running():
        return 0
    cutoff = timezone.now() - timedelta(seconds=backups.LOCK_SECONDS)
    return interrupt(
        Errand.objects.filter(kind="backup", state=ErrandState.WORKING, started_at__lt=cutoff)
    )


def reap_archives() -> int:
    """Delete the archives that have run out. Called by the scheduler.

    One per export, each the size of somebody's whole job search, so leaving them is not an
    option -- and the row goes with the bytes, so *your export is ready* never points at a
    file that is not there.
    """
    from .models import ExportArchive

    gone = 0
    for archive in ExportArchive.objects.filter(expires_at__lte=timezone.now()):
        archive.delete()
        gone += 1
    return gone


#: How long a request that has just tidied up leaves the next one alone.
TIDY_EVERY_SECONDS = 3600


def tidy_up_if_due() -> None:
    """Expire archives and unconfirmed captured pages, from a request, at most once an hour.

    The scheduler is optional, and an instance run without it kept every archive for good
    while the export page said it is deleted after a day (#539). So the deletion does not
    depend on it: the pages that deal in archives ask for it, and the cache says whether an
    hour has passed since somebody last did. Whoever loses the race, or finds the cache
    gone, does nothing, and the scheduler's pass remains the one that always runs.
    """
    from django.core.cache import cache

    from postulo.jobs import pages

    try:
        if not cache.add("core:tidied-up", True, TIDY_EVERY_SECONDS):
            return
    except Exception:  # a cache that is down is no reason to fail an export page
        return
    from postulo.accounts import sessions

    reap_archives()
    pages.expire_unconfirmed()
    sessions.clear_expired()
    # The cache's own expired entries, which nothing else removes (#1167).
    from . import cache_purge

    cache_purge.purge_if_due()
