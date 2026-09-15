"""The two addresses the browser notifier needs a browser to reach (#209).

A plugin cannot serve an address, so these are Postulo's: the service worker, which has to sit
at the root of the site to receive pushes for all of it, and the place an open tab collects
what is waiting for it.
"""

from __future__ import annotations

import functools
from pathlib import Path

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from . import inbox

WORKER = Path(__file__).resolve().parents[1] / "static" / "js" / "sw.js"


@functools.cache
def _worker_source() -> bytes:
    return WORKER.read_bytes()


@require_GET
def worker(request: HttpRequest) -> HttpResponse:
    """The service worker, from the root so that its scope is the whole site.

    From a view rather than from ``/static/``: a worker controls the path it is served from
    and below, and under the static prefix it would control nothing anybody visits. Not cached
    for long, so a fixed worker reaches browsers on their next visit rather than next month.
    """
    response = HttpResponse(_worker_source(), content_type="text/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


@require_POST
def waiting(request: HttpRequest) -> JsonResponse:
    """Hand an open tab the notifications waiting for it, and mark them shown.

    A POST because it changes something: the notices it answers with are not answered again.
    Somebody signed out gets a 401 rather than the sign-in page, which a script cannot use.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"notices": []}, status=401)
    response = JsonResponse({"notices": inbox.collect(request.user)})
    response["Cache-Control"] = "no-store"
    return response
