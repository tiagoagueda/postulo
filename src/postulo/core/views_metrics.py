"""The metrics endpoint: off, or open, or behind a token, and honest about which.

The difference from ``/logs`` is worth stating, because the two look alike and are not.
A log entry names a connection, a company, an application. A metric here is a count of
things on the instance and carries nothing about anybody — which is why this one may
reasonably be left open on a private network, and why the log endpoint may not.

**A token is required (#801).** The numbers carry nothing about anybody, but they do name the
exact version of Postulo, Python and Django and say how many accounts there are, which is what
somebody picking a target wants. So with the endpoint on and no token set it refuses with a 503
and a line in the log, as `/logs` does, instead of serving whoever asks.
"""

from __future__ import annotations

import hmac
import logging

from django.http import Http404, HttpRequest, HttpResponse

from . import metrics, throttle

logger = logging.getLogger(__name__)

#: What Prometheus expects to be handed.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _authorised(request: HttpRequest) -> bool:
    expected = metrics.token()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip().encode(), expected.encode())


def scrape(request: HttpRequest) -> HttpResponse:
    if not metrics.enabled():
        raise Http404

    if not metrics.token():
        logger.error(
            "The /metrics endpoint is enabled but POSTULO_METRICS_TOKEN is not set, so it "
            "refuses to serve. Set a token or turn the endpoint off."
        )
        return HttpResponse(
            "This endpoint is enabled but has no token configured.\n",
            status=503,
            content_type="text/plain",
        )

    # Wrong tokens are counted per address; see `views_logs.collect` (#472).
    try:
        throttle.barred("metrics", request)
        if not _authorised(request):
            throttle.refused("metrics", request)
            response = HttpResponse(
                "A bearer token is required.\n", status=401, content_type="text/plain"
            )
            response["WWW-Authenticate"] = 'Bearer realm="postulo-metrics"'
            return response
    except throttle.TooOften as too_often:
        refusal = HttpResponse(f"{too_often}\n", status=429, content_type="text/plain")
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal

    # Token-guarded and, until now, unbounded. Keyed on the caller's address rather
    # than an account, because a shared token is what authorises this and there is no
    # account to key on (#112).
    try:
        throttle.endpoint("metrics", request)
    except throttle.TooOften as too_often:
        refusal = HttpResponse(f"{too_often}\n", status=429, content_type="text/plain")
        refusal["Retry-After"] = str(too_often.retry_after)
        return refusal

    response = HttpResponse(metrics.render(), content_type=CONTENT_TYPE)
    response["Cache-Control"] = "no-store"
    return response
