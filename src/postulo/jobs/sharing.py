"""Sharing a company, a contact or a listing (#896).

A grant gives one person, or every member of one group, a record read-only or read/write.
Three rules hold whatever the door -- the record's page, the API, a plugin:

* **Only who manages a record shares it**: its owner, or a member of the group that owns it
  (or a superuser). A read/write grant does not reach this far.
* **No share leaves a hidden reference behind.** A listing names its company, a contact
  theirs, a company its parent. Sharing a record whose references the recipient cannot see
  is offered as sharing those too, with the same scope unless said otherwise; if one of
  them is not the sharer's to share, the whole share is refused, and says which and why.
* **A revocation that would leave one** -- the recipient keeping a listing at a company they
  could no longer see -- is refused the same way.

The trail (`trail.record`) says who shared what, with whom, and who took it back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from .models import Company, Contact, JobPosting, Share, ShareScope

KINDS = {"company": Company, "contact": Contact, "listing": JobPosting}
FIELD = {Company: "company", Contact: "contact", JobPosting: "posting"}


class ShareRefused(Exception):
    """A share or a revocation that would leave somebody a reference they cannot follow."""


def kind_of(record) -> str:
    return {Company: "company", Contact: "contact", JobPosting: "listing"}[type(record)]


def references(record) -> list:
    """What ``record`` points at that a recipient must be able to see: a listing's company,
    a contact's company, a company's parent -- and theirs in turn."""
    found, seen, waiting = [], set(), [record]
    while waiting:
        current = waiting.pop()
        pointed = []
        if isinstance(current, JobPosting):
            pointed.append(current.company)
        elif isinstance(current, Contact):
            if current.company_id:
                pointed.append(current.company)
        elif isinstance(current, Company) and current.parent_id:
            pointed.append(current.parent)
        for target in pointed:
            key = (type(target), target.pk)
            if key not in seen:
                seen.add(key)
                found.append(target)
                waiting.append(target)
    return found


def grants_for(record):
    return Share.objects.filter(**{FIELD[type(record)]: record}).select_related(
        "grantee_user", "grantee_group", "granted_by"
    )


def seen_by(record, grantee) -> bool:
    """Whether ``grantee`` -- a person or a group -- can see ``record``.

    A group sees what it owns and what was shared with it: what one member happens to see
    on their own does not count, since every other member would not.
    """
    model = type(record)
    if isinstance(grantee, Group):
        if record.owner_group_id == grantee.pk:
            return True
        return Share.objects.filter(**{FIELD[model]: record}, grantee_group=grantee).exists()
    return model.objects.visible_to(grantee).filter(pk=record.pk).exists()


def may_manage(record, actor) -> bool:
    return type(record).objects.manageable_by(actor, everyone=True).filter(pk=record.pk).exists()


@dataclass
class Plan:
    """What sharing ``record`` with ``grantee`` takes."""

    record: object
    grantee: object
    scope: str
    also: list = field(default_factory=list)  # references the sharer may share too
    refused: list = field(default_factory=list)  # references they may not

    @property
    def possible(self) -> bool:
        return not self.refused


def plan_share(record, grantee, scope: str, actor) -> Plan:
    plan = Plan(record=record, grantee=grantee, scope=scope)
    for target in references(record):
        if seen_by(target, grantee):
            continue
        (plan.also if may_manage(target, actor) else plan.refused).append(target)
    return plan


def why_refused(plan: Plan) -> str:
    names = ", ".join(f"“{target}”" for target in plan.refused)
    return _(
        "%(record)s points at %(names)s, which %(grantee)s cannot see and which is not yours "
        "to share. Nothing was shared."
    ) % {"record": f"“{plan.record}”", "names": names, "grantee": grantee_label(plan.grantee)}


def grantee_label(grantee) -> str:
    if isinstance(grantee, Group):
        return _("the group %(name)s") % {"name": grantee.name}
    return grantee.get_username()


@transaction.atomic
def share(
    record, grantee, scope: str, actor, *, also_share: bool = False, addressed_as: str = ""
) -> Plan:
    """Share ``record`` with ``grantee``, and -- with ``also_share`` -- what it points at.

    Raises `ShareRefused` when a reference stands in the way: one the sharer may not share,
    or one they may and did not agree to. Sharing again with the same person changes the
    scope rather than adding a second grant. ``addressed_as`` is what the sharer typed to
    find the person, kept to show them in place of the username (#943).
    """
    from . import trail

    if not may_manage(record, actor):
        raise ShareRefused(_("Only its owner can share it."))
    if scope not in ShareScope.values:
        raise ShareRefused(_("That is not a kind of access."))
    if _is_the_owner(record, grantee):
        raise ShareRefused(_("It is theirs already."))
    plan = plan_share(record, grantee, scope, actor)
    if not plan.possible:
        raise ShareRefused(why_refused(plan))
    if plan.also and not also_share:
        raise ShareRefused(
            _("Share what it points at too, or nothing is shared: %(names)s")
            % {"names": ", ".join(f"“{target}”" for target in plan.also)}
        )
    for target in [*plan.also, record]:
        _grant(
            target,
            grantee,
            scope if target is record else ShareScope.READ,
            actor,
            addressed_as=addressed_as,
        )
        trail.record(target, actor, trail.SHARED, detail=_detail(grantee, scope))
    transaction.on_commit(lambda: _tell_shared(record, grantee, actor))
    return plan


def _is_the_owner(record, grantee) -> bool:
    if isinstance(grantee, Group):
        return record.owner_group_id == grantee.pk
    return record.owner_id == grantee.pk


def _grant(record, grantee, scope: str, actor, *, addressed_as: str = "") -> Share:
    lookup = {FIELD[type(record)]: record}
    lookup["grantee_group" if isinstance(grantee, Group) else "grantee_user"] = grantee
    # One query that cannot lose a race: two shares of the same record to the same grantee
    # at once (a double submit) end as one row, not as an IntegrityError (#948).
    found, created = Share.objects.get_or_create(
        **lookup,
        defaults={"scope": scope, "granted_by": actor, "addressed_as": addressed_as},
    )
    if created:
        return found
    # Widened when asked again; a referenced record never narrows what it already had.
    changed = []
    if found.scope != scope and (scope == ShareScope.WRITE or found.record == record):
        found.scope = scope
        changed.append("scope")
    if addressed_as and found.addressed_as != addressed_as:
        found.addressed_as = addressed_as
        changed.append("addressed_as")
    if changed:
        found.save(update_fields=changed)
    return found


def _detail(grantee, scope: str) -> dict:
    if isinstance(grantee, Group):
        return {"group": grantee.name, "scope": scope}
    return {"person": grantee.get_username(), "scope": scope}


@transaction.atomic
def revoke(grant: Share, actor) -> None:
    """Take a grant back, at once (#896).

    Refused while the grantee keeps something that points at the record -- their listing at
    a shared company -- unless they could still see it some other way. Whoever loses access
    to a record their own data points at keeps a copy of their own (`detach`).
    """
    from . import detach, trail

    record = grant.record
    if not may_manage(record, actor):
        raise ShareRefused(_("Only its owner can take a share back."))
    grantee = grant.grantee
    grant.delete()
    if not seen_by(record, grantee):
        kept = dependents_kept_after(record, grantee)
        if kept:
            raise ShareRefused(
                _(
                    "%(grantee)s keeps %(names)s, which points at it; take that back first, "
                    "or nothing changes."
                )
                % {
                    "grantee": grantee_label(grantee),
                    "names": ", ".join(f"“{other}”" for other in kept),
                }
            )
        detach.after_losing(record, people_of(grantee))
    trail.record(record, actor, trail.UNSHARED, detail=_detail(grantee, grant.scope))
    transaction.on_commit(lambda: _tell_revoked(record, grantee, actor))


def dependents_kept_after(record, grantee) -> list:
    """What ``grantee`` still sees that points at ``record``, which they no longer see."""
    if not isinstance(record, Company):
        return []
    pointing = [
        *JobPosting.objects.filter(company=record),
        *Contact.objects.filter(company=record),
        *Company.objects.filter(parent=record),
    ]
    return [other for other in pointing if seen_by(other, grantee)]


def people_of(grantee) -> list:
    if isinstance(grantee, Group):
        return list(grantee.user_set.all())
    return [grantee]


def check_link(record, target, actor) -> str:
    """A refusal, or nothing: may ``record`` point at ``target`` now? (#896)

    Everybody who can see ``record`` must be able to see what it points at. Said as the
    sentence a form shows beside the field; empty when the link is fine.
    """
    if target is None or record.pk is None:
        return ""
    missing = []
    for grant in grants_for(record):
        if not seen_by(target, grant.grantee):
            missing.append(grantee_label(grant.grantee))
    if record.owner_group_id is not None and not seen_by(target, record.owner_group):
        missing.append(grantee_label(record.owner_group))
    if not missing:
        return ""
    return _(
        "“%(target)s” is not shared with %(names)s, who can see this; share it with them first."
    ) % {"target": target, "names": ", ".join(missing)}


def shared_with(user):
    """The grants this person, or a group of theirs, holds."""
    from postulo.core.models import group_ids

    return Share.objects.filter(Q(grantee_user=user) | Q(grantee_group__in=group_ids(user)))


def grantee_from(actor, value: str):
    """``group:<pk>`` among the sharer's groups, or a person named by username or address.

    Only the sharer's own groups are offered, and a person is found by what the sharer
    types rather than chosen from a list: an instance's accounts are not a directory.
    """
    User = get_user_model()
    value = (value or "").strip()
    if value.startswith("group:"):
        try:
            pk = int(value.split(":", 1)[1])
        except ValueError:
            return None
        return actor.groups.filter(pk=pk).first()
    if not value:
        return None
    found = User.objects.filter(is_active=True).filter(
        Q(username__iexact=value) | Q(email__iexact=value)
    )
    person = found.first() if found.count() == 1 else None
    if person is not None and person.pk == actor.pk:
        return None
    return person


def _tell_shared(record, grantee, actor) -> None:
    from postulo.notifications.base import Notification
    from postulo.notifications.service import notify

    for person in people_of(grantee):
        if person.pk == actor.pk:
            continue
        notify(
            person,
            lambda person=person: Notification(
                event="record_shared",
                title=_("%(who)s shared “%(what)s” with you")
                % {"who": actor.get_username(), "what": str(record)},
                body="",
                url=record.get_absolute_url(),
                key=f"shared:{kind_of(record)}:{record.pk}:{person.pk}",
                data={"kind": kind_of(record), "id": record.pk},
            ),
        )


def _tell_revoked(record, grantee, actor) -> None:
    from postulo.notifications.base import Notification
    from postulo.notifications.service import notify

    for person in people_of(grantee):
        if person.pk == actor.pk:
            continue
        notify(
            person,
            lambda person=person: Notification(
                event="share_ended",
                title=_("“%(what)s” is no longer shared with you") % {"what": str(record)},
                body="",
                url="",
                key=f"unshared:{kind_of(record)}:{record.pk}:{person.pk}",
                data={"kind": kind_of(record), "id": record.pk},
            ),
        )


def panel(record, user) -> dict:
    """What a record's page says about who has it (#896): its owner, how this person came
    to see it, whether they may share it, and who changed it lately."""
    from postulo.core.models import group_ids
    from postulo.core.sharing import principal_label

    from . import trail

    manages = may_manage(record, user)
    grant = None
    if not manages:
        grant = (
            grants_for(record)
            .filter(Q(grantee_user=user) | Q(grantee_group__in=group_ids(user)))
            .order_by("-scope")
            .first()
        )
    return {
        "record": record,
        "kind": kind_of(record),
        "owner_label": principal_label(record),
        "owned_by_group": record.owner_group_id is not None,
        "mine": record.owner_id == user.pk,
        "manages": manages,
        "grant": grant,
        "grant_count": grants_for(record).count() if manages else 0,
        "changes": list(trail.of(record, limit=10)),
    }


def audience(record) -> list:
    """Every person who can see ``record`` apart from a superuser's reach: its owner or the
    members of the group that owns it, and everybody it is shared with (#896)."""
    from django.contrib.auth import get_user_model

    people = set()
    if record.owner_id is not None:
        people.add(record.owner_id)
    if record.owner_group_id is not None:
        people |= set(record.owner_group.user_set.values_list("pk", flat=True))
    for grant in grants_for(record):
        people |= {person.pk for person in people_of(grant.grantee)}
    return list(get_user_model().objects.filter(pk__in=people))


def tell_gone(person, what: str) -> None:
    """Tell ``person`` that something shared with them is gone with its owner's account."""
    from postulo.notifications.base import Notification
    from postulo.notifications.service import notify

    notify(
        person,
        lambda: Notification(
            event="share_ended",
            title=_("“%(what)s” is no longer shared with you") % {"what": what},
            body=_("Its owner's account was deleted. What your own records needed is kept."),
            key=f"gone:{person.pk}:{what}",
        ),
    )
