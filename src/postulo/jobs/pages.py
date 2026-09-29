"""What a capture keeps of the page it was read from (#256).

A capture reads a page, keeps what it understood, and used to throw the page away. The
reading is a guess and the page is the evidence, and adverts vanish: when one does, the
parsed fields are the only account left of what the job was. So a capture may keep two
things beside them, each optional and each refusable on its own.

**The source**, exactly as it was parsed -- the text `parse_page` was handed, whether the
server fetched it, a person pasted it or a browser extension sent it. Kept because a parser
improves and old captures do not: with the source, a capture can be read again.

**A rendering** of the whole page: a picture or a PDF. Either the browser that was looking
at the page sent it, or this instance drew it from the kept source with scripts, the
network and every outside resource switched off (`jobs.rendering`).

Four rules hold all of it together, and each is here rather than at the call sites because
a rule kept at four call sites is a rule the fifth will not know about.

1. **Two people have to have said yes.** The instance's switch is the administrator's and
   is final; the person's own can narrow it and never widen it; a request can narrow it
   again for one capture. Every one of them is off until somebody turns it on.
2. **Nothing is read before it is measured.** What a file may weigh is compared with what
   the request says it carries before a byte is taken, and counted again as the bytes
   arrive, so a request that lied is stopped at the cap rather than at the end.
3. **The source never leaves as a page.** It is a stranger's markup. It is kept gzipped as
   text, shown as text, downloaded as ``text/plain``, and drawn only by a renderer that can
   run none of it.
4. **Keeping a page never costs the capture.** Every refusal here is a sentence and a
   capture without a page; nothing in this module is allowed to be the reason a posting
   was not captured.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import logging
import secrets
import tempfile
from dataclasses import dataclass

from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Sum
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.core import site

from .kept import (  # noqa: F401 - re-exported: the page views and the tests read them here
    Unreadable,
    read_source,
    read_source_bytes,
    unpack,
)
from .models import Capture, CapturedPage, CaptureStatus, RenderedBy, RenderingKind

logger = logging.getLogger(__name__)

#: How much of an upload is held at once while it is measured and written down.
CHUNK = 64 * 1024

#: How much of a source the page that shows it prints. The whole of it is in the download;
#: a page carrying two megabytes of somebody else's markup as text is a page nobody can
#: scroll, and the first part is what a person checking a reading looks at.
EXCERPT_CHARACTERS = 60_000

#: The file each kind of rendering is kept as. The extension is ours, chosen from the kind,
#: and never anything a client sent.
EXTENSIONS = {
    RenderingKind.PNG: "png",
    RenderingKind.JPEG: "jpg",
    RenderingKind.WEBP: "webp",
    RenderingKind.PDF: "pdf",
}

#: What the source is kept as. `.txt`, never `.html`: a web server pointed at the media
#: directory by mistake answers `page.html.gz` as a gzipped *page*, and that one mistake
#: would be the whole of rule three undone.
SOURCE_EXTENSION = "txt.gz"


def is_what_it_says(kind: str, head: bytes) -> bool:
    """Whether a file's first bytes are those of the kind it was declared as.

    Not a decoder, and deliberately not one: nothing here opens a stranger's image. It is
    what stops a file being answered under a media type it is not -- an HTML document
    declared a PNG would otherwise be stored, and the declaration is all that says what it
    is served as.
    """
    if kind == RenderingKind.PNG:
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == RenderingKind.JPEG:
        return head.startswith(b"\xff\xd8\xff")
    if kind == RenderingKind.WEBP:
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if kind == RenderingKind.PDF:
        return head.startswith(b"%PDF-")
    return False


def rendering_kind(content_type: str | None) -> str | None:
    """The kind a declared media type names, or ``None`` for one Postulo does not keep."""
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    return declared if declared in RenderingKind.values else None


# ---------------------------------------------------------------------- who said yes


@dataclass(frozen=True)
class Keeping:
    """What is kept for one person, and which half of the answer each half gave."""

    #: What the instance allows. An administrator's, or the environment's; final.
    allowed_source: bool
    allowed_rendering: bool
    #: What the person asked for. Means nothing where the instance has said no.
    wanted_source: bool
    wanted_rendering: bool

    @property
    def source(self) -> bool:
        return self.allowed_source and self.wanted_source

    @property
    def rendering(self) -> bool:
        return self.allowed_rendering and self.wanted_rendering

    @property
    def anything(self) -> bool:
        return self.source or self.rendering

    def narrowed(self, *, source: bool = True, rendering: bool = True) -> Keeping:
        """The same answer with less in it, for one capture. It can never hold more."""
        return Keeping(
            allowed_source=self.allowed_source,
            allowed_rendering=self.allowed_rendering,
            wanted_source=self.wanted_source and source,
            wanted_rendering=self.wanted_rendering and rendering,
        )


def keeping_for(person) -> Keeping:
    """What this person's captures keep. The one place the rest of the application asks.

    A call site that read the two switches by hand would be a call site that could read
    one of them, and the one that forgets the instance's is the one that keeps a page an
    administrator said no to.
    """
    profile = getattr(person, "profile", None)
    return Keeping(
        allowed_source=site.capture_keep_source(),
        allowed_rendering=site.capture_keep_rendering(),
        wanted_source=bool(getattr(profile, "keep_page_source", False)),
        wanted_rendering=bool(getattr(profile, "keep_page_rendering", False)),
    )


def keeping_whatever_the_instance_allows() -> Keeping:
    """What an archive being put back may bring with it.

    The person's own switch is not asked: importing their archive is them asking. The
    instance's is, because an administrator's no is about what this machine holds and an
    archive is one more way for it to arrive.
    """
    return Keeping(
        allowed_source=site.capture_keep_source(),
        allowed_rendering=site.capture_keep_rendering(),
        wanted_source=True,
        wanted_rendering=True,
    )


# ------------------------------------------------------------------------- how much


def _weight(pages) -> int:
    totals = pages.aggregate(sources=Sum("source_stored"), renderings=Sum("rendering_size"))
    return (totals["sources"] or 0) + (totals["renderings"] or 0)


def weight_of(person) -> int:
    """What this account's kept pages weigh on disk, in bytes."""
    return _weight(CapturedPage.objects.for_user(person))


