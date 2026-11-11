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
from django.core.cache import cache
from django.db.models import Q
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


def reread_after() -> dt.timedelta:
    """How long the scheduled slice leaves a file it has read before reading it again."""
    return dt.timedelta(days=max(0, int(settings.POSTULO_SCRUB_EVERY_DAYS)))


def verify_uploads(*, limit: int | None = None, now=None) -> Findings:
    """Read uploads, the least recently checked first, and record what each one is.

    Writes the verdict, the moment and, for a row from before the size was kept, the size
    -- and nothing else: not the file, not the checksum, which is what the file is held to.

    With a ``limit`` this is the scheduled slice, and it takes only the uploads never read or
    last read more than ``POSTULO_SCRUB_EVERY_DAYS`` ago (#1118): the pass comes round every
    five minutes, and without that a hundred uploads were each read and hashed 72 times a
    day. Without one it is somebody asking, and it reads every file.
    """
    now = now or timezone.now()
    findings = Findings()
    rows = UploadedDocument.objects.exclude(file="").order_by("verified_at", "pk")
    if limit:
        rows = rows.filter(Q(verified_at__isnull=True) | Q(verified_at__lt=now - reread_after()))
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


#: The most of the files an unattended pass may remove, and the number below which the share is
#: not asked (#775). A database that names nothing -- a wrong URL after an upgrade, an empty
#: schema, a restore that has replaced the database and not yet the media -- makes every file
#: a stray, and a pass that agreed would delete every person's documents. A real backlog of
#: orphans is a few files from failed requests, never most of what is kept.
SWEEP_MAX_SHARE = 0.5
SWEEP_SMALL = 10


def sweep(*, now=None) -> list[str]:
    """Remove the files no row names that are older than the grace period. Their names.

    The scheduled pass's half of "no orphan survives": a request that fails after its file
    was written rolls the row back and leaves the file, and Django offers no hook for that.
    A worker killed while it wrote an export leaves one too: the dot-file the archive was
    being written under, beside the archives (#1125). One still being written is safe, since
    a file is as old as its last write and the grace period is an hour at the least.
    Logged line by line, and off with ``POSTULO_MEDIA_SWEEP=false``.

    **It has a floor** (#775): a pass that finds more than `SWEEP_MAX_SHARE` of the files kept to
    be strays, once there are more than `SWEEP_SMALL` of them, removes nothing and says so in
    the log, because the likelier fault is the database it is looking at. Someone who really
    has that many orphans removes them with ``manage.py prune_media``, which asks.
    """
    if not settings.POSTULO_MEDIA_SWEEP:
        return []
    removed: list[str] = []
    # The strays and the number of files kept, from the one walk (#1119).
    found, kept = media.survey(older_than=timezone.now() - grace())
    if len(found) > SWEEP_SMALL and kept and len(found) > kept * SWEEP_MAX_SHARE:
        logger.error(
            "The media sweep would remove %d of %d files; it removed none. If the database "
            "is the right one, run `manage.py prune_media`.",
            len(found),
            kept,
        )
        return []
    for stored in found:
        try:
            filestore.delete(stored.name)
        except OSError:  # pragma: no cover - a file already gone is the outcome wanted
            logger.warning("Could not remove an orphan file", exc_info=True)
            continue
        removed.append(stored.name)
    if removed:
        # How many, never which: a file's name carries what its owner called it (#797).
        logger.info("Removed %d orphan file(s): no record names them", len(removed))
    return removed


#: How long the scheduler's pass leaves between two sweeps, and the key in the cache that says
#: one was made (#1119).
SWEEP_EVERY_SECONDS = 3600
SWEEP_KEY = "documents:media-swept"


def sweep_if_due() -> list[str]:
    """`sweep`, once an hour at most: the one the scheduler's pass calls (#1119).

    The pass comes round every five minutes, and a sweep walks the whole media tree and reads
    every file field of every table. A file has to be older than the grace period to go, a
    day unless the operator says otherwise, so each sweep could find only what had come of
    age in the five minutes since the last. Once an hour removes every orphan an hour later
    at most.

    The hour is a key in the cache, as it is for `core.slow.tidy_up_if_due`: every process
    sees the same one, and a scheduler that restarts does not start the hour again. It does
    not have to be exact, since the pass that calls this already runs one at a time: a key
    lost early costs one more walk. A cache that is not answering costs a walk on every pass,
    as before, and never the pass. A sweep that is switched off does not take the key, so
    switching it on is not followed by an hour of nothing.
    """
    if not settings.POSTULO_MEDIA_SWEEP:
        return []
    try:
        due = cache.add(SWEEP_KEY, True, SWEEP_EVERY_SECONDS)
    except Exception:  # a cache that is down is no reason to end the pass, or to keep orphans
        due = True
    return sweep() if due else []
