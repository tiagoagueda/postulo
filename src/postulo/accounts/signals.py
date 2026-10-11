"""Side effects that keep accounts consistent."""

from __future__ import annotations

import logging

from allauth.account.models import EmailAddress
from allauth.account.signals import (
    email_changed,
    password_changed,
    password_reset,
    password_set,
    user_signed_up,
)
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .adapter import INVITE_SESSION_KEY, pending_invite
from .models import AddressClaim, Profile

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="postulo_create_profile")
def create_profile(sender, instance, created, **kwargs) -> None:
    """Give every new user a profile, so views never have to wonder whether one exists.

    A new profile starts from the instance defaults an administrator may have set for
    language and time zone, and from the standard dashboard arrangement, which becomes
    that account's own the moment it is written (#123). The person can change all three
    under Settings.
    """
    if created:
        from postulo.core import site, widgets

        profile, made = Profile.objects.get_or_create(user=instance)
        if made:
            row = site.current()
            if row.default_language or row.default_time_zone:
                profile.language = row.default_language
                profile.time_zone = row.default_time_zone
            # An arrangement of its own from the day the account exists, rather than one
            # computed from the registry every time somebody looks (#123).
            widgets.seed(profile)
            profile.save(
                update_fields=[
                    "language",
                    "time_zone",
                    "dashboard_widgets",
                    "dashboard_known",
                    "updated_at",
                ]
            )


@receiver(user_signed_up, dispatch_uid="postulo_first_account")
def first_account_is_the_administrator(request, user, **kwargs) -> None:
    """The first account on an empty instance administers it.

    Somebody has to, and the person who just installed Postulo and reached its sign-up
    form is the only candidate. Their address is trusted for the same reason the
    console's is: there is nobody else yet to send a verification link on whose behalf.
    """
    from postulo.core.models import SiteSettings

    User = type(user)
    # Two sign-ups that overlap each saw no other account and both became administrators on
    # PostgreSQL, where the check ran unlocked (#738). The instance's one settings row is a
    # sentinel every sign-up queues on: the second waits until the first has committed and
    # then looks again, so exactly one is first. SQLite serialises writers anyway.
    with transaction.atomic():
        SiteSettings.objects.select_for_update().filter(pk=SiteSettings.get().pk).first()
        if User.objects.exclude(pk=user.pk).exists():
            return
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
        EmailAddress.objects.filter(user=user, email__iexact=user.email).update(verified=True)
    # An event an operator would want to see in the log whoever it was (#738).
    logger.warning("The first account (%s) was made the administrator of this instance", user.pk)


@receiver(user_signed_up, dispatch_uid="postulo_consume_invite")
def consume_invite(request, user, **kwargs) -> None:
    """Spend the invitation that permitted this signup.

    An invitation bound to an address is *not* proof of holding that mailbox: Postulo
    never sends the invitation, the administrator hands the link over however they like,
    and whoever ends up holding it can type the bound address. Marking the address
    verified here made a mis-delivered link a verified identity that was never proven, and
    skipped the defences that rest on an address being verified (#732). So the address is
    left for allauth's own verification, which mails a link to it, and the person proves it
    the way anybody does.

    An invitation is one account. If it turns out to be spent already, by a sign-up that
    overlapped this one, and nothing else opened the door, this account is refused: the
    request is one transaction, so raising here takes the new account back out (#544).
    """
    from postulo.core import site

    invite = pending_invite(request)
    if invite is not None:
        if not invite.accept(user) and not site.signup_open_now():
            raise PermissionDenied("This invitation has already been used.")
    request.session.pop(INVITE_SESSION_KEY, None)


@receiver(email_changed, dispatch_uid="postulo_gravatar_follows_the_address")
def gravatar_follows_the_primary_address(
    request, user, from_email_address, to_email_address, **kwargs
) -> None:
    """A new primary address means a new Gravatar, for those who opted in."""
    from . import avatars

    # Read afresh: the instance cached on the user may predate the person opting in.
    profile = Profile.objects.filter(user=user).first()
    if profile is not None and profile.use_gravatar:
        # allauth's view is not ours to take out of its transaction, so the wait on Gravatar
        # starts once that transaction has committed (#357).
        transaction.on_commit(lambda: avatars.fetch_gravatar(profile))


@receiver(post_save, sender=EmailAddress, dispatch_uid="postulo_date_an_address_claim")
def date_an_address_claim(sender, instance, created, raw=False, **kwargs) -> None:
    """Note when an unverified, non-primary address was put on an account (#891)."""
    if raw or not created or instance.verified or instance.primary:
        return
    AddressClaim.objects.get_or_create(address=instance)


@receiver(password_changed, dispatch_uid="postulo_recovery_links_password_changed")
@receiver(password_set, dispatch_uid="postulo_recovery_links_password_set")
@receiver(password_reset, dispatch_uid="postulo_recovery_links_password_reset")
def end_recovery_links_when_the_password_changes(request, user, **kwargs) -> None:
    """A password set by the person themselves closes the door an administrator opened (#739)."""
    from . import recovery

    recovery.end_old_credentials(user)


@receiver(user_logged_in, dispatch_uid="postulo_recovery_links_sign_in")
def end_recovery_links_when_the_person_gets_in(sender, request, user, **kwargs) -> None:
    """Signing in by any route is the person getting in: the link is not needed (#739)."""
    from . import recovery

    recovery.revoke_for(user)
