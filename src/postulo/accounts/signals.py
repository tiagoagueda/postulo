"""Side effects that keep accounts consistent."""

from __future__ import annotations

from allauth.account.models import EmailAddress
from allauth.account.signals import email_changed, user_signed_up
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .adapter import INVITE_SESSION_KEY, pending_invite
from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="postulo_create_profile")
def create_profile(sender, instance, created, **kwargs) -> None:
    """Give every new user a profile, so views never have to wonder whether one exists.

    A new profile starts from the instance defaults an administrator may have set for
    language and time zone; the person can change both under Settings.
    """
    if created:
        from postulo.core import site

        profile, made = Profile.objects.get_or_create(user=instance)
        if made:
            row = site.current()
            if row.default_language or row.default_time_zone:
                profile.language = row.default_language
                profile.time_zone = row.default_time_zone
                profile.save(update_fields=["language", "time_zone", "updated_at"])


@receiver(user_signed_up, dispatch_uid="postulo_first_account")
def first_account_is_the_administrator(request, user, **kwargs) -> None:
    """The first account on an empty instance administers it.

    Somebody has to, and the person who just installed Postulo and reached its sign-up
    form is the only candidate. Their address is trusted for the same reason the
    console's is: there is nobody else yet to send a verification link on whose behalf.
    """
    User = type(user)
    if User.objects.exclude(pk=user.pk).exists():
        return
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    EmailAddress.objects.filter(user=user, email__iexact=user.email).update(verified=True)


@receiver(user_signed_up, dispatch_uid="postulo_consume_invite")
def consume_invite(request, user, **kwargs) -> None:
    """Spend the invitation that permitted this signup.

    An invitation addressed to one email address was delivered to that address, and
    following its link is proof of holding the mailbox — the same proof a verification
    link would give. So the address is recorded as verified here, before allauth decides
    whether to send one, and the invited person is not asked to prove it twice.
    """
    invite = pending_invite(request)
    if invite is not None:
        invite.accept(user)
        if invite.email and invite.email.casefold() == (user.email or "").casefold():
            EmailAddress.objects.filter(user=user, email__iexact=user.email).update(verified=True)
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
        avatars.fetch_gravatar(profile)
