"""How an application ended: where it had got to, and why (#239).

The funnel says where applications stop. It could never say why, and it could not say
where *this one* stopped without somebody reading its timeline. Both are read here, from
the timeline and from nothing else:

**Why** is a column of the entry that ended it -- `ApplicationEvent.end_reason` -- and the
note that goes with it is that entry's own text. A reason learnt afterwards, which is when
most of them are learnt, is a second entry rather than an edit of the first: the log is
appended to and never rewritten, and the latest reason given since the application ended is
the one that stands.

**Where** is never typed. It is the status the application was moved *from* when it ended,
which the entry already carries. Asking somebody to choose it as well would be asking for a
second account of something the record knows, and the two would come to disagree.

Nothing here is stored. An application that reopens has no ending, and one that ends again
has the ending its timeline now describes.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from .models import END_STATUSES, SENT_STATUSES, ApplicationEvent, EndReason, Status


class Entry(NamedTuple):
    """One status entry, reduced to what an ending is read from."""

    at: dt.datetime
    pk: int
    from_status: str
    to_status: str
    reason: str = ""
    note: str = ""


@dataclass(frozen=True)
class Ending:
    """Where an application ended and why, as far as its timeline says."""

    #: What it ended as: rejected, withdrawn or ghosted.
    status: str
    #: The stage it had reached when it ended. Empty where the timeline does not say --
    #: a row made before there was a timeline, or restored without one.
    last_stage: str = ""
    #: One of `EndReason`, or empty where nobody gave one.
    reason: str = ""
    #: What was written beside the reason, or beside the ending where there was none.
    note: str = ""
    ended_at: dt.datetime | None = None

    @property
    def status_label(self) -> str:
        return _label(Status, self.status)

    @property
    def last_stage_label(self) -> str:
        return _label(Status, self.last_stage)

    @property
    def reason_label(self) -> str:
        return _label(EndReason, self.reason)


def _label(choices, value: str) -> str:
    """The word for a stored value, or the value itself where this version has no word.

    An archive written by a later Postulo may carry a reason this one has never heard of,
    and a page that raised on it would be a page nobody could open.
    """
    if not value:
        return ""
    return str(dict(choices.choices).get(value, value))


def stage_before(entry: Entry) -> str:
    """The stage an application stood at when ``entry`` ended it.

    The entry says what it was moved from, with two corrections. An application recorded
    straight to *Rejected* or *Ghosted* -- a reply somebody already had -- was moved from
    *Draft* and had nevertheless been sent, so it ended at *Applied*; only one withdrawn
    from *Draft* ended before it went anywhere. And an entry that moved it from one ending
    to another says nothing about a stage at all.
    """
    stage = entry.from_status
    if stage in END_STATUSES:
        return ""
    if stage == Status.DRAFT and entry.to_status in SENT_STATUSES:
        return Status.APPLIED
    return stage


def read(status: str, entries: Iterable[Entry]) -> Ending | None:
    """The ending ``entries`` describe for an application standing at ``status``.

    ``None`` while it has not ended. Otherwise the run of entries since it last left the
    stages that are still live: the first of them says where it had got to and when it
    ended, and the latest of them to carry a reason says why.

    **In the order things happened, which is not always the order they were written.** A
    rejection accepted from a mailbox is dated when the message arrived, which can be
    before a move somebody made by hand in the meantime. Where that leaves something other
    than an ending as the latest entry, the latest entry that reached this status is taken
    as the one that ended it -- it is the only one that can have.
    """
    if status not in END_STATUSES:
        return None
    ordered = sorted(entries, key=lambda entry: (entry.at, entry.pk))

    run: list[Entry] = []
    for entry in reversed(ordered):
        if entry.to_status not in END_STATUSES:
            break
        run.append(entry)
    run.reverse()
    if not run:
        run = [entry for entry in ordered if entry.to_status == status][-1:]
    if not run:
        return Ending(status=status)

    told = next((entry for entry in reversed(run) if entry.reason), None)
    return Ending(
        status=status,
        last_stage=stage_before(run[0]),
        reason=told.reason if told else "",
        note=(told or run[-1]).note,
        ended_at=run[0].at,
    )


def entries(events) -> list[Entry]:
    """The status entries among ``events``, reduced to what an ending is read from."""
    return [
        Entry(
            at=event.occurred_at,
            pk=event.pk,
            from_status=event.from_status,
            to_status=event.to_status,
            reason=event.end_reason,
            note=event.body,
        )
        for event in events
        if event.to_status
    ]


def of(application, *, afresh: bool = False) -> Ending | None:
    """How one application ended, from whatever of its timeline is already in hand.

    A list that loaded the status entries beside its rows (`with_status_log`), or the
    whole timeline (`prefetch_related("events")`), is read from what it loaded; anything
    else asks once. Either way an application that has not ended costs nothing.

    ``afresh`` asks the database whatever is in hand, for a caller about to *write* on the
    strength of the answer: what was loaded when the page was drawn is the timeline as it
    was then.
    """
    if application.status not in END_STATUSES:
        return None
    held = None
    if not afresh:
        held = getattr(application, "status_log", None)
        if held is None:
            held = getattr(application, "_prefetched_objects_cache", {}).get("events")
    if held is None:
        held = application.events.exclude(to_status="")
    return read(application.status, entries(held))


def for_applications(applications) -> dict[int, Ending]:
    """The ending of every application in ``applications`` that has one, in one query."""
    ended = {
        application.pk: application.status
        for application in applications
        if application.status in END_STATUSES
    }
    if not ended:
        return {}
    logs: dict[int, list[Entry]] = defaultdict(list)
    rows = (
        ApplicationEvent.objects.filter(application_id__in=ended)
        .exclude(to_status="")
        .values_list(
            "application_id", "occurred_at", "pk", "from_status", "to_status", "end_reason", "body"
        )
    )
    for application_id, *rest in rows:
        logs[application_id].append(Entry(*rest))
    return {pk: read(status, logs[pk]) for pk, status in ended.items()}
