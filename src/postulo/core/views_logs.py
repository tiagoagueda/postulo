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
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse

from . import logs

logger = logging.getLogger(__name__)

#: How many records one request may ask for. A collector polls; it does not need the lot.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200


def enabled() -> bool:
    return bool(getattr(settings, "POSTULO_LOGS_ENDPOINT_ENABLED", False))


def token() -> str:
    return str(getattr(settings, "POSTULO_LOGS_TOKEN", "") or "")


def _authorised(request: HttpRequest) -> bool:
    """Whether this request carries the configured token.

    Compared in constant time, which costs nothing and removes the question.
    """
    import hmac

    expected = token()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip(), expected)


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

    if not _authorised(request):
        response = JsonResponse({"detail": "A bearer token is required."}, status=401)
        response["WWW-Authenticate"] = 'Bearer realm="postulo-logs"'
        return response

    try:
        limit = min(int(request.GET.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT
    limit = max(limit, 1)
    since = request.GET.get("since", "").strip()

    records = logs.read(limit=limit, level=request.GET.get("level", ""))
    if since:
        # The collector says what it has already seen, so it is handed only what it has
        # not. String comparison is enough: the times are ISO-8601 with a fixed shape.
        records = [record for record in records if record.time > since]

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
        for record in reversed(records)
    )
    response = HttpResponse(body, content_type="application/x-ndjson; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
