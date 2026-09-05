"""Model foundations shared by every feature.

The important piece here is :class:`OwnedModel`. Postulo is multi-user, and the one
bug class that would be genuinely serious is showing one person another person's job
search. Rather than trusting each view to remember a filter, every user-owned model
inherits an owner and a queryset that knows how to scope itself.
"""

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


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
    default_language = models.CharField(_("default language"), max_length=10, blank=True)
    default_time_zone = models.CharField(_("default time zone"), max_length=64, blank=True)

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
