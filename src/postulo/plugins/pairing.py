"""Pairing by hand: a sync connection's remote items and the person's own records (#327).

A sync plugin pairs what it can by itself, by UID. What it cannot guess -- that this card is
that company, that this event is the second interview -- the person says on the connection's
*Pairing* page, which the core draws and the plugin feeds. #228 still holds: a plugin adds no
page, no table and no script. It answers two optional calls, `remote_items` and
`fill_record` (`postulo.plugins.base.SyncPlugin`), and everything here is the core's.

**Live, not stored.** The plugin is asked when the page needs an item -- a search, *Show
unpaired*, the one item a pairing is about to be made with -- under its own allowance
(``POSTULO_CONNECTION_PAIRING_RATE``) and a deadline on every wait. Nothing it answers is
kept but the `SyncLink` a pairing writes: no copy of a personal address book lives here. What
is already paired is read from `SyncLink` alone, so the page opens, and a record's page draws
its line, without waiting on a server.

**The item's address comes from the plugin, never from a form.** A pairing names an item by
its key -- its collection and its UID -- and the plugin is asked for it again; the address
written on the link is the one it answered with. A forged one would be where the plugin sends
the password on its next run.

**Pairing and unpairing are the person's; what is filled is the plugin's.** A link carries no
actor. A record the plugin makes or fills is written through the surface's services, which
name the plugin as the actor there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .base import RECORD_KINDS, RemoteItem, call_with_user
from .models import SyncLink

logger = logging.getLogger(__name__)

#: The longest a question to the plugin may wait on the network, in seconds, all told.
DEADLINE = 15

#: The most items one answer is drawn with. A search is a search: a server answering with a
#: whole address book is cut here, and the person narrows the words.
MAX_ITEMS = 100

#: The most of the person's own records offered to pair one item with.
MAX_RECORDS = 50


@dataclass(frozen=True)
class Kind:
    """One of `RECORD_KINDS`, as the core knows it: its words, its model and its page."""

    key: str
    label: str
    model: str
    #: Whether a plugin may make or fill one of these. Nothing on the surface writes a person.
    fillable: bool = True

    @property
    def model_class(self):
        if self.model == "user":
            return get_user_model()
        from django.apps import apps

        return apps.get_model(self.model)


KINDS: dict[str, Kind] = {
    kind.key: kind
    for kind in (
        Kind("contact", _("Contact"), "jobs.Contact"),
        Kind("company", _("Company"), "jobs.Company"),
        Kind("department", _("Department"), "jobs.Department"),
        Kind("you", _("You"), "user", fillable=False),
        Kind("interview", _("Interview"), "applications.Interview"),
        Kind("reminder", _("Reminder"), "applications.Reminder"),
        Kind("application", _("Application"), "applications.Application"),
    )
}
assert tuple(KINDS) == RECORD_KINDS, "the core's words and the surface's vocabulary differ"


def can_pair(plugin) -> bool:
    """Whether this plugin gives its connections a *Pairing* page."""
    return plugin is not None and callable(getattr(plugin, "remote_items", None))


def can_fill(plugin) -> bool:
    """Whether this plugin can make or fill a record from one of its items."""
    return can_pair(plugin) and callable(getattr(plugin, "fill_record", None))


# --------------------------------------------------------------- the person's own records


def kind_of(record) -> str:
    """Which of `RECORD_KINDS` ``record`` is, or empty."""
    for kind in KINDS.values():
        if isinstance(record, kind.model_class):
            return kind.key
    return ""


def own(user, kind: str):
    """``user``'s own records of ``kind``: their own, not a group's nor a share's."""
    model = KINDS[kind].model_class
    if kind == "you":
        return model.objects.filter(pk=user.pk)
    return model.objects.for_user(user).filter(owner=user)


def record_of(user, kind: str, pk) -> object | None:
    """One of ``user``'s own records, by kind and id, or None."""
    if kind not in KINDS:
        return None
    try:
        return own(user, kind).filter(pk=int(pk)).first()
    except (TypeError, ValueError):
        return None


def is_own(user, record) -> bool:
    """Whether ``record`` is ``user``'s own: the only records a pairing line is drawn on."""
    if record is None or getattr(record, "pk", None) is None:
        return False
    if isinstance(record, get_user_model()):
        return record.pk == user.pk
    return getattr(record, "owner_id", None) == user.pk


