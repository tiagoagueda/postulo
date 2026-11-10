"""Watching a piece of slow work, and the page it leaves behind (#247).

Two addresses. One is a page a person is sent to after pressing a button that no longer
answers with the finished thing; the other is the fragment that page polls until the work
resolves. Both are ownership-checked, and the poll particularly: it is a new address that
names a piece of work, and an address that names a row is an address somebody will try with
another row's number.

**It degrades.** The polling is htmx on the fragment; with no script the page is a plain one
with a *Check again* link, which is the same request made by hand. An instance with no worker
never sees either: the work is already done by the time the page is drawn, and the page says
so on its first paint.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import errands as errand_layer
from . import scheduler
from .models import Errand, ErrandState

#: How often the fragment asks again. Long enough not to be a load, short enough that a
#: capture that took four seconds is not reported after ten.
POLL_SECONDS = 2

#: How often it asks once the errand is older than each age (#1154). Most errands finish in
#: their first seconds, which stay as quick as they were; one that has run for minutes is
#: not reported noticeably later for being asked about every fifteen seconds, and a tab left
#: open on one that will never finish is no longer 1,800 requests an hour.
POLL_BACK_OFF = (
    (timedelta(minutes=10), 30),
    (timedelta(minutes=2), 15),
    (timedelta(seconds=20), 5),
)

#: How long the worker may go unseen before the page stops saying the work carries on. The
#: worker's own healthcheck calls it dead after the same five minutes (`docker/compose.yml`).
WORKER_SILENT_AFTER = timedelta(minutes=5)

#: How often the fragment asks while the worker is silent: nothing will change until it is
#: back, and a tab left open is otherwise 1,800 requests an hour to say so (#1122).
SILENT_POLL_SECONDS = 30


def worker_silent() -> bool:
    """Whether the worker has not been round lately, or never has here (#1122).

    Read from the heartbeat the worker writes after each pass, which is the one thing the
    web container can see of it. A heartbeat from the future is a clock that disagrees, not
    a worker that has stopped.
    """
    seen = scheduler.worker_last_pass()
    if seen is None:
        return True
    if timezone.is_naive(seen):
        seen = timezone.make_aware(seen)
    return timezone.now() - seen > WORKER_SILENT_AFTER


def poll_seconds(errand: Errand) -> int:
    """How long the fragment waits before asking again, by the errand's age (#1154).

    Decided here and carried by the fragment, so each answer sets the wait for the next
    question and the page's script has nothing to know.
    """
    age = timezone.now() - errand.created_at
    for older_than, seconds in POLL_BACK_OFF:
        if age >= older_than:
            return seconds
    return POLL_SECONDS


def _errand(request: HttpRequest, pk: int) -> Errand:
    """The errand, if it is theirs; called interrupted first if it is past its deadline.

    The scheduler does that for every errand, but not on an instance that has none, and
    not before its next pass: the page that is being watched stops promising, and stops
    asking, on the first answer after the deadline (#1154).
    """
    errand = get_object_or_404(Errand.objects.for_user(request.user), pk=pk)
    errand_layer.fail_if_interrupted(errand)
    return errand


def state_context(errand: Errand, *, in_dialog: bool = False, first: bool = False) -> dict:
    """What the state fragment is drawn with; ``in_dialog`` is its wording for a dialog (#674).

    ``first`` is the answer to the press that started the work: the line has just appeared
    where a button was, so focus is sent to it, and again when the work finishes -- not on
    every poll in between, which would pull it out of whatever else is being read.

    ``worker_silent`` is an errand still waiting to be started whose worker has not been
    seen for five minutes: the line then says so instead of promising that the work carries
    on, and asks again far less often (#1122). A finished errand never looks, and nor does
    one that is being worked on: the worker writes its heartbeat between pieces of work, so
    during one long piece it looks silent to exactly the page that is watching it work.
    """
    silent = errand.state == ErrandState.WAITING and worker_silent()
    return {
        "errand": errand,
        "working_label": errand_layer.working_label(errand.kind),
        "poll_seconds": SILENT_POLL_SECONDS if silent else poll_seconds(errand),
        "worker_silent": silent,
        "in_dialog": in_dialog,
        "focus_result": in_dialog and (first or errand.is_finished),
    }


def _context(errand: Errand) -> dict:
    return state_context(errand)


@login_required
def errand_page(request: HttpRequest, pk: int) -> HttpResponse:
    """Where a button that sent work off lands: one line, watched until it changes."""
    return render(request, "core/errand.html", _context(_errand(request, pk)))


@transaction.non_atomic_requests
@login_required
def errand_state(request: HttpRequest, pk: int) -> HttpResponse:
    """The fragment the page polls. Ownership checked here, not only on the POST.

    Out of the request's transaction: it reads one row and writes nothing, every two seconds,
    and on SQLite it queued for the write lock behind the very work it was watching (#1129).
    Its one write is a single statement, once, for an errand past its deadline (#1154).
    """
    context = state_context(_errand(request, pk), in_dialog=request.GET.get("dialog") == "1")
    return render(request, "core/partials/errand_state.html", context)
