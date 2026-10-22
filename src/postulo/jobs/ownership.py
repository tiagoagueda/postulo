"""Giving a company, a contact or a listing to another owner (#896).

From a person to one of their groups, from a group to one of its members, and -- for a
superuser -- to anybody. Always an explicit act, said before it is done:

* to a group: every member can then see it, change it, share it and delete it, and the
  person who gave it can no longer take it back alone;
* to a person: the group's other members lose it, and whatever of theirs pointed at it is
  copied to them first (`detach`), so nobody's applications go with it.

What the record points at goes with the question, as when sharing: a listing given to a
group needs a company the group can see, and one the giver may share is shared with the
group read-only; one they may not stops the move.

Its numbers, addresses, links, identifiers, names and departments follow it in the same
transaction, since a child is owned as its record is.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink

from . import sharing, trail
from .models import (
    Company,
    CompanyHiringSignal,
    CompanyIdentifier,
    CompanyName,
    Contact,
    Department,
    Share,
)


class MoveRefused(Exception):
    """The owner cannot be changed, and why."""


def label(principal) -> str:
    if isinstance(principal, Group):
        return _("the group %(name)s") % {"name": principal.name}
    return principal.get_username()


def may_move_to(record, principal, actor) -> bool:
    """Whether ``actor`` may give ``record`` to ``principal``."""
    if not sharing.may_manage(record, actor):
        return False
    if actor.is_superuser:
        return True
    if isinstance(principal, Group):
        return actor.groups.filter(pk=principal.pk).exists()
    # A group's record goes to one of its members who asks for it: themselves.
    return principal.pk == actor.pk


def losing(record, principal) -> list:
    """Who can see ``record`` now and would not once ``principal`` owns it."""
    before = _audience(record)
    if isinstance(principal, Group):
        after = set(principal.user_set.values_list("pk", flat=True))
    else:
        after = {principal.pk}
    from django.contrib.auth import get_user_model

    grantees = set()
    for grant in sharing.grants_for(record):
        grantees |= {person.pk for person in sharing.people_of(grant.grantee)}
    gone = before - after - grantees
    return list(get_user_model().objects.filter(pk__in=gone))


def _audience(record) -> set[int]:
    if record.owner_group_id is not None:
        return set(record.owner_group.user_set.values_list("pk", flat=True))
    return {record.owner_id}


@transaction.atomic
def move(record, principal, actor, *, also_share: bool = False) -> None:
    """Make ``principal`` -- a person or a group -- the one owner of ``record``."""
    from . import detach

    if not may_move_to(record, principal, actor):
        raise MoveRefused(_("You cannot give it to them."))
    if record.principal == principal:
        return
    if isinstance(principal, Group):
        plan = sharing.plan_share(record, principal, Share.Scope.READ, actor)
        if not plan.possible:
            raise MoveRefused(sharing.why_refused(plan))
        if plan.also and not also_share:
            raise MoveRefused(
                _("Share what it points at with the group too, or it stays where it is: %(names)s")
                % {"names": ", ".join(f"“{target}”" for target in plan.also)}
            )
        for target in plan.also:
            sharing.share(target, principal, Share.Scope.READ, actor, also_share=also_share)
    gone = losing(record, principal)
    detach.after_losing(record, gone)
    before = sharing.principal_label(record) if hasattr(sharing, "principal_label") else ""
    _set_owner(record, principal)
    # A grant to the new owner says nothing any more.
    lookup = {sharing.FIELD[type(record)]: record}
    lookup["grantee_group" if isinstance(principal, Group) else "grantee_user"] = principal
    Share.objects.filter(**lookup).delete()
    trail.record(
        record,
        actor,
        trail.OWNER_CHANGED,
        detail={"from": before, "to": label(principal)},
    )


def _set_owner(record, principal) -> None:
    owner_id, group_id = (
        (None, principal.pk) if isinstance(principal, Group) else (principal.pk, None)
    )
    model = type(record)
    try:
        with transaction.atomic():
            model.objects.filter(pk=record.pk).update(owner_id=owner_id, owner_group_id=group_id)
    except IntegrityError as exc:
        raise MoveRefused(
            _("%(owner)s has a record of that name already; merge the two on the conflicts page.")
            % {"owner": label(principal)}
        ) from exc
    record.owner_id, record.owner_group_id = owner_id, group_id
    if isinstance(record, Company):
        for child in (CompanyName, CompanyHiringSignal, CompanyIdentifier, Department):
            child.objects.filter(company=record).update(owner_id=owner_id, owner_group_id=group_id)
    held = ContentType.objects.get_for_model(type(record))
    for row_model in (PhoneNumber, PostalAddress, WebLink, MessagingHandle):
        row_model.objects.filter(content_type=held, object_id=record.pk).update(
            owner_id=owner_id, owner_group_id=group_id
        )


def kinds_reached() -> tuple:
    return (Company, Contact)