def find_records(user, kind: str, search: str = ""):
    """``user``'s own records of ``kind`` that match ``search``, at most `MAX_RECORDS`."""
    records = own(user, kind)
    words = search.strip()
    if words and kind != "you":
        records = records.filter(_matching(kind, words))
    order = {
        "contact": ("name",),
        "company": ("name",),
        "department": ("company__name", "name"),
        "interview": ("-starts_at",),
        "reminder": ("-due_at",),
        "application": ("-pk",),
    }.get(kind, ("pk",))
    return list(records.order_by(*order)[:MAX_RECORDS])


def _matching(kind: str, words: str) -> Q:
    return {
        "contact": Q(name__icontains=words) | Q(company__name__icontains=words),
        "company": Q(name__icontains=words),
        "department": Q(name__icontains=words) | Q(company__name__icontains=words),
        "interview": Q(application__posting__title__icontains=words)
        | Q(application__posting__company__name__icontains=words),
        "reminder": Q(summary__icontains=words),
        "application": Q(posting__title__icontains=words)
        | Q(posting__company__name__icontains=words),
    }[kind]


def name_of(record) -> str:
    """What a record is called on the page."""
    if isinstance(record, get_user_model()):
        return _("You (%(name)s)") % {"name": record.display_name}
    return str(record)


def page_of(kind: str, record) -> str:
    """The record's own page: where its pairing line is drawn."""
    if kind == "you":
        return reverse("accounts:profile")
    if kind == "department":
        return reverse("jobs:company_detail", args=[record.company_id])
    if kind == "interview":
        return reverse("applications:interview_update", args=[record.pk])
    if kind == "reminder":
        return reverse("applications:reminder_update", args=[record.pk])
    return record.get_absolute_url()


# --------------------------------------------------------------------- asking the plugin


