"""Who changed what on a company, a contact or a listing (#896).

Written for every record, shared or not, so a record shared next month already says who
made it and who changed it since. Names of fields, never their values.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType

from .models import RecordChange, RecordChangeAction

CREATED = RecordChangeAction.CREATED
EDITED = RecordChangeAction.EDITED
SHARED = RecordChangeAction.SHARED
UNSHARED = RecordChangeAction.UNSHARED
OWNER_CHANGED = RecordChangeAction.OWNER_CHANGED
COPIED = RecordChangeAction.COPIED
MERGED = RecordChangeAction.MERGED

#: The parts of a page that are not fields of the model, by the name a person reads.
BLOCKS = {
    "phone_numbers": "telephone numbers",
    "postal_addresses": "postal addresses",
    "web_links": "web links",
    "messaging_handles": "messaging handles",
    "identifiers": "identifiers",
}


def record(target, actor, action: str, *, fields=(), detail=None, via: str = "") -> RecordChange:
    """Write one line of ``target``'s trail."""
    who = actor if getattr(actor, "is_authenticated", False) else None
    return RecordChange.objects.create(
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.pk,
        actor=who,
        via=(via or "")[:120],
        action=action,
        fields=sorted({str(name) for name in fields}),
        detail=detail or {},
    )


def edited(target, actor, form=None, *, formsets=(), via: str = "") -> RecordChange | None:
    """A line for a save that changed something: the form's changed fields, and each block of
    rows that changed. Nothing is written for a save that changed nothing."""
    names = set(form.changed_data) if form is not None else set()
    names.discard("notes")  # a person's own note is not a change to the record
    for formset in formsets:
        if formset is not None and formset.has_changed():
            names.add(BLOCKS.get(getattr(formset, "prefix", ""), getattr(formset, "prefix", "")))
    if not names:
        return None
    return record(target, actor, EDITED, fields=names, via=via)


def of(target, limit: int = 50):
    """``target``'s trail, newest first, with who did each thing."""
    return (
        RecordChange.objects.filter(
            content_type=ContentType.objects.get_for_model(target), object_id=target.pk
        )
        .select_related("actor")
        .order_by("-at", "-pk")[:limit]
    )
