"""The answers a suggestion box asks for as it is typed in (#1251)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import suggest, throttle


@login_required
@require_GET
def suggestions(request, kind: str) -> HttpResponse:
    """The options of ``kind`` that fit ``?q=``, as list items for the box's listbox.

    For the signed-in person only: a suggester is given the account and reads what that
    account recorded. Bounded per account by `POSTULO_SUGGESTION_RATE` -- a box that asks at
    every pause in typing is a way of making the server search as fast as a script can ask
    -- and what was typed is cut to a name's length and goes nowhere but the search.
    """
    if not suggest.known(kind):
        raise Http404
    try:
        throttle.consume("suggestions", request.user, throttle.rate_for("POSTULO_SUGGESTION_RATE"))
    except throttle.TooOften as too_often:
        refusal = HttpResponse(str(too_often), status=429, content_type="text/plain")
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal
    values = suggest.suggest(kind, request.user, request.GET.get("q", ""))
    response = render(request, "core/partials/suggestions.html", {"values": values})
    # What one person typed, answered for them: no cache between them and it.
    response["Cache-Control"] = "private, no-store"
    return response
