"""Slow work, sent off to be done, and the one rule that makes it optional (#247).

Five things Postulo does are slow for reasons it does not control: fetching a page somebody
else serves, finding a company's logo among six candidate images, rendering a PDF, building
an archive of a whole account, and telling a person's notifiers that something arrived. Each
was a POST that held a gunicorn worker for the whole time, so a capture from a slow job board
spent fifteen seconds of somebody's afternoon and a share of a small machine's capacity that
nothing else could use. #220 took them out of the request's transaction, which was the half
that was a bug; this is the other half.

**The queue is optional, and that is the shape of this module.** Postulo is self-hosted and
plenty of instances are one container with no worker in it. `send` therefore has two ways to
finish and one signature: with a worker configured it enqueues and answers *waiting*; without
one it does the work where it stands and answers *done*. The page it answers with is the same
page, which is what stops the second arrangement from rotting -- there is no path here that
only runs on somebody else's machine.

**What a handler is.** A function taking the `Errand` and returning what to say and where to
go. It raises to fail, and the message it raises with is what the person reads, because these
failures are explanations -- *that site refused us*, *no PDF backend is installed* -- and not
one of them is a bug the person can do anything about by seeing a traceback.

**What this deliberately does not do.** It does not retry. Rendering a CV twice files two
snapshots, and an automatic second attempt at work whose whole purpose is to produce a record
is a way to produce two of them; the person presses the button again, having seen why it
failed. It does not hold a transaction across the work, for the reason #220 exists. And it
never counts an allowance: a rate limit is spent by asking, in the view, before any of this,
or an account could queue a thousand fetches on one permit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Errand, ErrandState

logger = logging.getLogger(__name__)


class Refused(Exception):
    """What a handler raises to fail in words the person can read."""


@dataclass(frozen=True)
class Handler:
    """One kind of slow work: what to call it, and what to do."""

    kind: str
    #: What the work is called: the page's heading, and the line under it while it runs.
    #: A noun phrase rather than a sentence, and without the ellipsis -- the page is still
    #: headed *Drawing the PDF* once the PDF is drawn, and only the line below it changes.
    working: str
    run: Callable[[Errand], dict]


HANDLERS: dict[str, Handler] = {}


def handler(kind: str, *, working: str):
    """Register a kind of slow work. Used as a decorator on the function that does it."""

    def keep(run: Callable[[Errand], dict]) -> Callable[[Errand], dict]:
        HANDLERS[kind] = Handler(kind=kind, working=working, run=run)
        return run

    return keep


def worker_expected() -> bool:
    """Whether this instance has been told a worker is running.

    A setting rather than a probe. "Is a worker running" cannot be answered from inside a
    web request without either a round trip or a guess, and a guess that says yes on an
    instance with no worker turns every one of these buttons into a button that does
    nothing -- which is the failure this must not have.
    """
    return bool(getattr(settings, "POSTULO_BACKGROUND_WORK", False))


def send(kind: str, owner, *, subject=None, **payload) -> Errand:
    """Ask for a piece of slow work, and get back something a page can watch.

    Enqueued where a worker is expected, done here and now where one is not. Either way the
    answer is a saved `Errand`, and the caller redirects to the page that watches it.
    """
    if kind not in HANDLERS:
        raise KeyError(f"No handler is registered for {kind!r}.")
    errand = Errand.objects.create(kind=kind, owner=owner, payload=payload, subject=subject)
    if not worker_expected():
        perform(errand.pk)
        errand.refresh_from_db()
        return errand
    try:
        from .tasks import perform_errand

        perform_errand.enqueue(errand.pk)
    except Exception:
        # A queue that refuses the work is not a reason to lose it. The alternative is an
        # errand left *waiting* for ever on an instance whose operator set the flag and
        # never started the container, which is the silent button again.
        logger.exception("Could not enqueue errand %s; doing it here instead.", errand.pk)
        perform(errand.pk)
        errand.refresh_from_db()
    return errand


def perform(errand_id: int) -> None:
    """Do one errand, and write down how it went. Never raises.

    By id rather than by instance, because the worker is another process and the row is the
    only thing the two of them share. A row that has gone -- the account was deleted while
    the work waited -- is not an error: there is nobody left to tell.
    """
    errand = Errand.objects.filter(pk=errand_id).first()
    if errand is None or errand.is_finished:
        return
    work = HANDLERS.get(errand.kind)
    if work is None:
        # A kind that no longer exists: an errand queued by a version that had it, run by a
        # version that does not. Said plainly rather than retried for ever.
        _finish(errand, ErrandState.FAILED, error=str(_("Postulo no longer does that.")))
        return

    Errand.objects.filter(pk=errand.pk).update(state=ErrandState.WORKING, started_at=timezone.now())
    try:
        outcome = work.run(errand) or {}
    except Refused as refusal:
        _finish(errand, ErrandState.FAILED, error=str(refusal))
    except Exception:
        # The traceback goes to the log, where an operator can read it; the page gets a
        # sentence, because a person watching a spinner cannot act on a traceback.
        logger.exception("Errand %s (%s) failed.", errand.pk, errand.kind)
        _finish(
            errand,
            ErrandState.FAILED,
            error=str(_("Something went wrong. The server log has the details.")),
        )
    else:
        _finish(errand, ErrandState.DONE, outcome=outcome)


def _finish(errand: Errand, state: str, *, outcome: dict | None = None, error: str = "") -> None:
    """One statement, so the write lock is held for the length of one write (#220)."""
    Errand.objects.filter(pk=errand.pk).update(
        state=state,
        outcome=outcome or {},
        error=error,
        finished_at=timezone.now(),
    )


def working_label(kind: str) -> str:
    work = HANDLERS.get(kind)
    return str(work.working) if work else str(_("Working"))


def forget_old(*, days: int = 7) -> int:
    """Drop errands nobody is watching any more. Called by the scheduler.

    A finished errand is a sentence and a link, both of which are already somewhere better
    -- a rendered document is in *Sent documents*, a capture is in the review queue -- so a
    week is generous rather than tight. An unfinished one older than that belongs to a
    worker that is not coming back.
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _rows = Errand.objects.filter(created_at__lt=cutoff).delete()
    return deleted
