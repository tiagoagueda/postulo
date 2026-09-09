"""Model foundations shared by every feature.

The important piece here is :class:`OwnedModel`. Postulo is multi-user, and the one
bug class that would be genuinely serious is showing one person another person's job
search. Rather than trusting each view to remember a filter, every user-owned model
inherits an owner and a queryset that knows how to scope itself.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from . import phones


class TimeStampedModel(models.Model):
    """Records when a row was created and last changed."""

    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class OwnedQuerySet(models.QuerySet):
    """A queryset that can restrict itself to a single person's data."""

    def for_user(self, user) -> "OwnedQuerySet":
        """Return only the rows belonging to ``user``.

        An anonymous or missing user gets nothing rather than everything, so that a
        forgotten login check fails closed.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)


class OwnedModel(TimeStampedModel):
    """Base class for anything that belongs to one person.

    Subclasses get an ``owner``, timestamps, and a manager offering ``for_user()``.
    Every view that exposes a subclass is expected to use it; the test suite asserts
    that objects never cross between accounts.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        verbose_name=_("owner"),
    )

    objects = OwnedQuerySet.as_manager()

    class Meta:
        abstract = True


class Tag(OwnedModel):
    """A label the applicant invents for themselves.

    Deliberately free-form: everyone organises a job search differently, and a fixed
    vocabulary would fit nobody. Slugs are unique per owner, so two people may both
    have a "remote" tag without colliding.
    """

    name = models.CharField(_("name"), max_length=60)
    slug = models.SlugField(_("slug"), max_length=60)
    colour = models.CharField(
        _("colour"),
        max_length=20,
        blank=True,
        help_text=_("A hint for the interface, such as “amber” or “sky”."),
    )

    class Meta:
        verbose_name = _("tag")
        verbose_name_plural = _("tags")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("owner", "slug"), name="unique_tag_slug_per_owner")
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:60]
        super().save(*args, **kwargs)


class PhoneNumber(OwnedModel):
    """One telephone number belonging to a person or to a contact.

    A generic relation, for the reason ``CVItem`` gives: the holder is heterogeneous — the
    account holder today, a contact at a company today, whatever #92 decides tomorrow —
    and two nullable foreign keys with a check constraint would say the same thing less
    clearly while needing a migration every time a third kind of holder appears.

    **The primary is a property of the holder, not of the account.** One number per holder
    carries ``is_primary``, enforced by a partial unique index rather than by whichever
    form last saved. That is what makes "switched off shows the primary" a question with
    exactly one answer, in the database, at every point in the code that asks it.

    **Uniqueness reaches across the whole instance**, which is a deliberate choice by the
    maintainer and a disclosure worth naming: refusing a number because somebody already
    holds it tells whoever typed it that *some other account on this server has it*. There
    is no way to enforce the rule without saying so, which is why the message says it in
    those words instead of pretending the collision was something else.

    Only a number that reached international form takes part. A number nobody could parse
    is kept exactly as typed — ``phones.py`` means that — and has no comparable form, so
    it sits outside the constraint rather than colliding with the first number that shares
    its digits.
    """

    class Kind(models.TextChoices):
        MOBILE = "mobile", _("Mobile")
        WORK = "work", _("Work")
        HOME = "home", _("Home")
        SWITCHBOARD = "switchboard", _("Switchboard")
        FAX = "fax", _("Fax")
        OTHER = "other", _("Other")

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    holder = GenericForeignKey("content_type", "object_id")

    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices, blank=True)
    label = models.CharField(
        _("name, if Other"),
        max_length=60,
        blank=True,
        help_text=_("Your name for it, when none of the kinds above fits."),
    )
    number = models.CharField(_("phone"), max_length=40)
    #: The comparable form, or empty where there is none. Written by ``save``, never by a
    #: form: a value the constraint depends on cannot be somebody's to type.
    normalised = models.CharField(max_length=40, blank=True, db_index=True)
    is_primary = models.BooleanField(_("primary"), default=False)

    class Meta:
        verbose_name = _("telephone number")
        verbose_name_plural = _("telephone numbers")
        ordering = ("-is_primary", "created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "object_id"),
                condition=Q(is_primary=True),
                name="one_primary_phone_number_per_holder",
            ),
            models.UniqueConstraint(
                fields=("normalised",),
                condition=~Q(normalised=""),
                name="phone_number_unique_across_the_instance",
            ),
        ]
        indexes = [models.Index(fields=("content_type", "object_id"))]

    def __str__(self) -> str:
        return self.number

    def save(self, *args, **kwargs):
        self.number = (self.number or "").strip()
        self.normalised = phones.normalise(self.number)
        if self.kind != self.Kind.OTHER:
            self.label = ""
        return super().save(*args, **kwargs)

    @property
    def kind_label(self) -> str:
        """What to call this number, in words, without inventing a fact.

        A number carried over from the single field nobody was ever asked about has no
        kind, and gets none: an empty string, so the interface shows the number alone
        rather than labelling it a mobile because that is the commonest answer.
        """
        if self.kind == self.Kind.OTHER:
            return self.label
        return str(self.Kind(self.kind).label) if self.kind else ""


