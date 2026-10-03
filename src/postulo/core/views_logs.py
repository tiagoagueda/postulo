"""The log, served at ``/logs`` for something that collects logs.

Why an endpoint at all, when the ordinary answer is to read the container's stdout: a
self-hoster running Grafana Alloy, Vector or Promtail elsewhere on their network can point
it at a URL without arranging log shipping off the host. Anybody who *can* read stdout
should carry on doing that.

Four rules, and the reasons are the design:

**Off by default.** Nothing is served unless an operator asks for it.

**A 404 when off**, not a 403. A refusal confirms that something is there; a 404 says
nothing at all, and there is no reason to tell a stranger which endpoints an instance has.

**A token, not a session.** The reader is a collector, not a person. With the endpoint on
and no token set it refuses to serve and says why in the log: an unauthenticated log
endpoint is a data leak with a URL, and failing loudly is better than quietly publishing
somebody's records because a variable was forgotten.

**Answered once, not streamed.** A collector polls. A streaming response would hold a
worker open for as long as the collector cared to keep it, and there are three of them.

**In pages, from the oldest.** With ``since`` the answer is the oldest records after that
moment, so a collector that asks again with the last ``time`` it received carries on from
where it stopped, and a burst larger than one answer is handed over whole (#475).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse

from . import logs, throttle

logger = logging.getLogger(__name__)

#: How many records one request may ask for. A collector polls; it does not need the lot.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200

#: An offset whose ``+`` arrived as a space. A collector that pastes the ``time`` it was
#: given into the address without encoding it sends exactly this, and it can be nothing
#: else: a space between a date and a time is followed by more than hours and minutes.
_LOST_PLUS = re.compile(r" (\d\d:\d\d)$")


def enabled() -> bool:
    return bool(getattr(settings, "POSTULO_LOGS_ENDPOINT_ENABLED", False))


def token() -> str:
    return str(getattr(settings, "POSTULO_LOGS_TOKEN", "") or "")


def _authorised(request: HttpRequest) -> bool:
    """Whether this request carries the configured token.

    Compared in constant time, which costs nothing and removes the question. Compared as
    bytes: headers arrive decoded as Latin-1, and `compare_digest` refuses a non-ASCII
    `str` with a `TypeError` where a wrong token should simply be refused (#372).
    """
    import hmac

    expected = token()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip().encode(), expected.encode())


def _moment(written: str) -> dt.datetime:
    """The instant ``since`` names; a time with no offset is UTC. ``ValueError`` if none.

    Compared as times and not as text: the records are written with ``+00:00``, and a
    collector that sent the same instant as ``+02:00`` was answered with the wrong records,
    or with none, and a ``200`` either way.
    """
    try:
        when = dt.datetime.fromisoformat(written)
    except ValueError:
        repaired = _LOST_PLUS.sub(r"+\1", written)
        if repaired == written:
            raise
        when = dt.datetime.fromisoformat(repaired)
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def collect(request: HttpRequest) -> HttpResponse:
    """Records as one JSON object per line, oldest first, for a collector to read."""
    if not enabled():
        raise Http404

    if not token():
        # On, and open to anybody who found it. Refusing is the only safe answer, and
        # saying so in the log is what turns a silent leak into something an operator sees.
        logger.error(
            "The /logs endpoint is enabled but POSTULO_LOGS_TOKEN is not set, so it "
            "refuses to serve. Set a token or turn the endpoint off."
        )
        return JsonResponse(
            {"detail": "This endpoint is enabled but has no token configured."}, status=503
        )

    # Wrong tokens are counted per address, and an address that has spent its allowance of
    # them is refused whatever it presents next: the token is whatever the operator typed
    # (#472).
    try:
        throttle.barred("logs", request)
        if not _authorised(request):
            throttle.refused("logs", request)
            response = JsonResponse({"detail": "A bearer token is required."}, status=401)
            response["WWW-Authenticate"] = 'Bearer realm="postulo-logs"'
            return response
    except throttle.TooOften as too_often:
        refusal = JsonResponse({"detail": str(too_often)}, status=429)
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal

    # Token-guarded and, until now, unbounded. Keyed on the caller's address rather
    # than an account, because a shared token is what authorises this and there is no
    # account to key on (#112).
    try:
        throttle.endpoint("logs", request)
    except throttle.TooOften as too_often:
        refusal = JsonResponse({"detail": str(too_often)}, status=429)
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal

    try:
        limit = min(int(request.GET.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT
    limit = max(limit, 1)
    level = request.GET.get("level", "")
    since = request.GET.get("since", "").strip()

    if since:
        try:
            moment = _moment(since)
        except ValueError:
            # Not a default, as a bad `limit` is: guessing would hand over the wrong
            # records and the collector would file them as the right ones.
            return JsonResponse(
                {
                    "detail": "`since` is not a time. Write it as ISO 8601, for example "
                    "2026-09-10T08:00:00+00:00."
                },
                status=400,
            )
        # The collector says what it has already seen and is handed the oldest of what it
        # has not, so that asking again carries on from there (#475).
        records = logs.after(moment, limit=limit, level=level)
    else:
        records = logs.read(limit=limit, level=level)[::-1]

    # Oldest first, which is the order a collector wants to append them in.
    body = "".join(
        json.dumps(
            {
                "time": record.time,
                "level": record.level,
                "logger": record.logger,
                "message": record.message,
                **record.extras,
            },
            ensure_ascii=False,
            default=str,
        )
        + "\n"
        for record in records
    )
    response = HttpResponse(body, content_type="application/x-ndjson; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
