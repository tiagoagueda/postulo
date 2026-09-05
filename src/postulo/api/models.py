"""Credentials for the API.

The API exists so that something outside Postulo — a browser extension, a script, an
agent — can act for a person. That means a credential that is not their password, can be
handed to one device or tool, does only what it was allowed to, and can be taken away
again without disturbing anything else.
"""

from __future__ import annotations

import hashlib
import secrets

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

#: What a token may do. A token holds any set of these; the capture API needs only the first.
SCOPES = {
    "captures": _("Capture postings"),
    "read": _("Read everything: applications, listings, companies, documents, insights"),
    "write": _("Record and change: applications, listings, notes, reminders, letters"),
    "documents:read": _("Download the files themselves"),
}

TOKEN_BYTES = 32
#: Enough of the token to tell two of them apart in a list, and far too little to use.
PREFIX_LENGTH = 8


class ApiTokenQuerySet(models.QuerySet):
    def for_user(self, user) -> ApiTokenQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def active(self) -> ApiTokenQuerySet:
        now = timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class ApiToken(OwnedModel):
    """A bearer token with scopes, shown once.

    Only a hash is stored. Postulo shows the token once, at the moment it is created, and
    then cannot show it again — which is inconvenient exactly once, and means a copy of
    the database is not a set of working credentials.

    Scopes say what it may do. A token for a browser extension holds ``captures`` and
    nothing else, and a leak costs its holder the ability to fill a review queue somebody
    will then decline. A token for an agent may hold ``read``, and ``write`` if the person
    means it; files travel only under ``documents:read``, because they are the most
    sensitive thing here.
    """

    name = models.CharField(
        _("name"), max_length=100, help_text=_("Which device or tool this is for.")
    )
    prefix = models.CharField(_("prefix"), max_length=PREFIX_LENGTH, editable=False)
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True, editable=False)
    scopes = models.JSONField(_("scopes"), default=list, blank=True)
    expires_at = models.DateTimeField(_("expires"), null=True, blank=True)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(_("revoked"), null=True, blank=True, editable=False)

    objects = ApiTokenQuerySet.as_manager()

    class Meta:
        verbose_name = _("API token")
        verbose_name_plural = _("API tokens")
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
    def issue(cls, owner, name: str, scopes=("captures",), expires_at=None) -> tuple[ApiToken, str]:
        """Create a token, returning the record and the secret exactly once."""
        unknown = set(scopes) - set(SCOPES)
        if unknown:
            raise ValueError(f"Unknown scopes: {sorted(unknown)}")
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls.objects.create(
            owner=owner,
            name=name,
            prefix=raw_token[:PREFIX_LENGTH],
            token_hash=cls.hash_token(raw_token),
            scopes=sorted(set(scopes)),
            expires_at=expires_at,
        )
        return token, raw_token

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and not self.is_expired

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    @property
    def scope_labels(self) -> list[str]:
        return [str(SCOPES.get(scope, scope)) for scope in self.scopes or []]

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
