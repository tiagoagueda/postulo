"""The user model, personal profiles, and invitations.

The user model is defined in the very first migration on purpose: swapping
``AUTH_USER_MODEL`` after a database exists is painful, and Postulo is multi-user from
day one.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

INVITE_TOKEN_BYTES = 32
DEFAULT_INVITE_VALIDITY = timedelta(days=14)


class UserManager(DjangoUserManager):
    """Manager for a user identified by email address rather than a username."""

    def _create_user(self, email, password, **extra_fields):  # type: ignore[override]
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):  # type: ignore[override]
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email=None, password=None, **extra_fields):  # type: ignore[override]
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """A person applying for jobs."""

    username = None  # type: ignore[assignment]
    email = models.EmailField(_("email address"), unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    objects = UserManager()  # type: ignore[misc,assignment]

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined]
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.email

    @property
    def display_name(self) -> str:
        """A friendly name for the interface, falling back to the email address."""
        return self.get_full_name() or self.email.split("@")[0]


class Theme(models.TextChoices):
    SYSTEM = "system", _("Match the operating system")
    LIGHT = "light", _("Light")
    DARK = "dark", _("Dark")


class Profile(models.Model):
    """Personal details and preferences.

    The contact block is kept here rather than on the user because it is *content*: it
    is what gets rendered onto a CV, and it changes for reasons that have nothing to do
    with authentication.

    Neither ``language`` nor ``time_zone`` declares model-level choices. Doing so would
    write hundreds of time zone names into a migration and produce a fresh migration
    every time the IANA database or the language list changed. The choices belong to
    the form, which is where they are actually needed.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("user"),
    )

    headline = models.CharField(
        _("headline"),
        max_length=200,
        blank=True,
        help_text=_("A short professional title, such as “Backend engineer”."),
    )
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    location = models.CharField(
        _("location"),
        max_length=120,
        blank=True,
        help_text=_("City and country, as it should appear on a CV."),
    )
    website = models.URLField(_("website"), blank=True)
    linkedin_url = models.URLField(_("LinkedIn"), blank=True)
    source_repo_url = models.URLField(
        _("code repository"),
        blank=True,
        help_text=_("A profile on GitHub, Forgejo, GitLab or similar."),
    )

    language = models.CharField(_("language"), max_length=10, blank=True)
    time_zone = models.CharField(_("time zone"), max_length=64, blank=True)
    theme = models.CharField(_("theme"), max_length=10, choices=Theme, default=Theme.SYSTEM)

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("profile")
        verbose_name_plural = _("profiles")

    def __str__(self) -> str:
        return f"Profile for {self.user}"


class InviteQuerySet(models.QuerySet):
    def pending(self) -> InviteQuerySet:
        """Invitations that could still be accepted right now."""
        return self.filter(accepted_at__isnull=True, expires_at__gt=timezone.now())


def generate_invite_token() -> str:
    return secrets.token_urlsafe(INVITE_TOKEN_BYTES)


def default_invite_expiry():
    return timezone.now() + DEFAULT_INVITE_VALIDITY


class Invite(models.Model):
    """An invitation to create an account on this instance.

    A self-hosted instance is normally closed. Rather than asking an operator to choose
    between "only me" and "anyone on the internet", an invitation grants exactly one
    signup, optionally bound to one email address, and expires on its own.
    """

    token = models.CharField(
        _("token"), max_length=64, unique=True, default=generate_invite_token, editable=False
    )
    email = models.EmailField(
        _("email address"),
        blank=True,
        help_text=_("Optional. If set, only this address may use the invitation."),
    )
    note = models.CharField(
        _("note"), max_length=200, blank=True, help_text=_("A reminder of who this is for.")
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"), default=default_invite_expiry)

    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    accepted_by = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_used",
        verbose_name=_("accepted by"),
    )

    objects = InviteQuerySet.as_manager()

    class Meta:
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.email or self.note or f"Invitation {self.pk}"

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def is_valid(self, email: str | None = None) -> bool:
        """Whether this invitation may still be used, optionally by ``email``."""
        if self.is_accepted or self.is_expired:
            return False
        if self.email and email and self.email.casefold() != email.casefold():
            return False
        return True

    def accept(self, user) -> None:
        """Mark the invitation as spent by ``user``."""
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["accepted_at", "accepted_by"])
