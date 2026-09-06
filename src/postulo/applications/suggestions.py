"""Things a plugin thinks happened, waiting for a person to say whether they did.

A plugin that reads a mailbox, or a calendar, or anything else outside Postulo, is
guessing. It reads "we regret to inform you" and concludes a rejection; it reads a date
in an invitation and concludes an interview. It is right most of the time, and the times
it is wrong would put a rejection on an application that is still alive.

So nothing a plugin infers is ever written straight into the record. It becomes a
**suggestion**, in a queue a person looks at, exactly as a captured posting does:
accepting one writes it through ``record_event`` or ``change_status``, so the timeline
reads as it always does and says which plugin it came from. Declining one is remembered
— the same message is never suggested twice, whichever way it was answered.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import ApplicationEvent, EventKind, Suggestion, SuggestionStatus
from .services import change_status, record_event


def suggest(
    owner,
    *,
    source: str,
    external_id: str = "",
    application=None,
    kind: str = EventKind.NOTE,
    summary: str,
    body: str = "",
    occurred_at=None,
    suggested_status: str = "",
    proposed_dates=(),
    context: dict | None = None,
) -> tuple[Suggestion, bool]:
    """File one suggestion. Returns it and whether it is new.

    ``external_id`` is what the source calls the thing it read — a message id, an event
    identifier. Given one, this is idempotent for that source and person: a second call
    finds the first suggestion and changes nothing, whether it is still waiting, was
    accepted, or was declined. That is what stops a mailbox suggesting the same message
    on every pass.
    """
    values = {
        "application": application,
        "kind": kind,
        "summary": summary[:250],
        "body": body,
        "occurred_at": occurred_at or timezone.now(),
        "suggested_status": suggested_status,
        "proposed_dates": [str(date) for date in proposed_dates],
        "context": dict(context or {}),
    }
    if external_id:
        existing = Suggestion.objects.filter(
            owner=owner, source=source, external_id=external_id[:250]
        ).first()
        if existing is not None:
            return existing, False
    suggestion = Suggestion.objects.create(
        owner=owner, source=source, external_id=external_id[:250], **values
    )
    return suggestion, True


@transaction.atomic
def accept(suggestion: Suggestion, *, application=None, actor: str = "") -> Suggestion:
    """Write the suggestion into the record, through the services that keep the log true.

    ``application`` is needed only when the suggestion never found one. A suggestion that
    proposes a status moves the application through ``change_status``; every other kind
    is an entry on the timeline. Either way the entry says which plugin it came from, so
    a person can see what an automatism did and undo it by hand.
    """
    if suggestion.status != SuggestionStatus.PENDING:
        return suggestion
    target = application or suggestion.application
    if target is None:
        raise ValueError("A suggestion needs an application before it can be accepted.")
    if target.owner_id != suggestion.owner_id:
        raise ValueError("That application belongs to someone else.")

    by = actor or suggestion.source
    event: ApplicationEvent | None = None
    if suggestion.suggested_status:
        event = change_status(
            target,
            suggestion.suggested_status,
            note=suggestion.summary,
            occurred_at=suggestion.occurred_at,
            actor=by,
        )
    if event is None:
        event = record_event(
            target,
            kind=suggestion.kind,
            summary=suggestion.summary,
            body=suggestion.body,
            occurred_at=suggestion.occurred_at,
            actor=by,
        )

    suggestion.application = target
    suggestion.event = event
    suggestion.status = SuggestionStatus.ACCEPTED
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=["application", "event", "status", "reviewed_at", "updated_at"])
    return suggestion


def decline(suggestion: Suggestion) -> Suggestion:
    """Say no. Nothing is written to the record, and it is never suggested again."""
    if suggestion.status != SuggestionStatus.PENDING:
        return suggestion
    suggestion.status = SuggestionStatus.DECLINED
    suggestion.reviewed_at = timezone.now()
    suggestion.save(update_fields=["status", "reviewed_at", "updated_at"])
    return suggestion


def pending_count(user) -> int:
    return Suggestion.objects.for_user(user).pending().count()
