"""A connection: where another service is, and how to authenticate to it, for one person.

Three kinds of plugin need one — notifiers, stores, syncs — and they share the shape:
non-secret configuration as JSON, secrets encrypted, an enabled switch, and the outcome of
the last test. A plugin describes its own fields; Postulo draws the form and keeps the
answers. One person may hold several connections to the same plugin.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

from . import secrets
from .base import CONNECTED_KINDS


class CatalogueSerial(models.Model):
    """The highest ``serial`` seen from a catalogue, kept where a cache cull cannot take it (#972).

    A signed index says what its publisher signed, not when, so an old one can be served
    again by whoever controls the hosting. The counter that makes that visible has to outlive
    the cache, which is culled when full and emptied by anything that clears it: it is one row
    per catalogue *name* rather than a column of `PluginRepository`, because a catalogue
    configured from the environment has no row of that kind. ``key`` is the public key it was
    seen under: a new key is a catalogue not yet seen.
    """

    name = models.CharField(max_length=100, unique=True)
    key = models.CharField(max_length=200)
    serial = models.PositiveBigIntegerField()

    def __str__(self) -> str:
        return f"{self.name}: {self.serial}"


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


class PluginPolicy(models.Model):
    """What an administrator has decided about a plugin — for everybody, or for one person.

    Four states, and the difference between two of them is worth keeping.
    *Unavailable* means the plugin is not part of your Postulo and you do not see it.
    *Forced off* means you can see that it exists and that somebody switched it off for
    you. The second is more honest and the first is quieter, and which is right depends on
    why.

    A row with no ``person`` is the instance default. A row with one is an exception for
    that account. **No row at all means available**, which is why absence is the common
    case and nothing has to be written when the default changes.

    ``decided_by`` and ``decided_at`` are here because #95 asks that this be written down
    and *Server settings* has no audit trail of any kind — no action on any of those pages
    records anything today. Keeping who and when on the row itself is not a general trail;
    it is the smallest honest version of one for the decision that most needs it.
    """

    class State(models.TextChoices):
        AVAILABLE = "available", _("Available — the person chooses")
        UNAVAILABLE = "unavailable", _("Unavailable — not offered at all")
        FORCED_ON = "on", _("On — and they may not switch it off")
        FORCED_OFF = "off", _("Off — and they may not switch it on")

    plugin = models.CharField(_("plugin"), max_length=60)
    person = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="plugin_policies",
        verbose_name=_("person"),
        help_text=_("Empty for the instance default; an account for an exception to it."),
    )
    state = models.CharField(_("state"), max_length=12, choices=State, default=State.AVAILABLE)
    decided_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided"), auto_now=True)

    class Meta:
        verbose_name = _("plugin policy")
        verbose_name_plural = _("plugin policies")
        ordering = ("plugin", "person_id")
        constraints = [
            models.UniqueConstraint(
                fields=("plugin", "person"),
                name="one_plugin_policy_per_person",
            ),
            models.UniqueConstraint(
                fields=("plugin",),
                condition=models.Q(person__isnull=True),
                name="one_default_policy_per_plugin",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plugin}: {self.state}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._forget()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self._forget()
        return result

    @staticmethod
    def _forget() -> None:
        """Throw away the memoised decisions (#231).

        `policy.decide` is memoised for the request, keyed on the plugins record's stamp --
        which moves when a plugin is installed or switched off, and does not move when a row
        here changes. So writing one of these rows is the other thing that makes an answer
        wrong, and it says so itself rather than leaving every caller to remember. The memo
        lives in `core.memo`, below this module, since `policy` reads these rows (#248).
        """
        from postulo.core.memo import forget_decisions

        forget_decisions()


class PluginAccess(models.Model):
    """Who may use a plugin or module, switch it for themselves, and change its settings (#886).

    A rule for one account or one group, kept by administrators only. It replaces the single
    checkbox *Each person may change it*: *Everyone* is still the instance's row in
    :class:`PluginPolicy` (available, or forced on), and these are the exceptions to it, an
    account's over its groups' over the default.

    ``may_use`` false takes the plugin away from them as *unavailable* does. ``may_switch``
    lets them turn an installed plugin off and on for themselves, which for what ships inside
    Postulo is never theirs (#200). ``may_change_settings`` is the right to use the plugin's
    settings page, for an administrator who is not the operator.

    **Groups are Django's own** (``auth.Group``), already in the schema and the admin.
    Several groups that apply to one account are read together and the most permissive wins:
    a person put in a group to be let in is not locked out by another that says nothing.
    """

    plugin = models.CharField(_("plugin"), max_length=60)
    person = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="plugin_access",
        verbose_name=_("person"),
    )
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="plugin_access",
        verbose_name=_("group"),
    )
    may_use = models.BooleanField(_("may use it"), default=True)
    may_switch = models.BooleanField(_("may switch it for themselves"), default=False)
    may_change_settings = models.BooleanField(_("may change its settings"), default=False)

    class Meta:
        verbose_name = _("plugin access rule")
        verbose_name_plural = _("plugin access rules")
        ordering = ("plugin", "group_id", "person_id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(person__isnull=False, group__isnull=True)
                | models.Q(person__isnull=True, group__isnull=False),
                name="plugin_access_has_one_subject",
            ),
            models.UniqueConstraint(
                fields=("plugin", "person"),
                condition=models.Q(person__isnull=False),
                name="one_plugin_access_per_person",
            ),
            models.UniqueConstraint(
                fields=("plugin", "group"),
                condition=models.Q(group__isnull=False),
                name="one_plugin_access_per_group",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plugin}: {self.person or self.group}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        PluginPolicy._forget()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        PluginPolicy._forget()
        return result


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
        _("type"), max_length=20, choices=[(kind, kind) for kind in CONNECTED_KINDS]
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
    #: The counters and notes of that run, as numbers (``SyncReport.record``): the sentence is
    #: worded at display time, so it follows the reader's language rather than the run's (#384).
    last_report = models.JSONField(_("last run's report"), default=dict, blank=True)

    objects = ConnectionQuerySet.as_manager()

    @property
    def last_summary(self) -> str:
        """The last run's report in the active language, or nothing before a first run."""
        from .base import describe_sync

        return describe_sync(self.last_report) if self.last_report else ""

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

    def retire(self, reason: str, *, keep_secrets: bool = False) -> None:
        """Switch this connection off because the other side has ended it (#216).

        Nothing is deleted. The row, its label and its event switches stay where they are, so
        switching it back on is one press once whatever broke is fixed. The secrets go unless
        the plugin asked to keep them: a push subscription a browser withdrew, or a token a
        provider revoked, is not a credential worth keeping to try again with.
        """
        self.enabled = False
        self.last_error = reason
        fields = ["enabled", "last_error", "updated_at"]
        if not keep_secrets and self.secrets_encrypted:
            self.secrets = {}
            fields.append("secrets_encrypted")
        self.save(update_fields=fields)

    def record_test(self, ok: bool, message: str = "") -> None:
        from django.utils import timezone

        if ok:
            self.last_ok_at = timezone.now()
            self.last_error = ""
        else:
            self.last_error = message or str(_("Failed without saying why."))
        # Its own short transaction: a test waits on the connected server first (#357).
        with transaction.atomic():
            self.save(update_fields=["last_ok_at", "last_error", "updated_at"])


class AlreadyPaired(Exception):
    """A remote item already paired with another record on the same connection (#326).

    ``link`` is the pairing that holds it, so a plugin can say which record that is, or ask
    the person whether to move the item over (#327). Raised by :meth:`SyncLink.bind` in
    place of the database's bare ``IntegrityError``, which named neither side.
    """

    def __init__(self, link: SyncLink):
        self.link = link
        super().__init__(
            f"{link.collection} {link.uid} is already paired with "
            f"{link.content_type.model} {link.object_id}"
        )


class SyncLink(OwnedModel):
    """One local record's twin on the other side of a sync connection.

    The remote address, the identifier the remote knows the record by, the version tag it
    last gave, and a hash of what was last pushed — kept here, beside the record, never
    on it. A contact stays a contact; that it also lives in an address book is this
    connection's business. When the other side deletes the twin, the link is kept with
    ``remote_gone`` set rather than the local record deleted: a swipe on a phone must not
    erase an interview. Setting ``remote_gone``, and clearing it, is the plugin's job;
    :meth:`bind` clears it unless told otherwise, because a record bound again is paired
    again.

    **Where the twin lives, and which twin it is (#326).** ``collection`` is the address of
    the address book, calendar or task list holding the twin, blank where a plugin has no
    such thing. A UID is unique only within its collection (RFC 6352 §5.1, RFC 4791 §4.1),
    so a twin is keyed on its collection and its UID; ``remote_href`` is a cache of where it
    was last found, which a server may change (RFC 4791 §5.3.2). ``slot`` is a short key the
    plugin chooses, naming which of a record's twins this is — an application's "submit"
    task beside its "hear back" task, an interview's event beside its task. Blank means the
    only one, which is every row written before there were slots. It is free text, because
    one plugin's slots mean nothing to another, and not ``role``, which on a contact is
    already a job title.

    **One remote item, one record.** On one connection, a collection and a UID that are both
    set belong to one paired record at most; :meth:`bind` raises :class:`AlreadyPaired`
    rather than write one card into a contact and a department at once.

    **Undoing a pairing keeps the row (#326).** :meth:`unbind` marks it ``unpaired_at``
    instead of deleting it. A plugin that pairs by UID would otherwise pair the two again on
    its next run, or push the record as a new item and adopt the item as a new record. An
    unpaired row says "leave both sides alone": the plugin skips the record and the item
    until either is bound again, and the row no longer counts towards the rule above, so
    the item may be paired with another record.

    Pairings are not in the personal export (#326): connections are not, nor their secrets,
    and a pairing without its connection points at a server the importing instance has no
    credentials for. A connection added again pairs again by UID.
    """

    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="links", verbose_name=_("connection")
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    slot = models.CharField(_("slot"), max_length=50, blank=True)
    collection = models.CharField(_("collection"), max_length=500, blank=True)
    remote_href = models.CharField(_("remote address"), max_length=500, blank=True)
    uid = models.CharField(_("identifier"), max_length=200, blank=True)
    etag = models.CharField(_("version tag"), max_length=200, blank=True)
    local_hash = models.CharField(_("last pushed"), max_length=64, blank=True)
    last_synced_at = models.DateTimeField(_("last synced"), null=True, blank=True)
    remote_gone = models.BooleanField(_("removed on the other side"), default=False)
    unpaired_at = models.DateTimeField(_("unpaired"), null=True, blank=True)

    class Meta:
        verbose_name = _("sync link")
        verbose_name_plural = _("sync links")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "content_type", "object_id", "slot"),
                name="synclink_one_per_record_slot",
            ),
            # Rows written before #326 have a blank collection, so this does not reach them
            # until the plugin fills it in, after settling any two links holding one UID.
            models.UniqueConstraint(
                fields=("connection", "collection", "uid"),
                condition=~models.Q(collection="")
                & ~models.Q(uid="")
                & models.Q(unpaired_at__isnull=True),
                name="synclink_one_record_per_item",
            ),
        ]
        indexes = [
            models.Index(fields=("connection", "remote_href")),
            models.Index(fields=("connection", "collection", "uid")),
        ]

    def __str__(self) -> str:
        slot = f" [{self.slot}]" if self.slot else ""
        return f"{self.content_type.model} {self.object_id}{slot} ↔ {self.remote_href or self.uid}"

    @property
    def paired(self) -> bool:
        """False once :meth:`unbind` has undone the pairing and nothing has bound it again."""
        return self.unpaired_at is None

    @classmethod
    def for_record(cls, connection: Connection, record, slot: str = ""):
        """The link of ``record`` in ``slot`` on ``connection``, or ``None``.

        An unpaired link is returned too: check :attr:`paired` before syncing through it.
        """
        return cls.objects.filter(
            connection=connection,
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.pk,
            slot=slot,
        ).first()

    @classmethod
    def of_model(cls, connection: Connection, model):
        """Every link of ``model`` on ``connection``: every slot, paired or not."""
        return cls.objects.filter(
            connection=connection, content_type=ContentType.objects.get_for_model(model)
        )

    @classmethod
    def for_remote(cls, connection: Connection, collection: str, uid: str):
        """The link of the item ``uid`` in ``collection``, from the remote side, or ``None``.

        The paired link where there is one. Otherwise the one most recently unpaired, which
        tells the plugin that a person undid this pairing and the item is to be left alone.
        """
        return (
            cls.objects.filter(connection=connection, collection=collection, uid=uid)
            .order_by(models.F("unpaired_at").desc(nulls_first=True), "-pk")
            .first()
        )

    @classmethod
    def bind(cls, connection: Connection, record, slot: str = "", **values) -> SyncLink:
        """Pair ``record``, in ``slot``, with its twin: create the link or update it.

        A record bound again is paired again, so ``unpaired_at`` is cleared, and so is
        ``remote_gone`` unless the caller passes it. A remote item already paired with
        another record on this connection raises :class:`AlreadyPaired`.
        """
        values.setdefault("remote_gone", False)
        content_type = ContentType.objects.get_for_model(record)
        try:
            with transaction.atomic():
                link, _created = cls.objects.update_or_create(
                    connection=connection,
                    content_type=content_type,
                    object_id=record.pk,
                    slot=slot,
                    defaults={"owner": connection.owner, **values, "unpaired_at": None},
                )
        except IntegrityError:
            holder = cls._holder(connection, content_type, record, slot, values)
            if holder is None:
                raise
            raise AlreadyPaired(holder) from None
        return link

    @classmethod
    def _holder(cls, connection, content_type, record, slot, values):
        """The other record's link holding the item ``bind`` was asked to pair, if any."""
        current = cls.for_record(connection, record, slot)
        collection = values.get("collection", current.collection if current else "")
        uid = values.get("uid", current.uid if current else "")
        if not (collection and uid):
            return None
        return (
            cls.objects.filter(
                connection=connection, collection=collection, uid=uid, unpaired_at=None
            )
            .exclude(content_type=content_type, object_id=record.pk, slot=slot)
            .first()
        )

    @classmethod
    def unbind(cls, connection: Connection, record, slot: str = ""):
        """Undo the pairing of ``record`` in ``slot``, leaving the record and the item alone.

        The row is kept, marked unpaired, so that a plugin pairing by UID does not pair the
        two again on its next run; :meth:`bind` pairs them again. Returns the link, or
        ``None`` where there was none. Calling it twice keeps the time of the first.
        """
        from django.utils import timezone

        link = cls.for_record(connection, record, slot)
        if link is not None and link.unpaired_at is None:
            link.unpaired_at = timezone.now()
            link.save(update_fields=["unpaired_at", "updated_at"])
        return link
