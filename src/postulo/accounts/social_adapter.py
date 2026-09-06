"""How an identity provider's assertion becomes, or finds, a Postulo account.

The provider has verified who the person is; Postulo still applies its own rules. A
username must be one Postulo would accept, so the ``preferred_username`` claim is
normalised or, failing that, derived from the address. A full name is filled from the
claims. And whether a *new* account may be created at all is a policy question: by
default single sign-on signs in accounts that exist, and the identity provider only
becomes the invitation when the operator says so.

**Finding an account that already exists** is the other half, and it is worth being
explicit about what it trusts. With ``POSTULO_OIDC_LINK_BY_EMAIL`` on, which is the
default, somebody arriving through the provider is signed in as the local account holding
the address the provider asserts. allauth only ever matches an address the provider
marked verified, so an unverified claim links to nothing — but "verified" is the
provider's word, and the security of the whole arrangement is the operator's answer to
one question: does *their* provider only mark an address verified when the person proved
they hold it? For a Keycloak, Authentik or Pocket ID they run, yes. For an endpoint that
merely speaks OpenID Connect, not necessarily. Turning the setting off makes a person
sign in locally once and connect the provider from their own account page instead.
"""

from __future__ import annotations

import re

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from postulo.core import site

from .adapter import pending_invite
from .models import unique_username
from .validators import USERNAME_MAX_LENGTH, USERNAME_PATTERN, slug_from_email


def username_from_claim(preferred: str, email: str) -> str:
    """A username Postulo accepts, from what the provider suggested or from the address."""
    candidate = re.sub(r"[^a-z0-9._-]+", "-", (preferred or "").strip().casefold())
    candidate = re.sub(r"[._-]{2,}", "-", candidate).strip("._-")[:USERNAME_MAX_LENGTH]
    if not USERNAME_PATTERN.fullmatch(candidate):
        candidate = slug_from_email(email or "")
    User = get_user_model()
    return unique_username(candidate, lambda name: User.objects.filter(username=name).exists())


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin) -> bool:
        """Whether the provider may create an account here.

        Yes when the operator said the identity provider is the invitation, when
        registration is open anyway, or when this person followed an invitation link.
        Otherwise single sign-on only signs in the accounts that already exist.
        """
        if getattr(settings, "POSTULO_OIDC_AUTO_SIGNUP", False):
            return True
        if site.signup_open_now():
            return True
        return pending_invite(request) is not None

    def can_authenticate_by_email(self, sociallogin, email: str) -> bool:
        """Whether a verified address from the provider signs somebody into that account.

        allauth reads its own setting for this, which is derived from
        ``POSTULO_OIDC_LINK_BY_EMAIL`` at start-up. Deciding it here as well means the
        variable is authoritative wherever it is read, rather than there being two
        settings that could drift apart — and it puts Postulo's policy in the one file
        that already holds the rest of it.

        allauth has already narrowed what reaches this to addresses the provider marked
        verified; the question left is whether this instance takes the provider's word.
        """
        from . import sso

        if not sso.link_by_email():
            return False
        return super().can_authenticate_by_email(sociallogin, email)

    def populate_user(self, request: HttpRequest, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.username = username_from_claim(data.get("username") or "", user.email or "")
        return user
