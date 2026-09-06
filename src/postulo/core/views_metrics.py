"""The metrics endpoint: off, or open, or behind a token, and honest about which.

The difference from ``/logs`` is worth stating, because the two look alike and are not.
A log entry names a connection, a company, an application. A metric here is a count of
things on the instance and carries nothing about anybody — which is why this one may
reasonably be left open on a private network, and why the log endpoint may not.

That is also why the answer when no token is set is *serve it* rather than *refuse*: the
numbers are not secret. The administration page says plainly that anybody who can reach
the instance can read them, so the choice is made with the facts in view.
"""

from __future__ import annotations

import hmac

from django.http import Http404, HttpRequest, HttpResponse

from . import metrics

#: What Prometheus expects to be handed.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _authorised(request: HttpRequest) -> bool:
    expected = metrics.token()
    if not expected:
        # Nothing to check against, and nothing secret to protect. Documented as such.
        return True
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip(), expected)


def scrape(request: HttpRequest) -> HttpResponse:
    if not metrics.enabled():
        raise Http404

    if not _authorised(request):
        response = HttpResponse(
            "A bearer token is required.\n", status=401, content_type="text/plain"
        )
        response["WWW-Authenticate"] = 'Bearer realm="postulo-metrics"'
        return response

    response = HttpResponse(metrics.render(), content_type=CONTENT_TYPE)
    response["Cache-Control"] = "no-store"
    return response
