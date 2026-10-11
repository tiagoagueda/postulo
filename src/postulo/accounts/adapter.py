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


def _on_the_public_address(url: str) -> str:
    """``url`` with its scheme and host replaced by ``POSTULO_PUBLIC_URL``, where that is set."""
    from urllib.parse import urlsplit

    from django.conf import settings

    base = (getattr(settings, "POSTULO_PUBLIC_URL", "") or "").rstrip("/")
    if not base:
        return url
    parts = urlsplit(url)
    rest = parts.path + (f"?{parts.query}" if parts.query else "")
    return f"{base}{rest}"


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

    def _typed_name_key(self, **credentials) -> str:
        named = (credentials.get("email") or credentials.get("username") or "").strip()
        return f"name:{named.casefold()}"

    def pre_authenticate(self, request: HttpRequest, **credentials) -> None:
        """Count each guess twice, and say a lock only when the name that was typed is spent (#744).

        The count under the account is what stops a guesser who spreads their five guesses over
        an account's names (#489). But if the answer to the sixth guess differs by which names
        the account has, trying a name and seeing "too many attempts" tells you it belongs to the
        account that was being guessed at, and anyone could shut anybody's password sign-in for
        five minutes and read it off. So there are two counts: the **typed name's**, which says
        "too many attempts" because only the person who typed it is told anything, and the
        **account's**, which refuses just the same but in the ordinary words for a wrong
        password. The lock holds either way; it is only the telling that is even.
        """
        from allauth.account import app_settings
        from allauth.core.internal import ratelimit

        by_name = ratelimit.consume(
            request,
            config=app_settings.RATE_LIMITS,
            action="login_failed",
            key=self._typed_name_key(**credentials),
        )
        self._login_failed_rl_usage = by_name
        if not by_name:
            raise self.validation_error("too_many_login_attempts")
        key = self._get_login_attempts_cache_key(request, **credentials)
        if key.startswith("account:"):
            # Only the per-name part of the rate: the per-address part was spent by the first
            # count, and spending it twice halved what an address was allowed.
            configured = str(app_settings.RATE_LIMITS.get("login_failed") or "")
            per_name = ",".join(r for r in configured.split(",") if r.strip().endswith("/key"))
            by_account = ratelimit.consume(
                request, config={"login_failed": per_name}, action="login_failed", key=key
            )
            self._account_login_failed_rl_usage = by_account
            if not by_account:
                by_name.rollback()
                raise self.validation_error("username_password_mismatch")

    def _rollback_login_failed_rl_usage(self) -> None:
        super()._rollback_login_failed_rl_usage()
        usage = getattr(self, "_account_login_failed_rl_usage", None)
        if usage:
            usage.rollback()
            self._account_login_failed_rl_usage = None

    def _delete_login_attempts_cached_email(self, request: HttpRequest, **credentials) -> None:
        """A password reset clears both counts, as it cleared the one (#744)."""
        from allauth.account import app_settings
        from allauth.core.internal import ratelimit

        super()._delete_login_attempts_cached_email(request, **credentials)
        ratelimit.clear(
            request,
            config=app_settings.RATE_LIMITS,
            action="login_failed",
            key=self._typed_name_key(**credentials),
        )

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

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        """Name the instance in a message by its own name and public address (#747).

        allauth names "the site" from the Host header of the request, so a mail asked for
        with a Host of the sender's choosing greeted its recipient from the sender's domain.
        The instance has a name (`site.instance_name`) and, when the operator set one, an
        address; the greeting and the sign-off use those.
        """
        from types import SimpleNamespace
        from urllib.parse import urlsplit

        from django.conf import settings

        public = (getattr(settings, "POSTULO_PUBLIC_URL", "") or "").rstrip("/")
        if public and "current_site" not in context:
            context = {
                **context,
                "current_site": SimpleNamespace(
                    name=site.instance_name(), domain=urlsplit(public).netloc
                ),
            }
        super().send_mail(template_prefix, email, context)

    def format_email_subject(self, subject: str) -> str:
        """``[Postulo] Password reset``, not ``[the-host-header] Password reset`` (#747)."""
        from django.conf import settings

        if getattr(settings, "POSTULO_PUBLIC_URL", ""):
            from allauth.account import app_settings

            if app_settings.EMAIL_SUBJECT_PREFIX is None:
                return f"[{site.instance_name()}] {subject}"
        return super().format_email_subject(subject)

    def get_reset_password_from_key_url(self, key: str) -> str:
        """The link in the reset mail, on the address the operator named when they named one (#747).

        allauth builds it from the Host header of the request that asked for the mail, so a
        request with a Host of the sender's choosing made a mail to the victim whose link
        pointed at the sender's server. With ``POSTULO_PUBLIC_URL`` set, the link is on that.
        """
        url = super().get_reset_password_from_key_url(key)
        return _on_the_public_address(url)

    def get_email_confirmation_url(self, request: HttpRequest, emailconfirmation) -> str:
        """The same for the confirmation mail (#747)."""
        return _on_the_public_address(
            super().get_email_confirmation_url(request, emailconfirmation)
        )

    def is_safe_url(self, url: str) -> bool:
        """allauth's rule, and https where the instance is: one rule for every ``next`` (#746).

        allauth accepts an absolute address on any host the instance answers to with either
        scheme, so a sign-in, a sign-out or a sign-up could be sent on to the http address of
        an instance that is served over https -- which is the downgrade `core.redirects.safe_next`
        refuses everywhere else. A relative address has no scheme and is untouched.
        """
        from urllib.parse import urlsplit

        from allauth.core import context

        if not super().is_safe_url(url):
            return False
        request = getattr(context, "request", None)
        scheme = urlsplit(url).scheme
        return not (request is not None and request.is_secure() and scheme == "http")

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
