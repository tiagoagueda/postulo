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

# MailSecurity is not a model -- it is how TLS gets onto an SMTP session, and it lives
# beside the code that opens one (#149). Imported here because a field's choices need it.
from .mail import MailSecurity


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
    #: When somebody proved they hold this number, or NULL for never. NULL is the only
    #: state any existing row may have: numbers have been storable and first-come-first-
    #: served since #90, so every one recorded before this existed is a claim nobody
    #: checked. A migration granting them verification would pre-position whoever typed a
    #: stranger's number a month ago, which is why this starts empty and is earned (#142).
    verified_at = models.DateTimeField(_("verified"), null=True, blank=True)
    #: Whether this is the number that gets its owner back into their account.
    #:
    #: **Deliberately not `is_primary`.** The primary is what a document prints — it is on
    #: somebody's CV, and a recruiter dials it. Making the same row the way back into the
    #: account couples "the number I publish" to "the number that proves I am me", so
    #: changing the number on a CV would silently change a recovery route. A separate flag
    #: removes the problem rather than warning about it (#144).
    #:
    #: Only ever true on a number held by the owner's own profile, and only on one that was
    #: verified: a claim nobody checked cannot be a way in. `clean()` refuses both.
    is_recovery = models.BooleanField(_("way back into the account"), default=False)

    #: How long a proof lasts before it is worth asking again. An address is forever in a
    #: way a number is not: people give up numbers and carriers reissue them, so a proof
    #: from three years ago describes somebody who may no longer be reachable there. A
    #: year is long enough not to nag and short enough that a reissued number stops being
    #: a way into an account within one.
    VERIFICATION_LASTS = timedelta(days=365)

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
            # One way back in per account, in the database rather than in whichever form
            # saved last -- the same argument the primary constraint makes, for a flag with
            # more riding on it.
            models.UniqueConstraint(
                fields=("owner",),
                condition=Q(is_recovery=True),
                name="one_recovery_number_per_account",
            ),
        ]
        indexes = [models.Index(fields=("content_type", "object_id"))]

    def __str__(self) -> str:
        return self.number

    def save(self, *args, **kwargs):
        previous = self.normalised
        self.number = (self.number or "").strip()
        self.normalised = phones.normalise(self.number)
        if self.kind != self.Kind.OTHER:
            self.label = ""
        # Editing the digits makes this a different number, and a proof of the old one says
        # nothing about the new. Dropped here rather than in a form, because a row saved by
        # the API, a management command or a shell would otherwise keep a verification that
        # was never about the number now stored (#142).
        if self.pk and previous and previous != self.normalised:
            self.verified_at = None
            # And it stops being a way back in, for the same reason and in the same breath:
            # a route to a number nobody has answered on is not a route (#144).
            self.is_recovery = False
            if "update_fields" in kwargs and kwargs["update_fields"] is not None:
                kwargs["update_fields"] = {
                    *kwargs["update_fields"],
                    "verified_at",
                    "is_recovery",
                }
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        """Refuse the two ways this flag could mean something it does not.

        A contact's number is one this account *recorded*, never one this account *is*, and a
        number nobody proved is a claim rather than a channel. Checked here rather than only
        in the form, because the API, a management command and a shell all reach the model.
        """
        from django.core.exceptions import ValidationError

        super().clean()
        if not self.is_recovery:
            return
        if not self._holder_is_a_profile():
            raise ValidationError(
                {
                    "is_recovery": _(
                        "Only one of your own numbers can get you back into your account. "
                        "This one belongs to somebody you deal with."
                    )
                }
            )
        if not self.is_verified:
            raise ValidationError(
                {
                    "is_recovery": _(
                        "A number has to be confirmed before it can get you back in. "
                        "Nobody has answered on this one."
                    )
                }
            )

    def _holder_is_a_profile(self) -> bool:
        from django.contrib.contenttypes.models import ContentType

        from postulo.accounts.models import Profile

        return self.content_type_id == ContentType.objects.get_for_model(Profile).pk

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

    # ------------------------------------------------------- whether it was proved

    @property
    def is_verified(self) -> bool:
        """Proved, and recently enough to still mean something.

        The freshness half is not decoration. A number a carrier has reissued is somebody
        else's handset answering to a proof this instance recorded, and that is the failure
        mode an email address does not have.
        """
        from django.utils import timezone

        if self.verified_at is None:
            return False
        return timezone.now() - self.verified_at <= self.VERIFICATION_LASTS

    @property
    def verification_has_lapsed(self) -> bool:
        """Proved once, too long ago. A different state from never, and worth saying so."""
        return self.verified_at is not None and not self.is_verified

    def record_verified(self) -> None:
        """Somebody answered on this number, and this is the only thing that may say so.

        Nothing calls it yet, and that is the order the work has rather than an omission:
        proving a number means sending a code to it, which needs a channel that can reach
        one (#143). No channel, no verification; no verification, no way back in.
        """
        from django.utils import timezone

        self.verified_at = timezone.now()
        self.save(update_fields=["verified_at", "updated_at"])

    def forget_verification(self) -> None:
        """Unprove it, because the number itself changed and that is a new claim."""
        if self.verified_at is None and not self.is_recovery:
            return
        self.verified_at = None
        self.is_recovery = False
        self.save(update_fields=["verified_at", "is_recovery", "updated_at"])


