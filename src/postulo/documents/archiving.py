"""Copies of documents to external stores: scheduled on creation, sent by the scheduler.

Nothing here runs inside a request except when a person presses *Send now*. A new
document gets one pending :class:`~postulo.documents.models.DocumentCopy` per store
connection that wants its kind; the scheduler's next pass sends what is pending, retries
what failed with a growing wait, and gives up after a few attempts until someone asks
again. Each copy shows its own state on the document — *archived*, *waiting*, *failed:
…*, *not accepted* — so that a copy that never arrived is a thing a person can see rather
than a thing they assume.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.plugins.models import Connection
from postulo.plugins.secrets import SecretsUnreadable

from .models import CopyStatus, DocumentCopy, DocumentKind, RenderedDocument, UploadedDocument
from .stores import documents_of, metadata_for, wants_kind

logger = logging.getLogger(__name__)

#: After this many failed attempts a copy waits for a person rather than the clock.
MAX_ATTEMPTS = 6
#: The wait doubles from here: five, ten, twenty, forty minutes, then hours.
FIRST_RETRY = dt.timedelta(minutes=5)
#: How many copies one scheduler pass sends at most, so a backlog cannot starve reminders.
BATCH = 50


def _lookup(document) -> dict:
    if isinstance(document, RenderedDocument):
        return {"rendered": document}
    return {"upload": document}


def store_connections(user):
    return Connection.objects.for_user(user).enabled().of_kind("store").exclude(plugin="local")


def schedule_copies(document, *, connections=None) -> list[DocumentCopy]:
    """One pending copy per store connection that wants this document's kind.

    Idempotent: a copy that already exists for a connection is left as it is, whatever
    its state, so calling this twice — on creation and again from a backfill — never
    sends anything twice.
    """
    if connections is None:
        connections = store_connections(document.owner)
    created: list[DocumentCopy] = []
    for connection in connections:
        if not wants_kind(connection.config, document.kind):
            continue
        copy, was_created = DocumentCopy.objects.get_or_create(
            connection=connection,
            **_lookup(document),
            defaults={
                "owner": document.owner,
                "store": connection.plugin,
                "label": connection.label,
            },
        )
        if was_created:
            created.append(copy)
    return created


def backfill(connection: Connection) -> int:
    """Schedule every existing document of the kinds this connection wants. Returns how many."""
    kinds = {kind.value for kind in DocumentKind if wants_kind(connection.config, kind.value)}
    count = 0
    for document in documents_of(connection.owner, kinds):
        count += len(schedule_copies(document, connections=[connection]))
    return count


# -------------------------------------------------------------------- sending


def send_copy(copy: DocumentCopy) -> bool:
    """One attempt at one copy. Records the outcome on the copy; never raises."""
    now = timezone.now()
    connection = copy.connection
    document = copy.document
    copy.attempts += 1
    copy.last_attempt_at = now

    def fail(message: str) -> bool:
        copy.status = CopyStatus.FAILED
        copy.last_error = message[:500]
        copy.next_attempt_at = now + FIRST_RETRY * (2 ** (copy.attempts - 1))
        copy.save()
        return False

    if connection is None or not connection.enabled:
        return fail(str(_("The connection is gone or switched off.")))
    plugin = connection.plugin_instance
    if plugin is None:
        return fail(
            str(_("The %(plugin)s plugin is not installed.") % {"plugin": connection.plugin})
        )
    if document is None or not document.file:
        return fail(str(_("There is no file to send.")))

    try:
        with document.file.open("rb") as handle:
            ref = plugin.put(
                document, handle, metadata_for(document), connection.full_config, document.owner
            )
    except SecretsUnreadable as error:
        return fail(str(error))
    except Exception as error:
        logger.exception("Store %r failed for copy %s", connection.plugin, copy.pk)
        return fail(f"{type(error).__name__}: {error}")

    if ref is None:
        copy.status = CopyStatus.DECLINED
        copy.last_error = ""
        copy.next_attempt_at = None
        copy.save()
        return False

    copy.status = CopyStatus.SENT
    copy.external_id = str(ref.id)[:500]
    copy.external_url = str(ref.url or "")[:500]
    copy.sent_at = now
    copy.last_error = ""
    copy.next_attempt_at = None
    copy.save()
    connection.record_test(True)
    return True


def pending_copies(now=None):
    now = now or timezone.now()
    return (
        DocumentCopy.objects.filter(status__in=(CopyStatus.PENDING, CopyStatus.FAILED))
        .filter(attempts__lt=MAX_ATTEMPTS)
        .filter(next_attempt_at__lte=now)
        .select_related("connection", "rendered", "upload", "owner")
        .order_by("next_attempt_at", "pk")
    )


def send_pending(*, limit: int = BATCH) -> tuple[int, int]:
    """Send what is due. Returns (sent, failed). Called by the scheduler on every pass."""
    sent = failed = 0
    for copy in list(pending_copies()[:limit]):
        if send_copy(copy):
            sent += 1
        else:
            failed += 1
    return sent, failed


def send_now(document) -> tuple[int, int]:
    """A person's *Send now*: schedule anything missing, then try every copy at once.

    A copy that had given up gets its attempts back — the person is asking again, and
    the store may well have come back in the meantime.
    """
    with transaction.atomic():
        schedule_copies(document)
    copies = DocumentCopy.objects.filter(**_lookup(document)).exclude(status=CopyStatus.SENT)
    sent = failed = 0
    for copy in copies.select_related("connection"):
        copy.attempts = 0
        if send_copy(copy):
            sent += 1
        else:
            failed += 1
    return sent, failed


def copies_for(documents) -> dict[tuple[str, int], list[DocumentCopy]]:
    """The copies of many documents at once, keyed by (origin, pk), for a list page."""
    renders = [d.pk for d in documents if isinstance(d, RenderedDocument)]
    uploads = [d.pk for d in documents if isinstance(d, UploadedDocument)]
    found: dict[tuple[str, int], list[DocumentCopy]] = {}
    queryset = DocumentCopy.objects.filter(rendered_id__in=renders) | DocumentCopy.objects.filter(
        upload_id__in=uploads
    )
    for copy in queryset.select_related("connection").order_by("pk"):
        key = ("render", copy.rendered_id) if copy.rendered_id else ("upload", copy.upload_id)
        found.setdefault(key, []).append(copy)
    return found


def attach_copies(documents) -> None:
    """Give each document an ``archive_copies`` list, so a template can show their state."""
    found = copies_for(documents)
    for document in documents:
        origin = "render" if isinstance(document, RenderedDocument) else "upload"
        document.archive_copies = found.get((origin, document.pk), [])