class MailSecurity(models.TextChoices):
    """How TLS gets onto an SMTP session. Two ways, and they are not interchangeable.

    STARTTLS connects in the clear and asks the server to upgrade the socket; implicit TLS
    hands over a certificate before a byte of SMTP is spoken. Point one at the other's port
    and nothing happens until the timeout, because each is waiting for the other to speak.
    """

    NONE = "none", _("None")
    STARTTLS = "starttls", _("STARTTLS, after connecting")
    SSL = "ssl", _("TLS from the first byte")


#: The port each kind of connection is normally offered on. A suggestion, filled in when
#: nobody typed one -- never a correction of a port somebody did type, because a relay on a
#: port of its own is an ordinary thing for a self-hosted instance to have.
DEFAULT_MAIL_PORTS = {
    MailSecurity.NONE: 25,
    MailSecurity.STARTTLS: 587,
    MailSecurity.SSL: 465,
}


class SiteSettings(models.Model):
    """Instance policy an administrator may change from the interface. One row.

    Infrastructure — secrets, the database, hosts, TLS — stays in the environment: it is
    needed before the application can start, or it is a secret. Policy lives here. An
    environment variable, when set, still wins (see :mod:`postulo.core.site`), so an
    existing deployment keeps behaving exactly as it did. A ``None`` means "never set from
    the interface", and the code's default applies.
    """

    instance_name = models.CharField(
        _("instance name"),
        max_length=60,
        default="Postulo",
        help_text=_("Shown in the header and on the sign-in page."),
    )
    tagline = models.CharField(
        _("tagline"),
        max_length=200,
        blank=True,
        help_text=_("A line under the name on the sign-in page. Optional."),
    )
    registration_open = models.BooleanField(
        _("registration open"),
        null=True,
        blank=True,
        help_text=_("Whether anyone who finds the address may create an account."),
    )
    capture_ignore_robots = models.BooleanField(
        _("ignore robots.txt when capturing"),
        null=True,
        blank=True,
    )
    sso_is_second_factor = models.BooleanField(
        _("single sign-on counts as the second factor"),
        null=True,
        blank=True,
        help_text=_(
            "Whether somebody who arrived through the identity provider is asked for a "
            "code as well."
        ),
    )
    default_language = models.CharField(_("default language"), max_length=10, blank=True)
    #: Which languages this instance offers, as a list of codes. **Empty means all of
    #: them**, and that is not the same as a list naming every one: an instance whose
    #: operator has never opened this setting keeps offering everything, including a
    #: language added in a later release, while a list frozen on the day somebody first
    #: saved the form would silently exclude it for ever.
    #:
    #: This is a curation, never an entitlement. Nothing here is checked per account, and
    #: an administrator is not exempt from it — what the instance offers is what the
    #: instance offers, and two rules where one will do is how the two drift apart.
    offered_languages = models.JSONField(_("languages offered"), default=list, blank=True)
    default_time_zone = models.CharField(_("default time zone"), max_length=64, blank=True)

    # --- how this instance sends mail --------------------------------------------------
    #
    # Infrastructure that used to be environment-only, and is here because an operator
    # should not have to edit a file and restart a container to change an SMTP host. The
    # environment still wins where it speaks; see `postulo.core.site`. Blank and NULL both
    # mean "not set from the interface", so an instance that has never opened this page
    # behaves exactly as it did.
    email_host = models.CharField(_("SMTP server"), max_length=255, blank=True)
    email_port = models.PositiveIntegerField(
        _("port"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    email_username = models.CharField(_("username"), max_length=255, blank=True)
    #: Fernet, under the same key as a plugin connection's secrets. Never a plain column:
    #: a readable password in the policy row would be a new kind of secret in a codebase
    #: that has deliberately avoided having one. Read through `email_password`.
    email_password_encrypted = models.TextField(_("password"), blank=True)
    #: How the connection is encrypted, as one choice rather than two booleans. Django's
    #: SMTP backend raises on `use_tls` and `use_ssl` together, and rightly -- they are
    #: alternatives, not layers -- so a checkbox each would offer a pair that cannot be
    #: saved. Blank keeps its meaning from every other column here: not set from the
    #: interface, so the environment answers (#158).
    email_security = models.CharField(
        _("connection security"),
        max_length=10,
        blank=True,
        choices=MailSecurity,
    )
    email_timeout = models.PositiveIntegerField(
        _("timeout in seconds"), null=True, blank=True, validators=[MaxValueValidator(300)]
    )
    email_from = models.EmailField(_("from address"), blank=True)

    #: Which transport plugin carries the mail. Blank means the built-in SMTP one, so an
    #: instance that has never heard of transports keeps behaving exactly as it did.
    email_transport = models.CharField(_("mail transport"), max_length=60, blank=True)
    #: A non-SMTP transport's own settings, drawn from the fields it declares. SMTP keeps
    #: the named columns above, because those existed first and the environment overrides
    #: them one by one; a transport nobody has written yet gets the generic pair, the same
    #: shape a `Connection` uses.
    transport_config = models.JSONField(_("transport configuration"), default=dict, blank=True)
    transport_secrets_encrypted = models.TextField(
        _("transport secrets"), blank=True, editable=False
    )

    #: What the mail transport last actually did. Recorded rather than probed, because the
    #: question "can this instance still send mail?" is asked while rendering a page and an
    #: answer that opens a connection would make reading a page send traffic (#152).
    mail_last_ok_at = models.DateTimeField(_("mail last worked"), null=True, blank=True)
    mail_last_error_at = models.DateTimeField(_("mail last failed"), null=True, blank=True)
    mail_last_error = models.TextField(_("mail's last error"), blank=True)
    #: Failures since the last success. One refused address is not a broken relay, so the
    #: route stops counting only after several in a row with nothing succeeding between.
    mail_failures = models.PositiveIntegerField(_("mail failures in a row"), default=0)

    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("updated by"),
    )

    class Meta:
        verbose_name = _("site settings")
        verbose_name_plural = _("site settings")

    def __str__(self) -> str:
        return self.instance_name

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> "SiteSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row

    #: Failures in a row before mail stops counting as a way back into an account. Three
    #: rather than one: a relay that refuses one address at RCPT TO has told us about that
    #: address, not about itself, and a lock that opens on a typo is worse than one that
    #: stays shut an evening longer.
    MAIL_FAILURES_BEFORE_BROKEN = 3

    #: How stale a success may be before it is written again. A send is common; a row
    #: update per send is not worth it, and the timestamp is read to the hour anyway.
    MAIL_OK_INTERVAL = timedelta(hours=1)

    def record_mail(self, ok: bool, message: str = "") -> None:
        """Remember how the last send went. Never raises; a send must not fail over this."""
        from django.utils import timezone

        now = timezone.now()
        if not ok:
            self.mail_last_error_at = now
            self.mail_last_error = (message or str(_("Failed without saying why.")))[:500]
            self.mail_failures = (self.mail_failures or 0) + 1
            fields = ["mail_last_error_at", "mail_last_error", "mail_failures"]
        else:
            fresh = self.mail_last_ok_at and now - self.mail_last_ok_at < self.MAIL_OK_INTERVAL
            if fresh and not self.mail_failures:
                return
            self.mail_last_ok_at = now
            self.mail_last_error = ""
            self.mail_failures = 0
            fields = ["mail_last_ok_at", "mail_last_error", "mail_failures"]
        self.save(update_fields=[*fields, "updated_at"])

    @property
    def mail_is_delivering(self) -> bool:
        """Whether mail counts as a way back into an account.

        Unknown counts as working, and so does a run of failures shorter than
        `MAIL_FAILURES_BEFORE_BROKEN`. Both fail in the direction that keeps the interlock
        shut: this is a check that makes the lock honest, never one that is eager to open it.
        """
        return (self.mail_failures or 0) < self.MAIL_FAILURES_BEFORE_BROKEN

    @property
    def email_password(self) -> str:
        """The stored SMTP password, or empty. Raises if the key has changed under it."""
        from postulo.plugins import secrets

        return secrets.decrypt(self.email_password_encrypted).get("password", "")

    @email_password.setter
    def email_password(self, raw: str) -> None:
        from postulo.plugins import secrets

        self.email_password_encrypted = secrets.encrypt({"password": raw} if raw else {})

    @property
    def transport_secrets(self) -> dict:
        """The selected transport's secret settings, decrypted."""
        from postulo.plugins import secrets

        return secrets.decrypt(self.transport_secrets_encrypted)

    @transport_secrets.setter
    def transport_secrets(self, values: dict) -> None:
        from postulo.plugins import secrets

        self.transport_secrets_encrypted = secrets.encrypt(values or {})

    @property
    def has_email_password(self) -> bool:
        """Whether one is stored. The page may say this much and nothing more: a masked
        field of the right length gives away the length."""
        return bool(self.email_password_encrypted)
