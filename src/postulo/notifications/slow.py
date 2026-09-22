"""Telling somebody's notifiers, after the request that caused it has gone (#247).

`notify` walks every notifier connection an account has and gives each one a message, which
means a network timeout per connection. The capture API called it inline, so a browser
extension sending forty postings waited on all of them, forty times over, before the
fortieth was acknowledged. Delivery survives a failing notifier already; it was the *place*
that was wrong.

**The words are still written at delivery, in the owner's language.** That is why the errand
carries ingredients rather than sentences. `notify` switches language and then calls what it
was given, so a message built inside it is built in the language the person reads -- which is
the whole of #223, and pre-wording it in the request would undo it. The request's language is
particularly wrong here: a capture arrives through a token, so the `Accept-Language` on it is
the browser that found the posting rather than a choice anybody made about Postulo.

The builders live here, keyed by event. A notification whose event this version does not know
is not delivered rather than delivered blank -- an errand queued by a newer version and run
by an older one is the only way that happens, and silence beats an empty message.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from django.urls import reverse
from django.utils.translation import gettext as _

from postulo.core.errands import handler

from .base import Notification, absolute_url

#: event -> a function that builds the message, called inside the language override.
BUILDERS: dict[str, Callable[[dict], Notification]] = {}


def builder(event: str):
    def keep(build: Callable[[dict], Notification]) -> Callable[[dict], Notification]:
        BUILDERS[event] = build
        return build

    return keep


@handler("notify", working=_("Telling your notifiers"))
def deliver(errand) -> dict:
    """Send one message through every notifier the owner has that wants it."""
    from .service import notify

    build = BUILDERS.get(errand.payload.get("event", ""))
    if build is None:
        return {"message": "", "delivered": 0}
    taken = notify(errand.owner, lambda: build(errand.payload))
    return {"message": "", "delivered": taken}


def link(payload: dict, path: str) -> str:
    """A link a message can carry, from where the request said this instance is reached.

    The request is gone by the time this runs, so the address it knew travels in the errand.
    `absolute_url` is the fallback it has always been -- `POSTULO_PUBLIC_URL`, else the bare
    path -- and it is the fallback rather than the answer because a message that says
    */jobs/captures/3/review/* is a message nobody can follow from their mail (#247).
    """
    base = (payload.get("base") or "").rstrip("/")
    return f"{base}{path}" if base else absolute_url(path)


def when(payload: dict):
    """When the thing happened, from the errand, since the errand may run much later.

    A queued send is minutes old by the time a notifier sees it, and older still if it was
    retried; a notifier that files the message wants the arrival, not the delivery (#229).
    """
    stamped = payload.get("at") or ""
    try:
        return dt.datetime.fromisoformat(stamped) if stamped else None
    except ValueError:
        return None


@builder("capture_received")
def a_capture_arrived(payload: dict) -> Notification:
    """One posting came in from outside: the one event a person cannot see coming."""
    return Notification(
        event="capture_received",
        title=_("Captured: %(title)s") % {"title": payload.get("title", "")},
        body=payload.get("where", ""),
        url=link(payload, reverse("jobs:capture_review", args=[payload["capture_id"]])),
        # The capture, not this send: the errand may be retried, and one posting arriving
        # is one message (#229).
        key=f"capture:{payload['capture_id']}",
        occurred_at=when(payload),
        data={"capture_id": payload["capture_id"], "where": payload.get("where", "")},
    )


@builder("capture_batch")
def a_batch_arrived(payload: dict) -> Notification:
    """Forty came in because somebody pressed one button, and the first says so (#177)."""
    return Notification(
        event="capture_received",
        title=_("Captured %(count)s postings from %(host)s")
        % {"count": payload.get("count", 0), "host": payload.get("host", "")},
        body=_("The first: %(title)s") % {"title": payload.get("title", "")},
        url=link(payload, reverse("jobs:capture_list")),
        key=f"capture_batch:{payload.get('capture_id', '')}",
        occurred_at=when(payload),
        data={
            "count": payload.get("count", 0),
            "host": payload.get("host", ""),
            "capture_id": payload.get("capture_id", ""),
        },
    )


# ---------------------------------------------- what the person did, for a machine (#240)


def anybody_wants(owner, event: str) -> bool:
    """Whether any notifier this person has switched on asks for ``event``.

    Asked before an errand is queued, because the events about what the person did fire on
    every status change, every interview and every offer, and a person with no webhook -- the
    ordinary case -- must not collect an errand row per click for a message nobody would
    receive. One query; the errand and the delivery are the expensive part.
    """
    from postulo.plugins.models import Connection

    from .base import wants

    for connection in Connection.objects.for_user(owner).enabled().of_kind("notifier"):
        plugin = connection.plugin_instance
        if plugin is not None and wants(connection.config, event, plugin):
            return True
    return False


def tell(event: str, owner, *, subject=None, **payload) -> None:
    """Queue the announcement of ``event``, if anybody would hear it."""
    from django.utils import timezone

    from postulo.core import errands

    if not anybody_wants(owner, event):
        return
    errands.send(
        "notify", owner, subject=subject, event=event, at=timezone.now().isoformat(), **payload
    )


def _role_at(payload: dict) -> str:
    return _("%(role)s at %(company)s") % {
        "role": payload.get("role", ""),
        "company": payload.get("company", ""),
    }


@builder("status_changed")
def a_status_changed(payload: dict) -> Notification:
    return Notification(
        event="status_changed",
        title=_("%(what)s: %(status)s")
        % {"what": _role_at(payload), "status": payload.get("status", "")},
        body=payload.get("note", ""),
        url=link(payload, reverse("applications:detail", args=[payload["application_id"]])),
        # The timeline entry, not the application: an application moves many times and each
        # move is one message; a retry of the same move is not.
        key=f"status:{payload['application_id']}:{payload.get('event_id', '')}",
        occurred_at=when(payload),
        data={
            "application_id": payload["application_id"],
            "event_id": payload.get("event_id"),
            "from_status": payload.get("from_status", ""),
            "to_status": payload.get("to_status", ""),
            "actor": payload.get("actor", ""),
        },
    )


@builder("interview_scheduled")
def an_interview_was_scheduled(payload: dict) -> Notification:
    moved = bool(payload.get("moved"))
    return Notification(
        event="interview_scheduled",
        title=(_("Interview moved: %(what)s") if moved else _("Interview scheduled: %(what)s"))
        % {"what": _role_at(payload)},
        body=payload.get("starts_at", ""),
        url=link(payload, reverse("applications:detail", args=[payload["application_id"]])),
        key=f"interview:{payload['interview_id']}:{payload.get('starts_at', '')}",
        occurred_at=when(payload),
        data={
            "interview_id": payload["interview_id"],
            "application_id": payload["application_id"],
            "kind": payload.get("interview_kind", ""),
            "starts_at": payload.get("starts_at", ""),
            "ends_at": payload.get("ends_at", ""),
            "moved": moved,
        },
    )


@builder("offer_recorded")
def an_offer_was_recorded(payload: dict) -> Notification:
    revised = bool(payload.get("revised"))
    return Notification(
        event="offer_recorded",
        title=(_("Offer revised: %(what)s") if revised else _("Offer recorded: %(what)s"))
        % {"what": _role_at(payload)},
        body=payload.get("terms", ""),
        url=link(payload, reverse("applications:detail", args=[payload["application_id"]])),
        key=f"offer:{payload['offer_id']}:{payload.get('event_id', '')}",
        occurred_at=when(payload),
        data={
            "offer_id": payload["offer_id"],
            "application_id": payload["application_id"],
            "terms": payload.get("terms", ""),
            "revised": revised,
        },
    )
