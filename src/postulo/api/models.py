"""Credentials for the capture API.

The API exists so that something outside Postulo — a browser extension, a script, a
shortcut on a phone — can hand over a posting. That means a credential that is not the
owner's password, can be handed to one device, and can be taken away again without
disturbing anything else.
"""

from __future__ import annotations

import hashlib
import secrets

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

TOKEN_BYTES = 32
#: Enough of the token to tell two of them apart in a list, and far too little to use.
PREFIX_LENGTH = 8


class CaptureTokenQuerySet(models.QuerySet):
    def for_user(self, user) -> CaptureTokenQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def active(self) -> CaptureTokenQuerySet:
        return self.filter(revoked_at__isnull=True)


class CaptureToken(OwnedModel):
    """A bearer token that may do exactly one thing: submit a capture.

    Only a hash is stored. Postulo shows the token once, at the moment it is created, and
    then cannot show it again — which is inconvenient exactly once, and means a copy of
    the database is not a set of working credentials.

    The token's scope is not configurable because there is nothing to configure: the API
    it reaches consists of captures and a call to check the token itself. It cannot read
    an application, a CV, or anything else.
    """

    name = models.CharField(
        _("name"), max_length=100, help_text=_("Which device or tool this is for.")
    )
    prefix = models.CharField(_("prefix"), max_length=PREFIX_LENGTH, editable=False)
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True, editable=False)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(_("revoked"), null=True, blank=True, editable=False)

    objects = CaptureTokenQuerySet.as_manager()

    class Meta:
        verbose_name = _("capture token")
        verbose_name_plural = _("capture tokens")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Hash a token for storage and lookup.

        A plain SHA-256 rather than a password hash on purpose: this is 32 bytes from a
        cryptographic random source, not something a person chose, so there is no
        dictionary to attack and nothing for a slow hash to buy. Being fast also means
        the lookup is a single indexed query.
        """
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, owner, name: str) -> tuple[CaptureToken, str]:
        """Create a token, returning the record and the secret exactly once."""
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls.objects.create(
            owner=owner,
            name=name,
            prefix=raw_token[:PREFIX_LENGTH],
            token_hash=cls.hash_token(raw_token),
        )
        return token, raw_token

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at", "updated_at"])

    def record_use(self) -> None:
        """Note that the token was used, without writing on every single request."""
        now = timezone.now()
        if self.last_used_at is None or (now - self.last_used_at).total_seconds() > 300:
            self.last_used_at = now
            self.save(update_fields=["last_used_at"])
