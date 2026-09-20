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


@builder("capture_received")
def a_capture_arrived(payload: dict) -> Notification:
    """One posting came in from outside: the one event a person cannot see coming."""
    return Notification(
        event="capture_received",
        title=_("Captured: %(title)s") % {"title": payload.get("title", "")},
        body=payload.get("where", ""),
        url=link(payload, reverse("jobs:capture_review", args=[payload["capture_id"]])),
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
    )