def room_left(person) -> int:
    """How many more bytes this account may keep. Never less than nought."""
    return max(0, site.capture_account_max_bytes() - weight_of(person))


def everything_kept() -> dict:
    """How many pages the instance holds and what they weigh, for the page that reports it."""
    pages = CapturedPage.objects.all()
    return {"pages": pages.count(), "bytes": _weight(pages)}


def kept_by(person) -> dict:
    """The same, for one account: what Settings -> Capture says is being kept."""
    pages = CapturedPage.objects.for_user(person)
    return {"pages": pages.count(), "bytes": _weight(pages)}


# ------------------------------------------------------------------------ refusals


class NotKept(Exception):
    """Something was not kept, and why. The message is a sentence for a person.

    ``reason`` is for a program: ``switched-off``, ``decided``, ``already-kept``,
    ``no-room``, ``too-large``, ``unsupported``, ``not-what-it-says``, ``empty``,
    ``no-length``, ``no-source``, ``no-renderer``, ``could-not-draw``. ``limit`` is the cap
    that was met, where the refusal is about size.
    """

    def __init__(self, reason: str, message: str, *, limit: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.limit = limit


def _switched_off() -> NotKept:
    return NotKept("switched-off", str(_("This account does not keep a rendering of a page.")))


def _too_large(limit: int) -> NotKept:
    return NotKept(
        "too-large",
        str(
            _("That is larger than %(limit)s, which is the most this instance keeps.")
            % {"limit": filesizeformat(limit)}
        ),
        limit=limit,
    )


def _no_room() -> NotKept:
    limit = site.capture_account_max_bytes()
    return NotKept(
        "no-room",
        str(
            _(
                "This account's kept pages already weigh what this instance keeps for one "
                "account, which is %(limit)s. Delete some and there is room again."
            )
            % {"limit": filesizeformat(limit)}
        ),
        limit=limit,
    )


def _already_kept() -> NotKept:
    return NotKept("already-kept", str(_("This capture already has a rendering.")))


def _nothing_there() -> NotKept:
    return NotKept("empty", str(_("There was nothing to keep.")))


def _unsupported(content_type: str | None) -> NotKept:
    """A kind Postulo does not keep. What was declared is quoted, and cut short.

    It is a header somebody else wrote, so it is quoted as far as a media type runs and no
    further; the answer it goes into is JSON, which draws nothing.
    """
    declared = (content_type or "").split(";", 1)[0].strip()[:60]
    if not declared:
        return NotKept(
            "unsupported",
            str(
                _(
                    "A rendering is a PNG, JPEG or WebP image, or a PDF, and the request "
                    "did not say which."
                )
            ),
        )
    return NotKept(
        "unsupported",
        str(
            _("A rendering is a PNG, JPEG or WebP image, or a PDF. That was %(kind)s.")
            % {"kind": declared}
        ),
    )


# ---------------------------------------------------------------------- the source


def _name(capture: Capture, extension: str) -> str:
    """A name of our own making. Nothing a stranger or a client chose is in it."""
    return f"{capture.pk}-{secrets.token_hex(8)}.{extension}"


def _page_of(capture: Capture) -> CapturedPage:
    return capture.kept_page or CapturedPage(owner=capture.owner, capture=capture)


def _write(page: CapturedPage, field: str, name: str, content) -> None:
    """Put a file beside the row and save the row, or leave neither.

    The file is written first, because the row has to name it. If the row then cannot be
    saved the file would be bytes on the disk that nothing points at, which is the state
    #217 exists to prevent -- so it is taken back before the failure is passed on.

    The row is saved in a savepoint of its own. A capture keeps its page inside the request
    that makes the capture, and a row that could not be written must leave that request
    able to carry on and answer -- rule four -- rather than a transaction nothing more can
    be asked of.
    """
    held = getattr(page, field)
    held.save(name, content, save=False)
    try:
        with transaction.atomic():
            page.save()
    except BaseException:
        held.storage.delete(held.name)
        raise
    # A capture read before it had a page still says it has none.
    page.capture.page = page


def _claim_rendering(
    capture: Capture, content, *, kind: str, size: int, checksum: str, rendered_by: str
) -> tuple[CapturedPage, bool]:
    """Keep a rendering beside a capture unless one is already there. The row, and whether
    this call's rendering is the one it now holds.

    **A claim, not a check followed by a write.** Two renderings of one capture arriving at
    once -- a client that sent again before the first answer came, a button pressed twice
    -- would both find the column empty, and the second save would leave the first file on
    the disk with nothing pointing at it. So the file is written under a name of its own,
    and the row takes it only where the rendering is still empty: made with it when there is
    no row yet, which the unique capture column settles, or updated where it is blank. The
    loser's file is taken back at once.
    """
    blank = CapturedPage(owner_id=capture.owner_id, capture_id=capture.pk)
    blank.rendering.save(_name(capture, EXTENSIONS[kind]), content, save=False)
    stored, storage = blank.rendering.name, blank.rendering.storage
    held = {
        "rendering": stored,
        "rendering_type": kind,
        "rendering_size": size,
        "rendering_checksum": checksum,
        "rendered_by": rendered_by,
    }
    try:
        page, created = CapturedPage.objects.get_or_create(
            capture_id=capture.pk, defaults={"owner_id": capture.owner_id, **held}
        )
        claimed = created or bool(
            CapturedPage.objects.filter(pk=page.pk, rendering="").update(
                updated_at=timezone.now(), **held
            )
        )
    except BaseException:
        storage.delete(stored)
        raise
    if not claimed:
        storage.delete(stored)
    page.refresh_from_db()
    capture.page = page
    return page, claimed


def keep_source(capture: Capture, html: str, keeping: Keeping | None = None) -> CapturedPage:
    """Keep what was parsed beside the capture, or say why not.

    Raises :class:`NotKept`. `keep_source_quietly` is the form a capture in progress uses,
    because a capture is never refused on account of its page.
    """
    keeping = keeping or keeping_for(capture.owner)
    if not keeping.source:
        raise NotKept("switched-off", str(_("This account does not keep the source of a page.")))

    # What was parsed is a string; what is kept is its UTF-8, which reads back as the same
    # string. A lone surrogate cannot be written as UTF-8 at all -- JSON can carry one --
    # so it is replaced rather than allowed to make the file unreadable.
    text = html.encode("utf-8", errors="replace")
    limit = site.capture_source_max_bytes()
    if len(text) > limit:
        raise _too_large(limit)
    if not text:
        raise _nothing_there()

    # No timestamp in the header: the same source is the same file, whenever it was kept.
    packed = gzip.compress(text, mtime=0)
    if len(packed) > room_left(capture.owner):
        raise _no_room()

    page = _page_of(capture)
    previous = page.source.name if page.source else ""
    page.source_size = len(text)
    page.source_stored = len(packed)
    page.source_checksum = hashlib.sha256(text).hexdigest()
    _write(page, "source", _name(capture, SOURCE_EXTENSION), ContentFile(packed))
    _discard(page.source.storage, previous)
    return page


def keep_source_quietly(
    capture: Capture, html: str, keeping: Keeping | None = None
) -> tuple[CapturedPage | None, str]:
    """The same, for a capture in progress: a page or nothing, and a sentence if nothing.

    Never raises. The sentence is empty where nothing was asked for -- a person who keeps
    no sources is not told, every time, that a source was not kept -- and says why where
    something they had asked for could not be done.
    """
    keeping = keeping or keeping_for(capture.owner)
    if not keeping.source:
        return None, ""
    try:
        return keep_source(capture, html, keeping), ""
    except NotKept as refusal:
        return None, str(_("The source of the page was not kept. %(why)s") % {"why": refusal})
    except Exception:
        # Rule four. The capture has been made; what failed is a courtesy beside it.
        logger.exception("Could not keep the source of capture %s", capture.pk)
        # And the capture in hand goes back to saying what the database says, so that the
        # answer does not describe a page the failure left only in memory.
        try:
            capture.refresh_from_db()
        except Exception:  # pragma: no cover - the answer is already a sentence
            logger.warning("Could not re-read capture %s", capture.pk, exc_info=True)
        return None, str(_("The source of the page could not be kept."))


def excerpt_of(page: CapturedPage) -> tuple[str, bool]:
    """The start of the source for the page that shows it, and whether there was more."""
    text = read_source(page)
    return text[:EXCERPT_CHARACTERS], len(text) > EXCERPT_CHARACTERS


# -------------------------------------------------------------------- the rendering


def why_no_rendering_is_taken(
    capture: Capture, keeping: Keeping | None = None, *, room: int | None = None
) -> NotKept | None:
    """Why this capture would not take a rendering from outside, or ``None`` if it would.

    Asked before anything is read, and answered from what is already known: who has said
    yes, whether the capture is still waiting, whether it already has one, and whether the
    account has room for anything at all. ``room`` is for a caller that asks about many
    captures of one account and has counted once.
    """
    keeping = keeping or keeping_for(capture.owner)
    if not keeping.rendering:
        return _switched_off()
    if capture.status != CaptureStatus.PENDING:
        # A capture that has been decided is a record. What it kept is not added to from
        # outside afterwards, by a token that may no longer be in the hands it was made for.
        return NotKept(
            "decided",
            str(_("This capture has been reviewed, and what it kept is no longer added to.")),
        )
    page = capture.kept_page
    if page is not None and page.rendering:
        return _already_kept()
    if (room_left(capture.owner) if room is None else room) <= 0:
        return _no_room()
    return None


def describe(capture: Capture, keeping: Keeping | None = None, *, room: int | None = None) -> dict:
    """What a capture kept and what it would still take, for a program to read.

    The answer a client needs before it goes to the trouble of drawing a page: whether a
    rendering would be taken at all, how large it may be, and in which formats.
    """
    page = capture.kept_page
    refusal = why_no_rendering_is_taken(capture, keeping, room=room)
    return {
        "source": bool(page is not None and page.source),
        "rendering": bool(page is not None and page.rendering),
        "rendering_type": page.rendering_type if page is not None and page.rendering else "",
        "accepts_rendering": refusal is None,
        "rendering_types": list(RenderingKind.values),
        "rendering_max_bytes": site.capture_rendering_max_bytes(),
    }


def attach_rendering(
    capture: Capture,
    stream,
    *,
    content_type: str | None,
    length: int | None,
    keeping: Keeping | None = None,
) -> CapturedPage:
    """Take a rendering from a stream, measuring it before and while it is read.

    ``length`` is what the request said it carries, and it is compared with the cap
    **before a byte is read**; the bytes are then counted as they arrive, so a length that
    lied stops at the cap. They go to a temporary file a chunk at a time and are never held
    whole in memory, whatever they weigh.

    The same bytes sent again are the same answer, because a client that never saw the
    first reply will try again. Different bytes for a capture that already has a rendering
    are refused: what a capture kept is evidence, and evidence is not replaced from outside.
    """
    keeping = keeping or keeping_for(capture.owner)
    held = capture.kept_page
    already = held is not None and bool(held.rendering)

    # The order is the order of cost: whether anything is kept at all, then how large, then
    # what kind, then whether there is room -- and only then the bytes.
    if not keeping.rendering:
        raise _switched_off()
    if not already:
        refusal = why_no_rendering_is_taken(capture, keeping)
        if refusal is not None:
            raise refusal
    cap = site.capture_rendering_max_bytes()
    if length is None:
        raise NotKept("no-length", str(_("The request did not say how large the rendering is.")))
    if length <= 0:
        raise _nothing_there()
    if length > cap:
        raise _too_large(cap)
    kind = rendering_kind(content_type)
    if kind is None:
        raise _unsupported(content_type)
    room = room_left(capture.owner)
    if not already and length > room:
        raise _no_room()

    with tempfile.TemporaryFile() as held_back:
        size, checksum, head = _take(stream, held_back, cap)
        if size == 0:
            raise _nothing_there()
        if not is_what_it_says(kind, head):
            raise NotKept(
                "not-what-it-says",
                str(
                    _("That is not a %(kind)s, whatever it was sent as.")
                    % {"kind": RenderingKind(kind).label}
                ),
            )
        if already:
            if held.rendering_checksum == checksum:
                return held
            raise _already_kept()
        if size > room:
            raise _no_room()

        held_back.seek(0)
        page, claimed = _claim_rendering(
            capture,
            File(held_back),
            kind=kind,
            size=size,
            checksum=checksum,
            rendered_by=RenderedBy.CLIENT,
        )
    # Another request got there first. The same bytes are the same answer, as above.
    if not claimed and page.rendering_checksum != checksum:
        raise _already_kept()
    return page


def _take(stream, into, limit: int) -> tuple[int, str, bytes]:
    """Copy a stream into a file, counting and hashing, and stop one chunk past the limit.

    Returns the size, the SHA-256 and the first bytes. Raises :class:`NotKept` the moment
    the count passes the limit; what was copied so far is abandoned with the file.
    """
    digest = hashlib.sha256()
    size = 0
    head = b""
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise _too_large(limit)
        if len(head) < 16:
            head += chunk[: 16 - len(head)]
        digest.update(chunk)
        into.write(chunk)
    return size, digest.hexdigest(), head


def why_nothing_is_drawn(capture: Capture, keeping: Keeping | None = None) -> NotKept | None:
    """Why this instance would not draw a rendering for this capture, or ``None``."""
    from . import rendering

    keeping = keeping or keeping_for(capture.owner)
    if not keeping.rendering:
        return _switched_off()
    page = capture.kept_page
    if page is None or not page.source:
        return NotKept(
            "no-source",
            str(_("There is no kept source to draw from, and a rendering is drawn from it.")),
        )
    if page.rendering:
        return _already_kept()
    if rendering.renderer() is None:
        return NotKept("no-renderer", str(rendering.why_not()))
    if room_left(capture.owner) <= 0:
        return _no_room()
    return None


def draw_rendering(capture: Capture, keeping: Keeping | None = None) -> CapturedPage:
    """Draw a rendering from the kept source, here, with nothing of it allowed to run.

    What it draws is the source and only the source: no script, no request, no stylesheet
    or image from anywhere else. So it is the same whenever it is drawn, which is why it is
    drawn when somebody asks rather than at every capture -- and why a rendering made
    months later is as good as one made on the day, the checksum of the source being what
    says it is the same page.
    """
    from . import rendering

    refusal = why_nothing_is_drawn(capture, keeping)
    if refusal is not None:
        raise refusal
    page = capture.kept_page

    try:
        html = read_source(page)
    except (Unreadable, OSError) as error:
        raise NotKept(
            "could-not-draw", str(_("The kept source could not be read back."))
        ) from error
    try:
        drawn = rendering.draw(html)
    except rendering.CannotDraw as error:
        raise NotKept("could-not-draw", str(error)) from error

    limit = site.capture_rendering_max_bytes()
    if len(drawn) > limit:
        raise _too_large(limit)
    if len(drawn) > room_left(capture.owner):
        raise _no_room()

    page, claimed = _claim_rendering(
        capture,
        ContentFile(drawn),
        kind=RenderingKind.PDF,
        size=len(drawn),
        checksum=hashlib.sha256(drawn).hexdigest(),
        rendered_by=RenderedBy.INSTANCE,
    )
    if not claimed:
        # Drawn twice at once, and the other one was kept: a press of the button that
        # arrived while the first was still drawing.
        raise _already_kept()
    return page


# ------------------------------------------------------------------------ forgetting


def _discard(storage, name: str) -> None:
    """Remove a file a row has stopped pointing at, once the change is committed.

    After the transaction commits, so a change that is rolled back does not take the file
    with it, and only when no row uses the name -- the rule #217 set for documents.
    """
    if not name:
        return

    def remove() -> None:
        still_used = (
            CapturedPage.objects.filter(source=name).exists()
            or CapturedPage.objects.filter(rendering=name).exists()
        )
        if still_used:
            return
        try:
            storage.delete(name)
        except OSError:  # pragma: no cover - a file already gone is the outcome we wanted
            logger.warning("Could not remove %s from storage", name, exc_info=True)

    transaction.on_commit(remove)


def forget(page: CapturedPage, *, source: bool = True, rendering: bool = True) -> None:
    """Throw away what a capture kept, all of it or one half. The capture stays.

    A row with nothing left to point at is deleted rather than kept empty, and the files go
    with whatever stopped pointing at them: `jobs.signals` for a row that went, `_discard`
    for a column that was cleared.
    """
    keeps_source = bool(page.source) and not source
    keeps_rendering = bool(page.rendering) and not rendering
    if source and page.source:
        # A capture still waiting would have read its review's lesson from this source;
        # what that takes is kept on the capture instead, and none of the page's text (#267).
        from . import remembered

        remembered.before_the_source_goes(page)
    if not keeps_source and not keeps_rendering:
        capture = page.capture
        page.delete()
        # The capture in hand goes on saying what it was told when it was read.
        capture.page = None
        return

    dropped = []
    if source and page.source:
        dropped.append((page.source.storage, page.source.name))
        page.source = ""
        page.source_size = 0
        page.source_stored = 0
        page.source_checksum = ""
    if rendering and page.rendering:
        dropped.append((page.rendering.storage, page.rendering.name))
        page.rendering = ""
        page.rendering_type = ""
        page.rendering_size = 0
        page.rendering_checksum = ""
        page.rendered_by = ""
    if dropped:
        page.save()
    for storage, name in dropped:
        _discard(storage, name)


def _text_of(packed: bytes) -> bytes | None:
    """A gzipped source unpacked within the instance's cap, or ``None`` if it is not one."""
    try:
        return unpack(packed, site.capture_source_max_bytes())
    except Unreadable:
        return None


def restore(
    capture: Capture,
    *,
    source: bytes | None = None,
    rendering: bytes | None = None,
    rendering_type: str = "",
    rendered_by: str = "",
) -> tuple[CapturedPage | None, list[str]]:
    """Put back what an archive says a capture kept. What came back, and what did not.

    An archive is a file somebody hands over, so nothing in it is taken on trust: not the
    sizes, not the checksums, not what it says a file is. Each file is measured against the
    same caps an upload is, the source is unpacked under a limit to see that it is the
    gzipped text it should be, the rendering's first bytes are read against the kind it
    claims, and every figure stored is worked out again from the bytes that arrived. The
    names are made afresh, under the account the archive is being put back into.

    The instance is asked whether it keeps each thing, and the person is not: putting
    their own archive back is them asking. Returns the row, or ``None`` where nothing could
    be kept, and a sentence for each thing the archive had that was left out.
    """
    keeping = keeping_whatever_the_instance_allows()
    left_out: list[str] = []
    page = _page_of(capture)
    room = room_left(capture.owner)

    if source is not None:
        text = _text_of(source) if keeping.source else None
        if not keeping.source:
            left_out.append("the source of a page: this instance does not keep them")
        elif not text:
            left_out.append("the source of a page: it was not text this instance could read")
        else:
            # Packed again rather than copied, so that what is on disk is what this
            # module writes, whatever the archive's copy was packed with.
            packed = gzip.compress(text, mtime=0)
            if len(packed) > room:
                left_out.append("the source of a page: this account has no room left for it")
            else:
                page.source_size = len(text)
                page.source_stored = len(packed)
                page.source_checksum = hashlib.sha256(text).hexdigest()
                _write(page, "source", _name(capture, SOURCE_EXTENSION), ContentFile(packed))
                room -= len(packed)

    if rendering is not None:
        kind = rendering_kind(rendering_type)
        if not keeping.rendering:
            left_out.append("a rendering of a page: this instance does not keep them")
        elif kind is None or not is_what_it_says(kind, rendering[:16]):
            left_out.append("a rendering of a page: it was not a picture or a PDF")
        elif len(rendering) > site.capture_rendering_max_bytes():
            left_out.append("a rendering of a page: it is larger than this instance keeps")
        elif len(rendering) > room:
            left_out.append("a rendering of a page: this account has no room left for it")
        else:
            page.rendering_type = kind
            page.rendering_size = len(rendering)
            page.rendering_checksum = hashlib.sha256(rendering).hexdigest()
            page.rendered_by = rendered_by if rendered_by in RenderedBy.values else ""
            _write(page, "rendering", _name(capture, EXTENSIONS[kind]), ContentFile(rendering))

    return (page if page.pk else None), left_out


def expire_unconfirmed() -> int:
    """Remove the pages of captures that never became a listing. Called by the scheduler.

    A capture discarded, or one still waiting for review, keeps its page for
    `POSTULO_CAPTURE_PAGE_KEEP_DAYS` from the day it was captured; a capture somebody saved
    keeps its page for as long as it exists. The capture itself is never touched here: what
    goes is the copy of the page, which is the part that weighs something.

    Returns how many pages went.
    """
    days = site.capture_page_keep_days()
    if not days:
        return 0
    cutoff = timezone.now() - dt.timedelta(days=days)
    stale = CapturedPage.objects.filter(capture__created_at__lt=cutoff).exclude(
        capture__status=CaptureStatus.ACCEPTED
    )
    gone = 0
    for page in stale:
        page.delete()
        gone += 1
    return gone
