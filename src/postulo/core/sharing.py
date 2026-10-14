"""Who may see, change and manage a company, a contact or a listing (#896).

Those three records are owned by one person or one group, and can be shared with a person
or a group, read-only or read/write. Everything a person writes *about* them -- notes,
a listing's status, its history, applications -- stays theirs and never goes through here.

Three questions, three methods:

``visible_to(user)``
    What ``user`` may see: what they own, what their groups own, and what was shared with
    them or with one of their groups. ``write=True`` narrows the shares to read/write ones.
``manageable_by(user)``
    What ``user`` may delete, give to another owner, or share: what they own, and what one
    of their groups owns. A read/write share is not ownership.
``owned_by(principal)``
    Exactly one owner's records, for matching by name or identifier inside them.

**The superuser override is asked for, never implied.** ``everyone=True`` lets a superuser
reach any record, and only the views addressed by a record's id pass it -- the page, its
editing, its deletion, its owner and its shares -- with the conflicts page. A picker, a
search, the map, an export or the API never does, so a superuser's own lists stay theirs.
"""

from __future__ import annotations

from django.apps import apps
from django.db.models import Exists, OuterRef, Q

from .models import PrincipalQuerySet, group_ids


def _authenticated(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


class SharedQuerySet(PrincipalQuerySet):
    """The queryset of a record that can be shared: a company, a contact, a listing.

    ``share_field`` names the record's column on `jobs.Share`.
    """

    share_field: str = ""

    def _grants(self, user, *, write: bool):
        Share = apps.get_model("jobs", "Share")
        grants = Share.objects.filter(**{self.share_field: OuterRef("pk")}).filter(
            Q(grantee_user=user) | Q(grantee_group__in=group_ids(user))
        )
        if write:
            grants = grants.filter(scope=Share.Scope.WRITE)
        return grants

    def visible_to(self, user, *, write: bool = False, everyone: bool = False):
        """What ``user`` may see -- or, with ``write``, change the details of."""
        if not _authenticated(user):
            return self.none()
        if everyone and user.is_superuser:
            return self.all()
        return self.filter(
            Q(owner=user)
            | Q(owner_group__in=group_ids(user))
            | Exists(self._grants(user, write=write))
        )

    def manageable_by(self, user, *, everyone: bool = False):
        """What ``user`` may delete, move to another owner, or share."""
        if not _authenticated(user):
            return self.none()
        if everyone and user.is_superuser:
            return self.all()
        return self.filter(Q(owner=user) | Q(owner_group__in=group_ids(user)))

    def for_user(self, user):
        """What ``user`` may see. Kept for plugins written before #896; prefer ``visible_to``."""
        return self.visible_to(user)


def principal_label(record) -> str:
    """The owner of a record as a person reads it: a username, or a group's name."""
    if record.owner_id is not None:
        return record.owner.get_username()
    return record.owner_group.name if record.owner_group_id else ""
