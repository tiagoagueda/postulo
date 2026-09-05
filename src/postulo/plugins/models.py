"""A connection: where another service is, and how to authenticate to it, for one person.

Three kinds of plugin need one — notifiers, stores, syncs — and they share the shape:
non-secret configuration as JSON, secrets encrypted, an enabled switch, and the outcome of
the last test. A plugin describes its own fields; Postulo draws the form and keeps the
answers. One person may hold several connections to the same plugin.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

from . import secrets
from .base import CONNECTED_KINDS


class ConnectionQuerySet(models.QuerySet):
    def for_user(self, user) -> ConnectionQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def enabled(self) -> ConnectionQuerySet:
        return self.filter(enabled=True)

    def of_kind(self, kind: str) -> ConnectionQuerySet:
        return self.filter(kind=kind)


class Connection(OwnedModel):
    kind = models.CharField(
        _("kind"), max_length=20, choices=[(kind, kind) for kind in CONNECTED_KINDS]
    )
    plugin = models.CharField(_("plugin"), max_length=60)
    label = models.CharField(
        _("label"),
        max_length=100,
        help_text=_("Your name for it: “Telegram”, “Paperless at home”."),
    )
    config = models.JSONField(_("configuration"), default=dict, blank=True)
    secrets_encrypted = models.TextField(_("secrets"), blank=True, editable=False)
    enabled = models.BooleanField(_("enabled"), default=True)

    last_ok_at = models.DateTimeField(_("last worked"), null=True, blank=True)
    last_error = models.TextField(_("last error"), blank=True)

    objects = ConnectionQuerySet.as_manager()

    class Meta:
        verbose_name = _("connection")
        verbose_name_plural = _("connections")
        ordering = ("kind", "plugin", "label")
        indexes = [models.Index(fields=("owner", "kind"))]

    def __str__(self) -> str:
        return self.label or self.plugin

    # ------------------------------------------------------------------ secrets

    @property
    def secrets(self) -> dict:
        # Cached against the ciphertext, so a refresh_from_db() or a save from elsewhere
        # is noticed rather than served a stale decryption.
        cached = getattr(self, "_secrets_cache", None)
        if cached is None or cached[0] != self.secrets_encrypted:
            cached = (self.secrets_encrypted, secrets.decrypt(self.secrets_encrypted))
            self._secrets_cache = cached
        return dict(cached[1])

    @secrets.setter
    def secrets(self, values: dict) -> None:
        self.secrets_encrypted = secrets.encrypt(dict(values))
        self._secrets_cache = (self.secrets_encrypted, dict(values))

    @property
    def full_config(self) -> dict:
        """Everything the plugin gets handed: configuration and secrets together."""
        return {**self.config, **self.secrets}

    # ------------------------------------------------------------------- plugin

    @property
    def plugin_instance(self):
        from .registry import find_plugin

        return find_plugin(self.kind, self.plugin)

    @property
    def is_installed(self) -> bool:
        return self.plugin_instance is not None

    def record_test(self, ok: bool, message: str = "") -> None:
        from django.utils import timezone

        if ok:
            self.last_ok_at = timezone.now()
            self.last_error = ""
        else:
            self.last_error = message or str(_("Failed without saying why."))
        self.save(update_fields=["last_ok_at", "last_error", "updated_at"])
