"""What a kept file looks like before it is opened: its kind of preview, and its thumbnail (#942).

Two questions, answered here and nowhere else, so that the page, the preview view and the
thumbnail view cannot disagree about them.

**What kind of preview a file gets** is read from its *extension* and from nothing a person
typed (#771): a PDF is drawn page by page by the viewer, a PNG or JPEG is a picture, a plain
text file is text, and everything else -- a Word file, an OpenDocument file, an RTF -- has no
preview and is offered as a download. A converter that would draw those is an optional
plugin, not part of the core.

**A thumbnail** is the first page of a PDF, or a picture made smaller, as a WebP at most
`WIDTH` pixels wide. It is a *derived* file: kept in the store under the owner's own folder
at a name worked out from the file's own, so there is no row, no column and nothing to export
or migrate; it is made the first time one is asked for, and made again if it is gone. The
sweep that finds files nobody names (`core.media`), the deletion of a file and the deletion of
an account all know the name (`thumbnail_names`), so a thumbnail is never an orphan and never
outlives what it shows.

**Rasterising a PDF is a parser on a stranger's bytes**, and it is not done in the worker
that answers a request: `pypdfium2` runs in a process of its own (`thumbnailer`), under a time
limit and, where the system has them, limits on memory and processor time, and whatever goes
wrong there -- a broken file, a password, a bomb, a crash -- is no thumbnail and no traceback.
A picture is decoded here through `core.pictures.decode`, which refuses what is too large.
"""

from __future__ import annotations

import functools
import importlib.util
import io
import logging
import posixpath
import subprocess
import sys
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from postulo.core import pictures

from .filestore import thumbnail_name

logger = logging.getLogger(__name__)

#: The widest a thumbnail is drawn, in pixels (the list shows them smaller; this is twice that
#: for a screen that doubles everything) and the tallest, so a page far taller than it is wide
#: is cut short rather than made into a column.
WIDTH = 400
HEIGHT = 560

#: How long the process that rasterises a PDF may take before it is stopped, and what it may
#: be given: a PDF over this size is not worth a thumbnail.
TIME_LIMIT = 20
MAX_PDF_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True)
class Preview:
    """What the viewer is to do with one file."""

    #: ``pdf``, ``image``, ``text``, or ``none`` where the file has no preview.
    kind: str
    #: The type the preview is answered as, or ``""`` where there is none.
    content_type: str


#: Extension to preview. The whole list of what is ever answered *inline*: a type not here is
#: an attachment, whatever its name or its title says (#771).
BY_EXTENSION = {
    ".pdf": Preview("pdf", "application/pdf"),
    ".png": Preview("image", "image/png"),
    ".jpg": Preview("image", "image/jpeg"),
    ".jpeg": Preview("image", "image/jpeg"),
    ".txt": Preview("text", "text/plain; charset=utf-8"),
}
NONE = Preview("none", "")

#: The kinds that get a thumbnail: text has none, and a kind icon stands in for it.
THUMBNAILED = frozenset({"pdf", "image"})


def preview_of(filename: str) -> Preview:
    """What a file called ``filename`` is previewed as, from its last extension alone."""
    extension = posixpath.splitext(filename or "")[1].lower()
    return BY_EXTENSION.get(extension, NONE)


@functools.cache
def can_rasterise() -> bool:
    """Whether the PDF library is installed (the ``preview`` extra)."""
    return importlib.util.find_spec("pypdfium2") is not None


def _from_pdf(data: bytes) -> bytes | None:
    """The first page of a PDF as WebP bytes, made in a process of its own, or nothing."""
    if not can_rasterise() or len(data) > MAX_PDF_BYTES:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - our own interpreter and module, fixed arguments
            [sys.executable, "-I", "-m", "postulo.documents.thumbnailer", str(WIDTH), str(HEIGHT)],
            input=data,
            capture_output=True,
            timeout=TIME_LIMIT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        logger.info("A PDF could not be drawn for a thumbnail in time")
        return None
    if done.returncode != 0 or not done.stdout.startswith(b"RIFF"):
        return None
    return done.stdout


def _from_picture(data: bytes) -> bytes | None:
    """A PNG or JPEG made smaller, as WebP bytes, through the restricted decode (#777)."""
    try:
        with pictures.decode(
            data, kinds={"image/png", "image/jpeg"}, draft_to=max(WIDTH, HEIGHT)
        ) as opened:
            image = opened.convert("RGBA" if opened.mode in ("RGBA", "LA", "P") else "RGB")
            image.thumbnail((WIDTH, HEIGHT))
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=80)
            return out.getvalue()
    except (pictures.UnusablePicture, OSError, ValueError):
        return None


def has_thumbnail(filename: str) -> bool:
    """Whether a thumbnail may be asked for a file of this name (a PDF needs the library)."""
    kind = preview_of(filename).kind
    return kind == "image" or (kind == "pdf" and can_rasterise())


def make(data: bytes, filename: str) -> bytes | None:
    """The thumbnail of a file's bytes, or nothing when it cannot be made. Never raises."""
    kind = preview_of(filename).kind
    try:
        if kind == "pdf":
            return _from_pdf(data)
        if kind == "image":
            return _from_picture(data)
    except Exception:  # a thumbnail is never worth an error page
        logger.warning("A thumbnail could not be made", exc_info=True)
    return None


def thumbnail_bytes(file_field, filename: str) -> bytes | None:
    """The thumbnail of a kept file, made now if it has none; nothing when there is none to make.

    A file that cannot be drawn leaves an *empty* file at the thumbnail's name, so it is
    tried once and not on every request; the bytes are then nothing, as they are for a kind
    that has none. Performs no permission checking: the caller has established who is asking.
    """
    if preview_of(filename).kind not in THUMBNAILED:
        return None
    name = thumbnail_name(file_field.name)
    if not name:
        return None
    if default_storage.exists(name):
        with default_storage.open(name, "rb") as kept:
            return kept.read() or None
    try:
        with file_field.storage.open(file_field.name, "rb") as source:
            data = source.read(MAX_PDF_BYTES + 1)
    except OSError:
        return None
    made = make(data, filename)
    saved = default_storage.save(name, ContentFile(made or b""))
    if saved != name:  # another request wrote it first; keep one
        default_storage.delete(saved)
    return made


def remove(file_name: str) -> None:
    """Delete the thumbnail of a file that is going."""
    name = thumbnail_name(file_name)
    if name and default_storage.exists(name):
        try:
            default_storage.delete(name)
        except OSError:  # pragma: no cover - a thumbnail already gone is what was wanted
            logger.warning("Could not remove %s from storage", name, exc_info=True)
