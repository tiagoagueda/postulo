"""The slow work core asks for: building an export archive (#247).

An export reads every record and every file an account owns and zips the lot. Done inside
the request it was one long POST holding a gunicorn worker; done by a worker it becomes a
file, and a file is a thing somebody has to look after.

**Written to a temporary file rather than to memory.** `write_archive` took a target long
before this, and a `BytesIO` in a worker process is the same megabytes held for the same
time with nobody waiting on them. The temporary file is handed to the `ExportArchive` and
then removed, so at no point are there two copies for long.

**It expires.** One archive per export, holding the whole of somebody's job search; the
scheduler deletes what has run out, bytes and row together. `POSTULO_EXPORT_KEEP_HOURS`
says how long, and a day is the default because an export is something a person asks for
and then downloads.

**It is served like every other personal document**: through an ownership-checked view,
never from the media directory.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .errands import handler


@handler("export", working=_("Packing up your data"))
def build_an_export(errand) -> dict:
    """Build the archive, keep it as a file, and say where to fetch it."""
    from django.urls import reverse

    from .export import suggested_filename, write_archive
    from .models import ExportArchive

    owner = errand.owner
    name = suggested_filename(owner)
    hours = int(getattr(settings, "POSTULO_EXPORT_KEEP_HOURS", 24) or 24)

    handle, temporary = tempfile.mkstemp(prefix="postulo-export-", suffix=".zip")
    path = Path(temporary)
    try:
        with open(handle, "w+b") as writing:
            write_archive(owner, writing)
        with path.open("rb") as reading:
            archive = ExportArchive(
                owner=owner,
                filename=name,
                size=path.stat().st_size,
                expires_at=timezone.now() + timedelta(hours=hours),
            )
            archive.file.save(name, File(reading), save=True)
    finally:
        path.unlink(missing_ok=True)

    return {
        "message": str(_("Your data is ready to download.")),
        "url": reverse("core:export_archive", args=[archive.pk]),
        "archive_id": archive.pk,
    }


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
