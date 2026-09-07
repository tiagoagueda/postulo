"""A connection: where another service is, and how to authenticate to it, for one person.

Three kinds of plugin need one — notifiers, stores, syncs — and they share the shape:
non-secret configuration as JSON, secrets encrypted, an enabled switch, and the outcome of
the last test. A plugin describes its own fields; Postulo draws the form and keeps the
answers. One person may hold several connections to the same plugin.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

from . import secrets
from .base import CONNECTED_KINDS


class PluginRepository(models.Model):
    """A catalogue an administrator may install plugins from, as a row rather than a string.

    Catalogues already worked: a signed index, an Ed25519 key, a checksum per wheel, and
    several of them supported. What did not exist was any way to manage one. They came from
    ``POSTULO_PLUGIN_CATALOGUES`` — ``name|url|key``, comma-separated — so adding a
    catalogue meant editing a file and restarting the container.

    The environment still wins, and this is not an instance-wide policy row like
    ``SiteSettings``: there may be several, and each is switchable on its own.

    **There is no row for the internal tier.** The plugins that ship inside Postulo are not
    fetched from anywhere; they arrived the way the rest of the application did. The page
    shows them as a repository so the list reads as one thing, and it is synthesised at
    render time rather than stored — a row would be a fact about a place, and there is no
    place.
    """

    class Tier(models.TextChoices):
        #: Exactly one, shipped disabled and empty until this project publishes a catalogue
        #: and takes on the signing key that implies.
        OFFICIAL = "official", _("Official")
        CUSTOM = "custom", _("Custom")

    #: The key an installed plugin's ``origin`` records as ``catalogue:<name>``, so it must
    #: be stable: renaming one orphans the provenance of everything installed from it.
    name = models.SlugField(_("name"), max_length=50, unique=True)
    tier = models.CharField(_("tier"), max_length=10, choices=Tier, default=Tier.CUSTOM)
    url = models.URLField(_("index address"), max_length=500, blank=True)
    public_key = models.CharField(
        _("public key"),
        max_length=200,
        blank=True,
        help_text=_(
            "Ed25519, base64. Without it there is no catalogue: an unsigned list of "
            "addresses to run code from is not something Postulo will offer."
        ),
    )
    enabled = models.BooleanField(_("enabled"), default=True)
    added_at = models.DateTimeField(_("added"), auto_now_add=True)

    class Meta:
        verbose_name = _("plugin repository")
        verbose_name_plural = _("plugin repositories")
        ordering = ("tier", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("tier",),
                condition=models.Q(tier="official"),
                name="one_official_plugin_repository",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_official(self) -> bool:
        return self.tier == self.Tier.OFFICIAL

    @property
    def usable(self) -> bool:
        """Enabled, and actually pointing somewhere it can prove."""
        return bool(self.enabled and self.url and self.public_key)


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
    #: For syncs: when the plugin last ran, whatever the outcome, and what it reported.
    synced_at = models.DateTimeField(_("last run"), null=True, blank=True)
    last_summary = models.TextField(_("last run's report"), blank=True)

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


class SyncLink(OwnedModel):
    """One local record's twin on the other side of a sync connection.

    The remote address, the identifier the remote knows the record by, the version tag it
    last gave, and a hash of what was last pushed — kept here, beside the record, never
    on it. A contact stays a contact; that it also lives in an address book is this
    connection's business. When the other side deletes the twin, the link is kept with
    ``remote_gone`` set rather than the local record deleted: a swipe on a phone must not
    erase an interview.
    """

    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="links", verbose_name=_("connection")
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    remote_href = models.CharField(_("remote address"), max_length=500, blank=True)
    uid = models.CharField(_("identifier"), max_length=200, blank=True)
    etag = models.CharField(_("version tag"), max_length=200, blank=True)
    local_hash = models.CharField(_("last pushed"), max_length=64, blank=True)
    last_synced_at = models.DateTimeField(_("last synced"), null=True, blank=True)
    remote_gone = models.BooleanField(_("removed on the other side"), default=False)

    class Meta:
        verbose_name = _("sync link")
        verbose_name_plural = _("sync links")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "content_type", "object_id"), name="synclink_one_per_record"
            ),
        ]
        indexes = [models.Index(fields=("connection", "remote_href"))]

    def __str__(self) -> str:
        return f"{self.content_type.model} {self.object_id} ↔ {self.remote_href or self.uid}"

    @classmethod
    def for_record(cls, connection: Connection, record):
        """The link of ``record`` on ``connection``, or ``None``."""
        return cls.objects.filter(
            connection=connection,
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.pk,
        ).first()

    @classmethod
    def of_model(cls, connection: Connection, model):
        return cls.objects.filter(
            connection=connection, content_type=ContentType.objects.get_for_model(model)
        )

    @classmethod
    def bind(cls, connection: Connection, record, **values) -> SyncLink:
        link, _created = cls.objects.update_or_create(
            connection=connection,
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.pk,
            defaults={"owner": connection.owner, **values},
        )
        return link
