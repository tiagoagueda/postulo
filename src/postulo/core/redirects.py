"""Going back where the person came from, and nowhere else.

Many actions take a ``next`` parameter so that a button on the board or the dashboard
returns to the page it was pressed on. A ``next`` is a value from the request, and a
value from the request can point anywhere: a form on a hostile page could post to Postulo
and send the person on to a look-alike sign-in. So every ``next`` goes through here, and
one that leaves this host — or drops from https to http — is replaced by the fallback the
view would have used anyway.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request: HttpRequest, fallback: str, *, key: str = "next") -> str:
    """The request's ``next`` when it stays on this host and scheme; ``fallback`` otherwise."""
    target = request.POST.get(key, "") or request.GET.get(key, "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback
