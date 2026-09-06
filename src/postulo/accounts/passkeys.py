"""What the interface needs to know about passkeys, in one place.

A passkey is held by the device or the password manager and unlocked by a fingerprint, a
face or a PIN. It is two factors on its own — something you have, released by something
you are or know — and there is no shared secret anywhere for a leak or a convincing e-mail
to get hold of. It is the best way in Postulo has to offer.

Two facts govern whether it can be offered at all, and both are the browser's rules rather
than Postulo's:

* **the page must be a secure context** — HTTPS, with ``localhost`` the exception. Over
  plain HTTP the browser refuses the API outright, so an instance reached at a bare address
  on a mesh VPN cannot use passkeys however it is configured;
* **a passkey is bound to the host it was made at.** Reaching the same instance at a second
  name, or moving it to a new one, makes every existing passkey unusable there.

Saying both plainly is the whole reason this module exists: neither failure explains itself
if a person only meets it when they cannot get in.
"""

from __future__ import annotations

from django.http import HttpRequest

#: Hosts a browser treats as a secure context even over plain HTTP.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _host(request: HttpRequest) -> str:
    """The hostname, or nothing if the request does not have a usable one.

    ``get_host`` raises on a host that is not allowed, and a page that merely *mentions*
    passkeys should not be the thing that turns that into a 500.
    """
    from django.core.exceptions import DisallowedHost

    try:
        return request.get_host().partition(":")[0]
    except DisallowedHost:
        return ""


def usable_here(request: HttpRequest) -> bool:
    """Whether the browser will let this page create or use a passkey at all."""
    from django.conf import settings

    if getattr(settings, "MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN", False):
        return True
    if request.is_secure():
        return True
    return _host(request) in LOCAL_HOSTS


def bound_to(request: HttpRequest) -> str:
    """The host a passkey made now would be tied to, which is what the browser will check."""
    return _host(request)


def summary(user, request: HttpRequest) -> dict:
    """Everything the account page shows about passkeys."""
    from allauth.mfa.models import Authenticator

    keys = list(
        Authenticator.objects.filter(user=user, type=Authenticator.Type.WEBAUTHN).order_by(
            "-created_at"
        )
    )
    return {
        "keys": keys,
        "count": len(keys),
        "usable": usable_here(request),
        "bound_to": bound_to(request),
        "has_password": user.has_usable_password(),
        # Recovery codes matter more once a passkey can be the only way in: lose the
        # device and there is nothing else to try.
        "has_recovery_codes": Authenticator.objects.filter(
            user=user, type=Authenticator.Type.RECOVERY_CODES
        ).exists(),
    }
