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

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from . import errands as errand_layer
from .models import Errand

#: How often the fragment asks again. Long enough not to be a load, short enough that a
#: capture that took four seconds is not reported after ten.
POLL_SECONDS = 2


def _errand(request: HttpRequest, pk: int) -> Errand:
    return get_object_or_404(Errand.objects.for_user(request.user), pk=pk)


def _context(errand: Errand) -> dict:
    return {
        "errand": errand,
        "working_label": errand_layer.working_label(errand.kind),
        "poll_seconds": POLL_SECONDS,
    }


@login_required
def errand_page(request: HttpRequest, pk: int) -> HttpResponse:
    """Where a button that sent work off lands: one line, watched until it changes."""
    return render(request, "core/errand.html", _context(_errand(request, pk)))


@login_required
def errand_state(request: HttpRequest, pk: int) -> HttpResponse:
    """The fragment the page polls. Ownership checked here, not only on the POST."""
    return render(request, "core/partials/errand_state.html", _context(_errand(request, pk)))
