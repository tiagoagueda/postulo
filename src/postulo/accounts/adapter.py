"""Registration policy for a self-hosted instance."""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from postulo.core import site

from .models import Invite

#: Holds the fingerprint of the invitation followed, never the token: the session is
#: server-side, so either would do, and the one that opens nothing is the one to keep.
INVITE_SESSION_KEY = "postulo_invite"


def pending_invite(request: HttpRequest, *, lock: bool = False) -> Invite | None:
    """Return the still-valid invitation held in this session, if any.

    With ``lock``, the row is held until the request's transaction ends, so a second
    sign-up through the same invitation waits here and then reads it as spent (#544). On
    PostgreSQL two requests otherwise both read it unspent, and both go on to make an
    account. SQLite has no such lock and needs none: its requests already take turns.
    """
    held = request.session.get(INVITE_SESSION_KEY)
    if not held:
        return None
    invites = Invite.objects.filter(token_fingerprint=held)
    # Only inside a transaction, which a request is. Outside one there is nothing for the
    # lock to last until, and a database that has the lock refuses to take it.
    if lock and transaction.get_connection().in_atomic_block:
        invites = invites.select_for_update()
    invite = invites.first()
    return invite if invite and invite.is_valid() else None


def account_named(login: str) -> int | None:
    """The account a sign-in under this name is checked against, or nothing.

    An address before a username, which is the order allauth tries them in. An address
    counts whether or not it has been confirmed and whether it is a row of allauth's or
    only the one on the account, because allauth checks the password against all of those.
    """
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    login = login.strip()
    if not login:
        return None
    people = get_user_model().objects
    return (
        EmailAddress.objects.filter(email__iexact=login)
        .order_by("-verified", "pk")
        .values_list("user_id", flat=True)
        .first()
        or people.filter(email__iexact=login).values_list("pk", flat=True).first()
        or people.filter(username__iexact=login).values_list("pk", flat=True).first()
    )


class AccountAdapter(DefaultAccountAdapter):
    """Close registration unless the operator opened it or an invitation was followed.

    An instance holding one person's employment history has no reason to accept
    strangers by default, so the answer is no unless something says otherwise.
    """

    def _get_login_attempts_cache_key(self, request: HttpRequest, **credentials) -> str:
        """Count failed sign-ins against the account, however it was named (#489).

        allauth counts them against what was typed, in lower case. Capitals are therefore
        counted together already, but a username and each of an account's addresses are
        different things to type, and each had five guesses of its own. Somebody guessing
        from many addresses, which is who this limit is for, had five per name.

        So the name is resolved to its account first, and the count is the account's. A
        name nobody has is counted under the name, without its capitals. The two kinds of
        key start differently, so nothing somebody types can land on an account's count.
        The same key is what a password reset clears, and it arrives here by the same road.

        allauth also puts the host the request named in front. An instance is one site
        whatever name it is reached by, and a second name was a second set of counts.

        The method is allauth's and its name says it is private. If a release renames it
        this stops being called, and `tests/security/test_admin_exposure.py` fails on the
        sixth guess.
        """
        named = (credentials.get("email") or credentials.get("username") or "").strip()
        account = account_named(named)
        if account is None:
            return f"name:{named.casefold()}"
        return f"account:{account}"

    def get_login_stages(self) -> list[str]:
        """allauth's steps after a sign-in, with Postulo's own second-factor rule.

        The stage is swapped rather than the setting changed, because what it decides
        depends on how somebody signed in — which is a question about this request, not
        about the instance.
        """
        stages = super().get_login_stages()
        return [
            "postulo.accounts.stages.SecondFactorStage"
            if stage == "allauth.mfa.stages.AuthenticateStage"
            else stage
            for stage in stages
        ]

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        if site.signup_open_now():
            return True
        # A POST is the sign-up itself, and it holds the invitation while it uses it.
        # Showing the form is only a look.
        return pending_invite(request, lock=request.method == "POST") is not None

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
