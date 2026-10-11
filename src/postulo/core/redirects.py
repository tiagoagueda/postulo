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
    if _on_this_site(target) and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def _on_this_site(target: str) -> bool:
    """Whether ``target`` is an absolute address or a path from the root of this site.

    Django accepts a scheme-less, host-less string such as ``abc`` or ``1`` as a relative
    reference, and ``redirect("abc")`` then tries to *reverse it as a view name* and answers
    500 -- while a name with no colon, like ``account_login``, would be reversed and followed
    (#768). So a relative address has to start with one slash, and not two (a network-path
    reference) nor a slash and a backslash (which a browser reads as two). An absolute
    address is left to Django's own host and scheme check.
    """
    if not target:
        return False
    if target.startswith("/"):
        return not target.startswith(("//", "/\\"))
    return "://" in target[:12]
