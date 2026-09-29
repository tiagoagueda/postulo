"""A listing's history, and the one way anything is written into it (#270).

`applications.services.record_event` is the way into an application's timeline; this is the
way into a listing's history, for the same reasons. Everything that writes one -- the form
on the listing's page, the review screen binding a second capture, the API, a plugin --
comes through `record_listing_event`, so an entry reads the same whoever wrote it and says
who did when it was not the person.

**The owner is never chosen by the caller.** It is the listing's, and the listing is the
record the caller was handed: a `JobPosting`, or an application whose posting it is. What an
entry names -- a contact, a capture, a file -- has to be that same person's, or nothing is
written. A plugin can bind only what it could already see.

**The text is a stranger's** (#218). An email or a message was written by somebody else, so
it is held here to what #218 settled for captured text: bounded, stripped of the one
character a database refuses, and stored as words. Nothing reads it as markup, a link, a
formula or a command -- the page escapes it, JSON quotes it, and it reaches no spreadsheet
and no calendar at all.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    Capture,
    CaptureStatus,
    JobPosting,
    ListingEvent,
    ListingEventKind,
)

#: What an entry may point at, as Django labels: things already stored where they belong. A
#: capture of the advert, and a file in the person's documents. Nothing here stores a file.
ARTEFACTS = ("jobs.capture", "documents.uploadeddocument")

#: The longest summary an entry keeps, which is the column's length.
SUMMARY_MAX_CHARS = 250

#: The longest body an entry keeps. The length a captured description is held to
#: (`plugins.base.MAX_DESCRIPTION_CHARS`): the same kind of text, from the same kind of
#: stranger, and a longer email is one somebody pasted a whole thread into.
BODY_MAX_CHARS = 40_000

#: Said where a body was cut, so that a shortened text never reads as the whole of it. The
#: marker a captured description carries, for the same reason.
TRUNCATED = "\n\n[…truncated]"

#: The longest identifier a source may give, and the longest name an actor may have.
EXTERNAL_ID_MAX_CHARS = 250
ACTOR_MAX_CHARS = 120


class NotBound(ValueError):
    """What was asked cannot be written into this listing's history. The message says why.

    Raised for a mistake in the caller -- a kind that does not exist, a file somebody else
    owns -- rather than for anything a person typed: the form and the API check those first
    and answer in words. A `ValueError`, so a plugin catching the broad one catches this.
    """


def listing_of(record) -> JobPosting:
    """The listing ``record`` belongs to: itself, or the posting an application is for."""
    from postulo.applications.models import Application

    if isinstance(record, JobPosting):
        return record
    if isinstance(record, Application):
        return record.posting
    raise TypeError(
        f"A listing's history is written for a listing or an application, not {type(record)!r}."
    )


def one_line(text) -> str:
    """One line of somebody else's text: no NUL, and its whitespace folded to single spaces.

    A summary is the line a history shows, and an email's subject can arrive folded across
    several. NUL is the one character PostgreSQL refuses in a text column, so a message
    carrying one would otherwise fail the whole request on one database and not the other.
    """
    return " ".join(str(text or "").replace("\x00", "").split())


def bounded(text) -> str:
    """A body, as long as an entry keeps and no longer, with nothing a database refuses."""
    text = str(text or "").replace("\x00", "")
    if len(text) > BODY_MAX_CHARS:
        return text[:BODY_MAX_CHARS].rstrip() + TRUNCATED
    return text


def _check_artefact(posting: JobPosting, kind: str, artefact) -> None:
    """Refuse a link to anything but one of the owner's captures or files, and the wrong kind.

    A capture is bound as a capture and nothing else, and a *document* entry is one that
    points at a document: an entry that says it is a file and has none would be a history
    naming something that is not there.
    """
    if kind == ListingEventKind.CAPTURE and not isinstance(artefact, Capture):
        raise NotBound("A capture entry points at the capture it records.")
    if kind == ListingEventKind.DOCUMENT and (
        artefact is None or artefact._meta.label_lower != "documents.uploadeddocument"
    ):
        raise NotBound("A document entry points at a file in the person's documents.")
    if artefact is None:
        return
    if artefact._meta.label_lower not in ARTEFACTS or artefact.pk is None:
        raise NotBound(f"A listing's history cannot point at {type(artefact).__name__}.")
    if isinstance(artefact, Capture) and kind != ListingEventKind.CAPTURE:
        raise NotBound("A capture is bound to a listing as a capture entry.")
    if artefact.owner_id != posting.owner_id:
        raise NotBound("That belongs to somebody other than the listing's owner.")


def record_listing_event(
    record,
    *,
    kind: str = ListingEventKind.NOTE,
    summary: str = "",
    body: str = "",
    occurred_at=None,
    actor: str = "",
    contact=None,
    artefact=None,
    external_id: str = "",
) -> tuple[ListingEvent, bool]:
    """Append one entry to a listing's history. Returns it, and whether it is new.

    ``record`` is the listing, or an application whose listing it is: the entry goes on the
    listing either way, and the application's page reads it from there. The owner is the
    listing's, and never the caller's to say.

    ``kind`` is one of `ListingEventKind`'s values -- ``note``, ``email_received``, ``call``,
    ``message``, ``document``, ``other``; ``capture`` is what binding a capture writes.
    ``contact`` is who it came from and ``artefact`` what it points at (one of the owner's
    captures, or one of their uploaded files); both are refused if they are anybody else's.
    ``actor`` names who wrote it when it was not the person -- an API token, a plugin -- so
    the history shows what an automatism did.

    Given an ``external_id`` -- what the source calls the thing, a message id say -- this is
    idempotent for that listing: a second call finds the first entry and changes nothing,
    and answers ``False``. That is what lets a mailbox be read every five minutes.

    The words are foreign text and are held to what #218 settled: ``summary`` is folded to
    one line of at most 250 characters, ``body`` is cut at `BODY_MAX_CHARS` with a marker
    saying so, and neither may carry a NUL.
    """
    posting = listing_of(record)
    kind = str(kind)
    if kind not in ListingEventKind.values:
        raise NotBound(
            f"{kind!r} is not a kind of entry; one of {sorted(ListingEventKind.values)}."
        )
    _check_artefact(posting, kind, artefact)
    if contact is not None and contact.owner_id != posting.owner_id:
        raise NotBound("That contact belongs to somebody other than the listing's owner.")

    external_id = one_line(external_id)[:EXTERNAL_ID_MAX_CHARS]
    if external_id:
        found = ListingEvent.objects.filter(posting=posting, external_id=external_id).first()
        if found is not None:
            return found, False

    values = {
        "kind": kind,
        "summary": one_line(summary)[:SUMMARY_MAX_CHARS],
        "body": bounded(body),
        "occurred_at": occurred_at or timezone.now(),
        "actor": one_line(actor)[:ACTOR_MAX_CHARS],
        "contact": contact,
        "external_id": external_id,
    }
    if artefact is not None:
        values["artefact_type"] = ContentType.objects.get_for_model(artefact)
        values["artefact_id"] = artefact.pk
    try:
        # A savepoint of its own, so that losing a race over one message id leaves the
        # caller's transaction usable -- an API request is one, and the answer is the entry
        # the other request made.
        with transaction.atomic():
            return ListingEvent.objects.create(posting=posting, **values), True
    except IntegrityError:
        found = (
            ListingEvent.objects.filter(posting=posting, external_id=external_id).first()
            if external_id
            else None
        )
        if found is None:
            raise
        return found, False


class AlreadyDecided(NotBound):
    """The capture was saved or discarded already, so it is not the review screen's to bind."""


@transaction.atomic
def bind_capture(capture: Capture, posting: JobPosting, *, actor: str = "") -> ListingEvent:
    """A second capture of an advert already in the listings, as an entry in its history.

    `jobs.known` says when an advert has been seen before and tells rather than refuses
    (#178); this is the answer that does not make a second listing for one job. The same
    advert read off another board is evidence about the job rather than noise, so the
    capture is **kept and pointed at**, not copied: what it read, the address it was read at,
    and whatever it kept of the page (#256) stay on the capture, which becomes the listing's
    as the first one did. The entry keeps the words -- the board, the title, the address --
    so it still says what it was if the capture ever goes.

    Read under a lock, so the review screen open in two tabs binds once; a capture that is
    no longer waiting raises `AlreadyDecided`.
    """
    if capture.owner_id != posting.owner_id:
        raise NotBound("That listing belongs to somebody other than the capture's owner.")
    locked = Capture.objects.select_for_update().filter(pk=capture.pk).first()
    if locked is None or locked.status != CaptureStatus.PENDING:
        raise AlreadyDecided("That capture has already been saved or discarded.")

    where = urlsplit(locked.url).hostname or locked.source_name
    title = str((locked.data or {}).get("title") or "")
    event, _created = record_listing_event(
        posting,
        kind=ListingEventKind.CAPTURE,
        summary=(
            _("Captured again from %(where)s") % {"where": where} if where else _("Captured again")
        ),
        body="\n".join(part for part in (title, locked.url) if part),
        occurred_at=locked.created_at,
        actor=actor,
        artefact=locked,
    )
    locked.status = CaptureStatus.ACCEPTED
    locked.posting = posting
    locked.save(update_fields=["status", "posting", "updated_at"])
    # The caller's object is the one it goes on to use -- the next capture to review is
    # worked out from it -- so it says what the row now says.
    capture.status, capture.posting = locked.status, posting
    return event


def history_of(posting: JobPosting) -> list[ListingEvent]:
    """A listing's entries, newest first, with what each names loaded beside it.

    What each points at is fetched a kind at a time -- captures with what they kept of their
    page, files as they are -- rather than one query per entry, and the listing each belongs
    to is the one in hand, so checking an artefact's owner asks nothing more.
    """
    from postulo.documents.models import UploadedDocument

    entries = list(
        ListingEvent.objects.filter(posting=posting)
        .select_related("contact", "contact__company")
        .prefetch_related(
            GenericPrefetch(
                "artefact",
                [Capture.objects.select_related("page"), UploadedDocument.objects.all()],
            )
        )
    )
    for entry in entries:
        entry.posting = posting
    return entries
