"""How an identity provider's assertion becomes, or finds, a Postulo account.

The provider has verified who the person is; Postulo still applies its own rules. A
username must be one Postulo would accept, so the ``preferred_username`` claim is
normalised or, failing that, derived from the address. A full name is filled from the
claims. And whether a *new* account may be created at all is a policy question: by
default single sign-on signs in accounts that exist, and the identity provider only
becomes the invitation when the operator says so.
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

    def populate_user(self, request: HttpRequest, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.username = username_from_claim(data.get("username") or "", user.email or "")
        return user