class PostalAddress(OwnedModel):
    """One postal address belonging to a person or to a contact.

    A generic relation, for the reason :class:`PhoneNumber` gives: the holder is
    heterogeneous, and two nullable foreign keys with a check constraint would say the same
    thing less clearly while needing a migration every time a third kind of holder appears.

    **Not unique across the instance, and that is the point rather than an omission.** A
    telephone number belongs to one person; a postal address does not. Spouses share one,
    flatmates share one, an adult child living at home shares one, and two siblings on a
    family instance share one -- and a family instance is exactly the kind of small
    self-hosted deployment this project is built for. A uniqueness constraint would refuse
    the second member of a household their own address *and* disclose, in refusing it, that
    somebody else on this server lives there. So: unique **per owner**, so one person cannot
    list the same address twice, and freely shared between accounts (#92).

    **Valid cannot mean verified.** Deciding whether an address exists needs a per-country
    reference database or a paid lookup service -- a network dependency, a cost, and a
    stream of updates. `phones.py` refuses the equivalent for telephone numbers and says
    why: Postulo has no use for the answer, because it is not going to dial anything. Nor is
    it going to post anything. Valid here means well-formed enough to be used, and an
    address somebody types oddly is saved exactly as typed.

    **The parts, not the format.** These five are what every format agrees on; where the
    postcode goes and whether a region is named at all differ by country, so the parts are
    stored and the rendering decides the order (#147).

    **The primary is not for the CV header.** Guidance across most of Europe is that a CV
    carries a city and a country and not a street -- partly because the street is irrelevant
    and partly because a precise address invites a reader to draw conclusions about somebody
    from where they live. `Profile.location` stays its own overridable line. Anything else
    would quietly put people's home addresses on documents they send to strangers.
    """

    class Kind(models.TextChoices):
        HOME = "home", _("Home")
        WORK = "work", _("Work")
        POSTAL = "postal", _("Postal")
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
    #: The street and whatever goes with it -- a number, a floor, a door, a second line.
    #: One field rather than three, because how many lines a street address takes is one of
    #: the things that differs by country, and splitting it here would be deciding that.
    street = models.TextField(_("address"), blank=True)
    postcode = models.CharField(_("postcode"), max_length=20, blank=True)
    municipality = models.CharField(_("town or city"), max_length=120, blank=True)
    #: A state, a province, a district, a prefecture. What it is *called* depends on the
    #: address's country rather than on the reader's language, which is #147's problem.
    region = models.CharField(_("region"), max_length=120, blank=True)
    country = models.CharField(_("country"), max_length=2, blank=True)
    is_primary = models.BooleanField(_("primary"), default=False)

    #: What the uniqueness constraint compares: the parts, folded, joined. Written by
    #: `save`, never by a form -- a value a constraint depends on cannot be somebody's to
    #: type. Case and spacing vary in what people type and mean nothing here.
    comparable = models.CharField(max_length=300, blank=True, db_index=True)

    class Meta:
        verbose_name = _("postal address")
        verbose_name_plural = _("postal addresses")
        ordering = ("-is_primary", "created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "object_id"),
                condition=Q(is_primary=True),
                name="one_primary_address_per_holder",
            ),
            # Per owner, never across the instance. Somebody listing the same address twice
            # is a mistake worth catching; two people sharing one is a household.
            models.UniqueConstraint(
                fields=("owner", "comparable"),
                condition=~Q(comparable=""),
                name="address_unique_per_owner",
            ),
        ]
        indexes = [models.Index(fields=("content_type", "object_id"))]

    def __str__(self) -> str:
        return self.one_line() or str(_("(empty address)"))

    def save(self, *args, **kwargs):
        for field in ("street", "postcode", "municipality", "region"):
            setattr(self, field, (getattr(self, field) or "").strip())
        self.country = (self.country or "").strip().upper()
        if self.kind != self.Kind.OTHER:
            self.label = ""
        self.comparable = self.comparable_form()
        return super().save(*args, **kwargs)

    def comparable_form(self) -> str:
        """The parts folded to something two typings of one address agree on.

        Case and interior spacing, and nothing cleverer: an address written *Rua do Exemplo
        1* and *rua do exemplo  1* is the same address, and one written *Rua do Exemplo 1A*
        is not. Guessing past that needs the reference database this deliberately does not
        have.
        """
        parts = (self.street, self.postcode, self.municipality, self.region, self.country)
        folded = " ".join(" ".join(str(part or "").split()) for part in parts)
        return folded.casefold().strip()[:300]

    @property
    def is_empty(self) -> bool:
        return not self.comparable_form()

    def one_line(self, separator: str = ", ") -> str:
        """The parts in the order they were entered, for a list or a log line.

        Not a rendering: printing an address correctly is per country and belongs to #147.
        This is what to show where an address needs to be recognised rather than posted.
        """
        from postulo.core import phones

        parts = [
            self.street,
            self.postcode,
            self.municipality,
            self.region,
            phones.country_name(self.country),
        ]
        return separator.join(part for part in (p.strip() for p in parts if p) if part)


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
    #: Whether somebody may sign in with a code sent to their primary address (#153).
    #:
    #: Nullable and off unless an administrator says otherwise, because turning it on makes
    #: the mailbox load-bearing in a new way: today email resets a password, and with this it
    #: signs somebody in. That was already nearly true — anybody holding the mailbox could
    #: complete a reset — but it is worth an operator deciding rather than inheriting.
    #:
    #: It is **not** a second factor and there is no setting to make it one, which is where
    #: this deliberately parts company with `sso_is_second_factor`. That setting exists
    #: because an identity provider may itself have checked identity carefully, and Postulo
    #: cannot see how; a code out of an inbox has no such provider behind it. Somebody with an
    #: authenticator is still asked for it.
    email_sign_in = models.BooleanField(
        _("sign in with a code sent by email"),
        null=True,
        blank=True,
        help_text=_("Offered only while this instance's mail is actually getting through."),
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

    #: The transport that carries text messages, and its own settings. A separate trio from
    #: the mail one rather than a shared blob, because both are selected at once and a single
    #: column could hold only one of their configurations (#143). Blank means none, which is
    #: what every instance has: Postulo ships a mail transport and no text gateway, and names
    #: no vendor for either.
    text_transport = models.CharField(_("text transport"), max_length=60, blank=True)
    text_config = models.JSONField(_("text configuration"), default=dict, blank=True)
    text_secrets_encrypted = models.TextField(_("text secrets"), blank=True, editable=False)

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
    def text_secrets(self) -> dict:
        """The text transport's secret settings, decrypted."""
        from postulo.plugins import secrets

        return secrets.decrypt(self.text_secrets_encrypted)

    @text_secrets.setter
    def text_secrets(self, values: dict) -> None:
        from postulo.plugins import secrets

        self.text_secrets_encrypted = secrets.encrypt(values or {})

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