def tidy(items, *, kind: str = "") -> list[RemoteItem]:
    """What a plugin's ``remote_items`` gave, read rather than trusted.

    Anything that is not a `RemoteItem` with a UID and a label is left out and logged; a key
    found twice is kept once; a kind outside `RECORD_KINDS` is dropped, and an item left with
    none, or without ``kind`` when one was asked for, is left out; every part is cut to what
    a `SyncLink` can hold.
    """
    kept: list[RemoteItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items or ():
        if not isinstance(item, RemoteItem):
            logger.warning("A plugin offered %r to pair, not a RemoteItem", item)
            continue
        kinds = tuple(dict.fromkeys(k for k in item.kinds if k in KINDS))
        tidied = RemoteItem(
            collection=str(item.collection or "")[:500],
            uid=str(item.uid or "")[:200],
            label=str(item.label or "").strip()[:300],
            collection_label=str(item.collection_label or "").strip()[:300],
            href=str(item.href or "")[:500],
            detail=str(item.detail or "").strip()[:300],
            kinds=kinds,
            slot=str(item.slot or "")[:50],
        )
        if not tidied.uid or not tidied.label or not kinds or tidied.key in seen:
            continue
        if kind and kind not in kinds:
            continue
        seen.add(tidied.key)
        kept.append(tidied)
        if len(kept) >= MAX_ITEMS:
            break
    return kept


class Refusal(Exception):
    """Why a pairing, or a record made from an item, was not done, in words for the person."""


def _held_to_the_deadline(method):
    """``method``, a call of the configuration, with every wait on the network held to
    `DEADLINE` -- the waits the plugin makes through Postulo's client -- so a slow server
    makes a sentence rather than a page that never comes."""
    from .http import deadline_in, until

    def call(config, user=None):
        with until(deadline_in(DEADLINE)):
            return call_with_user(method, config, user=user)

    return call


def search(on_the_server, plugin, *, words: str = "", kind: str = "") -> list[RemoteItem]:
    """The plugin's items matching ``words`` and ``kind``; `Refusal` with its reason if not.

    ``on_the_server(method)`` calls ``method(config)`` on the connection and answers
    ``(True, answer)`` or ``(False, why)``: the views hand in the one *Test* uses.
    """
    ok, answer = on_the_server(
        _held_to_the_deadline(partial(plugin.remote_items, search=words, kind=kind, key=None))
    )
    if not ok:
        raise Refusal(answer)
    if not isinstance(answer, list | tuple):
        logger.error("Plugin %r listed %r, not a list of items", plugin.name, answer)
        raise Refusal(_("It did not say what it found."))
    return tidy(answer, kind=kind)


def find(on_the_server, plugin, collection: str, uid: str) -> RemoteItem:
    """The one item ``(collection, uid)``, asked of the plugin again; `Refusal` if it is gone.

    What a pairing is written from: the address on the link is the one in this answer, and
    never anything a form carried.
    """
    ok, answer = on_the_server(
        _held_to_the_deadline(
            partial(plugin.remote_items, search="", kind="", key=(collection, uid))
        )
    )
    if not ok:
        raise Refusal(answer)
    for item in tidy(answer if isinstance(answer, list | tuple) else ()):
        if item.key == (collection, uid):
            return item
    raise Refusal(_("That item is no longer on the other side."))


# ----------------------------------------------------------------------------- the links


def links_of(connection):
    """Every pairing on ``connection`` that stands, gone ones too, with their records."""
    return list(
        SyncLink.objects.for_user(connection.owner)
        .filter(connection=connection, unpaired_at__isnull=True)
        .select_related("content_type")
        .prefetch_related("target")
        .order_by("content_type_id", "object_id", "slot")
    )


def link_of(user, connection, pk):
    """One of ``user``'s links on ``connection``, by id, or None."""
    try:
        return (
            SyncLink.objects.for_user(user)
            .filter(connection=connection, pk=int(pk))
            .select_related("content_type")
            .first()
        )
    except (TypeError, ValueError):
        return None


def holders(connection, items) -> dict[tuple[str, str], SyncLink]:
    """The pairing that holds each of ``items``, by key, where one does."""
    uids = {item.uid for item in items}
    if not uids:
        return {}
    links = (
        SyncLink.objects.for_user(connection.owner)
        .filter(connection=connection, uid__in=uids, unpaired_at__isnull=True)
        .select_related("content_type")
        .prefetch_related("target")
    )
    return {(link.collection, link.uid): link for link in links}


def lines_for(user, record) -> list[SyncLink]:
    """The pairings of one record on ``user``'s own connections: its page's line.

    From `SyncLink` alone: a record's page never waits on a server.
    """
    if not is_own(user, record):
        return []
    return list(
        SyncLink.objects.for_user(user)
        .filter(
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.pk,
            unpaired_at__isnull=True,
            connection__kind="sync",
        )
        .select_related("connection")
        .order_by("connection__label", "slot")
    )


def pairable_connections(user) -> list:
    """``user``'s sync connections whose plugin gives them a *Pairing* page."""
    from .models import Connection

    return [
        connection
        for connection in Connection.objects.for_user(user).of_kind("sync").order_by("label")
        if can_pair(connection.plugin_instance)
    ]


def pair(connection, item: RemoteItem, record) -> SyncLink:
    """Pair ``record`` with ``item``, as the plugin last described it.

    The link is written with no version tag and nothing pushed, so the plugin's next run makes
    the first comparison of the two. A record already paired in the item's slot is paired
    with this item instead; an item another record holds raises `AlreadyPaired`.
    """
    return SyncLink.bind(
        connection,
        record,
        slot=item.slot,
        collection=item.collection,
        uid=item.uid,
        remote_href=item.href,
        etag="",
        local_hash="",
        last_synced_at=None,
    )


def unpair(link: SyncLink) -> SyncLink:
    """Undo one pairing, keeping both sides and the row, as `SyncLink.unbind` does (#326)."""
    if link.unpaired_at is None:
        link.unpaired_at = timezone.now()
        link.save(update_fields=["unpaired_at", "updated_at"])
    return link


def renew(link: SyncLink) -> None:
    """Let the plugin make a new twin for a record whose twin is gone: forget the pairing.

    No row is left, so a plugin that pairs by UID finds the record with no twin on its next
    run and makes it one, as it does a record made since its last run.
    """
    link.delete()


def fill(on_the_server, plugin, connection, item: RemoteItem, kind: str, into=None):
    """Have the plugin make a ``kind`` from ``item``, or fill ``into``; returns the record.

    Called outside any transaction: the plugin may read the item from its server first, and
    writes through the surface's services as itself. What comes back must be a record of
    ``kind`` and the connection owner's own, or it is a `Refusal` and nothing is paired.
    """
    if kind not in item.kinds or not KINDS[kind].fillable:
        raise Refusal(_("That item cannot be made into one of those."))
    ok, answer = on_the_server(
        _held_to_the_deadline(
            partial(plugin.fill_record, connection, item=item, kind=kind, into=into)
        )
    )
    if not ok:
        raise Refusal(answer)
    if not isinstance(answer, KINDS[kind].model_class) or not is_own(connection.owner, answer):
        logger.error("Plugin %r filled %r, not one of the owner's %s", plugin.name, answer, kind)
        raise Refusal(_("It did not say what it made."))
    if into is not None and answer.pk != into.pk:
        logger.error("Plugin %r was asked to fill %r and made %r", plugin.name, into, answer)
        raise Refusal(_("It did not fill the record it was given."))
    return answer
