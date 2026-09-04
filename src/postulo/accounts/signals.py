"""Side effects that keep accounts consistent."""

from __future__ import annotations

from allauth.account.signals import user_signed_up
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .adapter import INVITE_SESSION_KEY, pending_invite
from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="postulo_create_profile")
def create_profile(sender, instance, created, **kwargs) -> None:
    """Give every new user a profile, so views never have to wonder whether one exists."""
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(user_signed_up, dispatch_uid="postulo_consume_invite")
def consume_invite(request, user, **kwargs) -> None:
    """Spend the invitation that permitted this signup."""
    invite = pending_invite(request)
    if invite is not None:
        invite.accept(user)
    request.session.pop(INVITE_SESSION_KEY, None)
