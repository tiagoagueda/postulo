"""Asking, once and only when told to, whether a link still answers.

A portfolio address that returns a 404 on the day a recruiter clicks it is the worst
outcome this whole record is meant to prevent, and it is invisible from inside Postulo
because nothing here ever visits it. So there is a *Check* button, and what it does is
exactly what it says: one request per link, when a person presses it, through a guarded
client — public addresses only, a short timeout, no redirects onto somebody's router.

That last part is the reason this does not simply build an ``httpx.Client``. A client
told to follow redirects makes each hop itself, so checking the address a person saved
says nothing about where the request ends up: a public host answering ``302 Location:
http://127.0.0.1:9000/`` would be followed, and the status that came back would be
written onto the link and shown in the interface. On a self-hosted instance sitting
beside a router, a NAS and a hypervisor, that turns *Check* into a scanner for the
network Postulo happens to be on. ``public_only_client`` runs the check on every request
instead, redirects included.

Nothing checks anything on a schedule. A job tracker that quietly makes requests on a
person's behalf is a different thing from one that answers a question they asked.
"""

from __future__ import annotations

import httpx
from django.utils import timezone
from django.utils.translation import gettext as _

from postulo.plugins import http
from postulo.plugins.fetching import UnsafeURL, validate_public_url

from .models import Link, LinkStatus

TIMEOUT = 8.0
MAX_REDIRECTS = 3


def check(link: Link) -> Link:
    """Ask whether ``link`` answers, and record what came back. Never raises."""
    status, detail = _ask(link.url)
    link.check_status = status
    link.check_detail = detail[:250]
    link.checked_at = timezone.now()
    link.save(update_fields=["check_status", "check_detail", "checked_at", "updated_at"])
    return link


def check_all(owner) -> tuple[int, int]:
    """Check every link this person has. Returns (answered, did not answer)."""
    ok = broken = 0
    for link in Link.objects.for_user(owner):
        check(link)
        if link.is_broken:
            broken += 1
        else:
            ok += 1
    return ok, broken


def _ask(url: str) -> tuple[str, str]:
    # Checked here as well as in the hook so a private address gives the person the
    # sentence explaining why, rather than the hook's refusal wrapped in a transport error.
    try:
        validate_public_url(url)
    except UnsafeURL as error:
        return LinkStatus.BROKEN, str(error)

    try:
        with http.public_only_client(timeout=TIMEOUT, max_redirects=MAX_REDIRECTS) as client:
            response = client.head(url)
            # Plenty of sites answer HEAD with 403 or 405 and are perfectly fine; ask
            # again properly rather than telling somebody their portfolio is broken.
            if response.status_code in (401, 403, 405, 501) or response.status_code >= 500:
                response = client.get(url)
    except http.DestinationRefused as error:
        return LinkStatus.BROKEN, str(
            _("It redirects to a private or local address, which was not followed.")
        ) + f" ({error})"
    except httpx.HTTPError as error:
        return LinkStatus.BROKEN, f"{type(error).__name__}: {error}"

    if response.status_code >= 400:
        return LinkStatus.BROKEN, str(
            _("The address answered %(code)s.") % {"code": response.status_code}
        )
    return LinkStatus.OK, str(_("Answered %(code)s.") % {"code": response.status_code})
