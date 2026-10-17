"""Groups that own records: joining, leaving, being removed, and deleting one (#896).

A group may own companies, contacts and listings. Whoever leaves it -- by themselves on
*Your groups*, or removed by an administrator on *Server settings → Groups*, or anywhere else
the membership changes -- loses its records straight away, and keeps a copy of each one their
own data points at (`jobs.detach`). The group keeps its records.

Two refusals keep a record from ever being left with nobody:

* the last member of a group that still owns records cannot leave it;
* a group that still owns records cannot be deleted.

Its records are first given to somebody, or deleted, on the conflicts page.

One door for every change: the membership table's own signal. A page that changes who is in
a group, the admin, a shell -- each goes through `m2m_changed`, so none of them can forget.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils.translation import gettext as _


class LastMemberOwns(Exception):
    """The last member of a group that owns records cannot leave it."""


class GroupOwnsRecords(Exception):
    """A group that owns records cannot be deleted."""


def owned_by(group) -> list:
    """Every company, contact and listing the group owns."""
    from postulo.jobs.models import Company, Contact, JobPosting

    return [
        *Company.objects.filter(owner_group=group),
        *Contact.objects.filter(owner_group=group),
        *JobPosting.objects.filter(owner_group=group),
    ]


def owns_anything(group) -> bool:
    from postulo.jobs.models import Company, Contact, JobPosting

    return any(
        model.objects.filter(owner_group=group).exists() for model in (Company, Contact, JobPosting)
    )


def shared_with(group) -> list:
    """Every company, contact and listing shared with the group."""
    from postulo.jobs.models import Share

    return [grant.record for grant in Share.objects.filter(grantee_group=group)]


def leave(group, person) -> None:
    """Take ``person`` out of ``group``: the one way the pages do it.

    In a savepoint of its own: Django removes a member inside a transaction with none, so a
    refusal from the membership signal would otherwise spoil the caller's.
    """
    from django.db import transaction

    with transaction.atomic():
        group.user_set.remove(person)


def delete_group(group) -> None:
    """Delete ``group``, unless it owns records.

    Its members lose what was shared with it, keeping copies of what their own data points
    at, before the grants go with the group.
    """
    from django.db import transaction

    from postulo.jobs import detach
    from postulo.jobs.models import Share

    if owns_anything(group):
        raise GroupOwnsRecords(
            _("%(name)s still owns records. Give them to somebody, or delete them, first.")
            % {"name": group.name}
        )
    with transaction.atomic():
        records = shared_with(group)
        members = list(group.user_set.all())
        Share.objects.filter(grantee_group=group).delete()
        detach.after_losing(records, members)
        group.delete()


def _pairs(instance, model, pk_set, reverse):
    """(group, person) pairs a membership signal is about, whichever side it was sent from."""
    User = get_user_model()
    if reverse:  # sent from the group: group.user_set.remove(...)
        people = (
            User.objects.filter(pk__in=pk_set) if pk_set is not None else instance.user_set.all()
        )
        return [(instance, person) for person in people]
    groups = Group.objects.filter(pk__in=pk_set) if pk_set is not None else instance.groups.all()
    return [(group, instance) for group in groups]


def _before_leaving(pairs) -> None:
    # The group's row is locked first, so two removals of the last two members take turns and
    # the second sees the first's result (#989). A no-op on SQLite, which has one writer.
    if connection.in_atomic_block:
        for pk in sorted({group.pk for group, _person in pairs}):
            Group.objects.select_for_update().filter(pk=pk).first()
    for group, person in pairs:
        stays = group.user_set.exclude(pk=person.pk)
        leaving = {p.pk for g, p in pairs if g.pk == group.pk}
        stays = stays.exclude(pk__in=leaving)
        if not stays.exists() and owns_anything(group):
            raise LastMemberOwns(
                _(
                    "%(person)s is the last member of %(name)s, which still owns records. Give "
                    "them to somebody, or delete them, first."
                )
                % {"person": person.get_username(), "name": group.name}
            )


def _after_leaving(pairs) -> None:
    from postulo.jobs import detach
    from postulo.jobs.models import Share

    for group, person in pairs:
        # Their own records' grants to the group end with their membership.
        for grant in Share.objects.filter(grantee_group=group):
            record = grant.record
            if record.owner_id == person.pk:
                grant.delete()
                detach.after_losing([record], list(group.user_set.all()))
        detach.after_losing([*owned_by(group), *shared_with(group)], [person])


_HELD = "_postulo_leaving"


@receiver(m2m_changed, sender=get_user_model().groups.through)
def membership_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action in ("pre_remove", "pre_clear"):
        pairs = _pairs(instance, model, pk_set, reverse)
        _before_leaving(pairs)
        setattr(instance, _HELD, pairs)
    elif action in ("post_remove", "post_clear"):
        pairs = getattr(instance, _HELD, None)
        if pairs is None:
            pairs = _pairs(instance, model, pk_set, reverse)
        _after_leaving(pairs)
        if hasattr(instance, _HELD):
            delattr(instance, _HELD)
