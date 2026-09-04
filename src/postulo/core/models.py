"""Model foundations shared by every feature.

The important piece here is :class:`OwnedModel`. Postulo is multi-user, and the one
bug class that would be genuinely serious is showing one person another person's job
search. Rather than trusting each view to remember a filter, every user-owned model
inherits an owner and a queryset that knows how to scope itself.
"""

from django.conf import settings
from django.db import models
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
