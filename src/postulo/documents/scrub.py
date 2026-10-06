"""Looking for the two kinds of damage a kept file can have, and the files nothing names (#663).

A file is damaged when it is **missing** or when it **no longer matches** the size and the
SHA-256 recorded when it arrived. Both are found by reading it, which is why this runs in
slices on a schedule rather than on every download: with a proxy handing the bytes over,
the application never reads them, and hashing a 20 MB file twice per download costs a
worker. A download compares nothing; the scrub, and a copy about to leave, do.

**Nothing here deletes a damaged file.** It may be the only copy of somebody's diploma, and
an unattended job removing it is the failure `prune_media` was written not to have. The row
says so, the Files page tells the person, and they upload a replacement as a new version.
A file no row names is another matter and is `sweep`'s: removed only once it is older than a
grace period, so that a request about to attach it is not robbed of it.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from postulo.core import media

from . import filestore, integrity
from .models import RenderedDocument, UploadedDocument

logger = logging.getLogger(__name__)


@dataclass
class Findings:
    """What a scrub looked at, and what it found."""

    checked: int = 0
    missing: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    stray: list[str] = field(default_factory=list)

    @property
    def damaged(self) -> int:
        return len(self.missing) + len(self.changed)


def verify_uploads(*, limit: int | None = None, now=None) -> Findings:
    """Read uploads, the least recently checked first, and record what each one is.

    Writes the verdict, the moment and, for a row from before the size was kept, the size
    -- and nothing else: not the file, not the checksum, which is what the file is held to.
    """
    now = now or timezone.now()
    findings = Findings()
    rows = UploadedDocument.objects.exclude(file="").order_by("verified_at", "pk")
    for upload in rows[:limit] if limit else rows:
        findings.checked += 1
        found = integrity.damage_of(upload.file.name, size=upload.size, checksum=upload.checksum)
        size = upload.size
        if size is None and not found:
            size = filestore.size(upload.file.name)
        UploadedDocument.objects.filter(pk=upload.pk).update(
            damage=found, verified_at=now, size=size
        )
        if found == integrity.Damage.MISSING:
            findings.missing.append(upload.file.name)
        elif found:
            findings.changed.append(upload.file.name)
    return findings


def verify_renders(findings: Findings) -> Findings:
    """The same questions of every snapshot, whose figure is the checksum alone."""
    for render in RenderedDocument.objects.exclude(file=""):
        findings.checked += 1
        found = integrity.damage_of(render.file.name, size=None, checksum=render.checksum)
        if found == integrity.Damage.MISSING:
            findings.missing.append(render.file.name)
        elif found:
            findings.changed.append(render.file.name)
    return findings


def strays(*, grace: dt.timedelta | None = None) -> list[filestore.Stored]:
    """Files under the media root that no row names, and old enough when ``grace`` says."""
    older_than = timezone.now() - grace if grace is not None else None
    return list(media.unreferenced(older_than=older_than))


def verify(*, limit: int | None = None) -> Findings:
    """Uploads (a slice, if ``limit``), every render, and the files nothing names."""
    findings = verify_uploads(limit=limit)
    if limit is None:
        verify_renders(findings)
    findings.stray = [stored.name for stored in strays()]
    return findings


def grace() -> dt.timedelta:
    return dt.timedelta(hours=max(1, int(settings.POSTULO_MEDIA_SWEEP_GRACE_HOURS)))


def sweep(*, now=None) -> list[str]:
    """Remove the files no row names that are older than the grace period. Their names.

    The scheduled pass's half of "no orphan survives": a request that fails after its file
    was written rolls the row back and leaves the file, and Django offers no hook for that.
    Logged line by line, and off with ``POSTULO_MEDIA_SWEEP=false``.
    """
    if not settings.POSTULO_MEDIA_SWEEP:
        return []
    removed: list[str] = []
    for stored in strays(grace=grace()):
        try:
            filestore.delete(stored.name)
        except OSError:  # pragma: no cover - a file already gone is the outcome wanted
            logger.warning("Could not remove the orphan %s", stored.name, exc_info=True)
            continue
        logger.info("Removed %s: no record names it", stored.name)
        removed.append(stored.name)
    return removed
