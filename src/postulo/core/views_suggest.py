"""The answers a suggestion box asks for as it is typed in (#1251)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import suggest, throttle


def _too_often(request) -> HttpResponse | None:
    """The refusal for an account asking faster than it may, or nothing."""
    try:
        throttle.consume("suggestions", request.user, throttle.rate_for("POSTULO_SUGGESTION_RATE"))
    except throttle.TooOften as too_often:
        refusal = HttpResponse(str(too_often), status=429, content_type="text/plain")
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal
    return None


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
    if (refusal := _too_often(request)) is not None:
        return refusal
    values = suggest.suggest(kind, request.user, request.GET.get("q", ""))
    response = render(request, "core/partials/suggestions.html", {"values": values})
    # What one person typed, answered for them: no cache between them and it.
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def fills(request, kind: str) -> HttpResponse:
    """What choosing ``?value=`` in a box of ``kind`` fills in beside it, as JSON.

    Asked once, when an option is taken, and never as somebody types; on the same account
    budget as the suggestions. The answer is from this person's own records only, and the
    script puts it into fields that are empty and nowhere else.
    """
    if not suggest.known(kind):
        raise Http404
    if (refusal := _too_often(request)) is not None:
        return refusal
    answer = JsonResponse(
        {"fills": suggest.fills(kind, request.user, request.GET.get("value", ""))}
    )
    answer["Cache-Control"] = "private, no-store"
    return answer
