"""What wants deciding between owners, for whoever can decide it (#896).

Nothing here merges, moves or deletes anything: it finds, and the page offers what to do --
merge the two into one, keep both, give one to another owner, share what is missing.

* **The same thing twice, held by different owners**: a company of the same name or the same
  identifier, a contact of the same name or email address, a listing of the same advert. A
  person's own and their group's Acme is the usual one; a copy left after leaving a group,
  once they rejoin, is another.
* **A shared record pointing at one its audience cannot see**: what the sharing rules keep
  from happening, and what a superuser's change or an old database can still leave.
* **Records of a group with nobody in it**, for a superuser: somebody has to have them.

A person sees what they could resolve: pairs where they manage at least one side and can
see both. A superuser asking for everybody's sees every one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from django.contrib.auth.models import Group
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from . import sharing
from .duplicates import person_name
from .models import Company, ConflictDismissal, Contact, JobPosting, Share

KINDS = {"company": Company, "contact": Contact, "listing": JobPosting}


@dataclass
class Pair:
    kind: str
    first: object
    second: object
    reasons: list = field(default_factory=list)
    first_manageable: bool = False
    second_manageable: bool = False

    @property
    def first_owner(self) -> str:
        from postulo.core.sharing import principal_label

        return principal_label(self.first)

    @property
    def second_owner(self) -> str:
        from postulo.core.sharing import principal_label

        return principal_label(self.second)


@dataclass
class Dangling:
    grant: Share
    record: object
    missing: list


@dataclass
class Found:
    pairs: list = field(default_factory=list)
    dangling: list = field(default_factory=list)
    orphaned: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.pairs or self.dangling or self.orphaned)


def _principal_key(record) -> tuple:
    return (record.owner_id, record.owner_group_id)


def _dismissed(kind: str) -> set[tuple[int, int]]:
    return set(ConflictDismissal.objects.filter(kind=kind).values_list("first_id", "second_id"))


def _ordered(a, b) -> tuple:
    return (a, b) if a.pk < b.pk else (b, a)


def _scope(model, user, everyone: bool):
    if everyone and user.is_superuser:
        return model.objects.all()
    return model.objects.visible_to(user)


def _pairs(kind: str, records, keys, reason, user, everyone: bool) -> list[Pair]:
    """Records sharing a key and held by different owners, as pairs worth deciding."""
    model = KINDS[kind]
    manageable = set(
        model.objects.manageable_by(user, everyone=everyone).values_list("pk", flat=True)
    )
    dismissed = _dismissed(kind)
    by_key: dict = {}
    for record in records:
        for key in keys(record):
            if key:
                by_key.setdefault(key, []).append(record)
    found: dict[tuple[int, int], Pair] = {}
    for group in by_key.values():
        for a, b in combinations(group, 2):
            if a.pk == b.pk or _principal_key(a) == _principal_key(b):
                continue
            first, second = _ordered(a, b)
            if (first.pk, second.pk) in dismissed:
                continue
            if first.pk not in manageable and second.pk not in manageable:
                continue
            pair = found.setdefault(
                (first.pk, second.pk),
                Pair(
                    kind=kind,
                    first=first,
                    second=second,
                    first_manageable=first.pk in manageable,
                    second_manageable=second.pk in manageable,
                ),
            )
            if reason not in pair.reasons:
                pair.reasons.append(reason)
    return list(found.values())


def find(user, *, everyone: bool = False) -> Found:
    found = Found()
    companies = list(
        _scope(Company, user, everyone)
        .select_related("owner", "owner_group")
        .prefetch_related("identifiers")
    )
    found.pairs += _pairs(
        "company", companies, lambda c: [c.name_key], _("The same name"), user, everyone
    )
    found.pairs += _pairs(
        "company",
        companies,
        lambda c: [f"{i.scheme}:{i.value.casefold()}" for i in c.identifiers.all()],
        _("The same identifier"),
        user,
        everyone,
    )
    contacts = list(_scope(Contact, user, everyone).select_related("owner", "owner_group"))
    found.pairs += _pairs(
        "contact", contacts, lambda c: [person_name(c.name)], _("The same name"), user, everyone
    )
    found.pairs += _pairs(
        "contact",
        contacts,
        lambda c: [(c.email or "").strip().casefold()],
        _("The same email address"),
        user,
        everyone,
    )
    listings = list(
        _scope(JobPosting, user, everyone).select_related("owner", "owner_group", "company")
    )
    found.pairs += _pairs(
        "listing", listings, lambda p: [p.url_key], _("The same advert"), user, everyone
    )
    _merge_pairs(found)

    for grant in _grants_to_check(user, everyone):
        record = grant.record
        missing = [
            target
            for target in sharing.references(record)
            if not sharing.seen_by(target, grant.grantee)
        ]
        if missing:
            found.dangling.append(Dangling(grant=grant, record=record, missing=missing))

    if everyone and user.is_superuser:
        empty = Group.objects.annotate(members=Count("user")).filter(members=0)
        for model in (Company, Contact, JobPosting):
            found.orphaned += [
                (sharing.kind_of(record), record)
                for record in model.objects.filter(owner_group__in=empty)
            ]
    return found


def _merge_pairs(found: Found) -> None:
    """One pair per two records, with every reason the two were matched for."""
    merged: dict[tuple, Pair] = {}
    for pair in found.pairs:
        key = (pair.kind, pair.first.pk, pair.second.pk)
        if key in merged:
            merged[key].reasons += [r for r in pair.reasons if r not in merged[key].reasons]
        else:
            merged[key] = pair
    found.pairs = list(merged.values())


def _grants_to_check(user, everyone: bool):
    grants = Share.objects.select_related("company", "contact", "posting")
    if everyone and user.is_superuser:
        return grants
    keep = []
    for grant in grants:
        if sharing.may_manage(grant.record, user):
            keep.append(grant)
    return keep


def keep_apart(kind: str, first_id: int, second_id: int, user) -> None:
    a, b = sorted((first_id, second_id))
    ConflictDismissal.objects.get_or_create(
        kind=kind, first_id=a, second_id=b, defaults={"by": user}
    )
