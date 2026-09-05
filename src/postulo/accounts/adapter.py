"""Registration policy for a self-hosted instance."""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from postulo.core import site

from .models import Invite

INVITE_SESSION_KEY = "postulo_invite_token"


def pending_invite(request: HttpRequest) -> Invite | None:
    """Return the still-valid invitation held in this session, if any."""
    token = request.session.get(INVITE_SESSION_KEY)
    if not token:
        return None
    invite = Invite.objects.filter(token=token).first()
    return invite if invite and invite.is_valid() else None


class AccountAdapter(DefaultAccountAdapter):
    """Close registration unless the operator opened it or an invitation was followed.

    An instance holding one person's employment history has no reason to accept
    strangers by default, so the answer is no unless something says otherwise.
    """

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        if site.signup_open_now():
            return True
        return pending_invite(request) is not None

    def clean_email(self, email: str) -> str:
        """Enforce an invitation that names a specific address.

        Suggesting the address in a message is not enough: without this check, an
        invitation addressed to one person could be redeemed by anyone holding the link.
        """
        email = super().clean_email(email)
        request = getattr(self, "request", None) or getattr(self, "_request", None)
        if request is None or site.registration_open():
            return email
        invite = pending_invite(request)
        if invite and invite.email and invite.email.casefold() != email.casefold():
            raise ValidationError(
                _("This invitation may only be used with the address it was sent to.")
            )
        return email
