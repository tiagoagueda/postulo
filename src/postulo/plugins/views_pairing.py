"""A sync connection's *Pairing* page: its items and the person's records, paired by hand (#327).

The core draws every page here; the plugin answers `remote_items` and `fill_record`, and
`postulo.plugins.pairing` says why it is shaped the way it is. Only the owner reaches any of
it -- every address is looked up through their own connections and their own records, and
answers 404 otherwise -- and a connection whose plugin lists no items has no page at all.

The plugin is asked outside any transaction, as *Test* and *Look on the server* ask it
(#357), with the same consent check and handling (`_on_the_server`), under the page's own
allowance (``POSTULO_CONNECTION_PAIRING_RATE``).
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from postulo.core.mixins import OwnedObjectMixin
from postulo.core.redirects import safe_next

from . import pairing
from .models import AlreadyPaired, Connection
from .views import _on_the_server, _switched_off, _too_often


class _PairingMixin(OwnedObjectMixin):
    """The person's own sync connection, whose plugin lists its items, or 404."""

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user).of_kind("sync")

    def _connection(self, pk: int):
        connection = get_object_or_404(self.get_queryset(), pk=pk)
        plugin = connection.plugin_instance
        if not pairing.can_pair(plugin):
            raise Http404("That connection has nothing to pair.")
        return connection, plugin

    def _asking(self, request, connection, plugin):
        """``on_the_server`` for `pairing`: the call *Test* makes, on this request."""
        return lambda method: _on_the_server(request, connection, plugin, method)

    @staticmethod
    def _page(connection, **query) -> str:
        address = reverse("connections:pairing", args=[connection.pk])
        query = {key: value for key, value in query.items() if value}
        return f"{address}?{urlencode(query)}" if query else address


def _chosen(request, user):
    """The record the person came to pair, from ``?for=kind-id``: ``(kind, record)`` or None.

    Somebody else's record, or one that is not there, is a 404 like any other address.
    """
    value = request.GET.get("for", "") or request.POST.get("for", "")
    if not value:
        return None
    kind, _sep, pk = value.partition("-")
    record = pairing.record_of(user, kind, pk)
    if record is None:
        raise Http404("No such record.")
    return kind, record


def _kind_choices(kinds=None) -> list[tuple[str, str]]:
    return [
        (kind.key, kind.label)
        for kind in pairing.KINDS.values()
        if kinds is None or kind.key in kinds
    ]


def _link_rows(links) -> list[dict]:
    rows = []
    for link in links:
        record = link.target
        kind = pairing.kind_of(record) if record is not None else ""
        rows.append(
            {
                "link": link,
                "record": record,
                "key": kind,
                "kind": pairing.KINDS[kind].label if kind else "",
                "name": pairing.name_of(record) if record is not None else "",
                "url": pairing.page_of(kind, record) if kind else "",
            }
        )
    return rows


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class PairingView(_PairingMixin, View):
    """The page: what is paired, from `SyncLink`; what is not, from the plugin on a search.

    It opens on the pairings that stand, gone ones too, without asking anybody. The plugin
    is asked only when the person searches or presses *Show unpaired*, and what it answers
    is drawn in a live region, each item saying which record holds it if one does.
    """

    template_name = "connections/pairing.html"

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection, plugin = self._connection(pk)
        chosen = _chosen(request, request.user)
        words = request.GET.get("q", "").strip()[:200]
        kind = request.GET.get("kind", "")
        if kind not in pairing.KINDS:
            kind = chosen[0] if chosen else ""
        unpaired = bool(request.GET.get("unpaired"))
        asked = bool(words) or unpaired
        items, error = [], ""
        if asked:
            if refused := _switched_off(request, connection):
                return refused
            if too_often := _too_often(request, pairing=True):
                error = too_often
            else:
                try:
                    items = pairing.search(
                        self._asking(request, connection, plugin),
                        plugin,
                        words=words,
                        kind=kind,
                    )
                except pairing.Refusal as refusal:
                    error = str(refusal)
        holders = pairing.holders(connection, items)
        rows = []
        for item in items:
            holder = holders.get(item.key)
            if unpaired and not words and holder is not None:
                continue
            rows.append(
                {
                    "item": item,
                    "holder": _link_rows([holder])[0] if holder is not None else None,
                    "kinds": [pairing.KINDS[key].label for key in item.kinds],
                    "offers_chosen": bool(chosen) and chosen[0] in item.kinds,
                    "choose_url": self._item_page(connection, item, kind, chosen),
                }
            )
        return render(
            request,
            self.template_name,
            {
                "connection": connection,
                "plugin": plugin,
                "section_title": _("Connections"),
                "links": _link_rows(pairing.links_of(connection)),
                "kinds": _kind_choices(),
                "words": words,
                "kind": kind,
                "unpaired": unpaired,
                "asked": asked,
                "rows": rows,
                "error": error,
                "chosen": chosen
                and {
                    "kind": chosen[0],
                    "record": chosen[1],
                    "name": pairing.name_of(chosen[1]),
                    "value": f"{chosen[0]}-{chosen[1].pk}",
                    "url": pairing.page_of(*chosen),
                },
                "here": request.get_full_path(),
            },
        )

    @staticmethod
    def _item_page(connection, item, kind, chosen) -> str:
        query = {"collection": item.collection, "uid": item.uid}
        if chosen and chosen[0] in item.kinds:
            query["kind"] = chosen[0]
        elif kind:
            query["kind"] = kind
        address = reverse("connections:pairing_item", args=[connection.pk])
        return f"{address}?{urlencode(query)}"


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class PairingItemView(_PairingMixin, View):
    """One item, asked of the plugin again by its key, and the person's records to pair it with.

    One form: a record of the chosen kind, or *a new one*, and what to do -- pair, fill from
    the item and pair, or fill once without pairing. Searching the person's records here is
    a plain GET of this page.
    """

    template_name = "connections/pairing_item.html"

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection, plugin = self._connection(pk)
        if refused := _switched_off(request, connection):
            return refused
        collection = request.GET.get("collection", "")
        uid = request.GET.get("uid", "")
        back = self._page(connection)
        if not uid:
            return redirect(back)
        if too_often := _too_often(request, pairing=True):
            messages.error(request, too_often)
            return redirect(back)
        try:
            item = pairing.find(self._asking(request, connection, plugin), plugin, collection, uid)
        except pairing.Refusal as refusal:
            messages.error(request, str(refusal))
            return redirect(back)
        kind = request.GET.get("kind", "")
        if kind not in item.kinds:
            kind = item.kinds[0]
        words = request.GET.get("q", "").strip()[:200]
        holder = pairing.holders(connection, [item]).get(item.key)
        can_fill = pairing.can_fill(plugin) and pairing.KINDS[kind].fillable
        return render(
            request,
            self.template_name,
            {
                "connection": connection,
                "plugin": plugin,
                "section_title": _("Connections"),
                "item": item,
                "holder": _link_rows([holder])[0] if holder is not None else None,
                "kinds": _kind_choices(item.kinds),
                "kind": kind,
                "kind_label": pairing.KINDS[kind].label,
                "words": words,
                "records": [
                    {"record": record, "name": pairing.name_of(record)}
                    for record in pairing.find_records(request.user, kind, words)
                ],
                "can_fill": can_fill,
                "back": back,
            },
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class PairingApplyView(_PairingMixin, View):
    """Pair an item with a record, or have the plugin make or fill one from it, then pair.

    The item is named by its key alone. It is asked of the plugin again, and the link is
    written from that answer: an address in the form is never read, so a forged one cannot
    become where the plugin sends the password on its next run.
    """

    http_method_names: ClassVar[list[str]] = ["post"]

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection, plugin = self._connection(pk)
        if refused := _switched_off(request, connection):
            return refused
        back = safe_next(request, self._page(connection))
        action = request.POST.get("action", "")
        kind = request.POST.get("kind", "")
        if action not in ("pair", "fill", "import") or kind not in pairing.KINDS:
            messages.error(request, _("Choose what to do with the item."))
            return redirect(back)
        chosen = request.POST.get("record", "")
        record = None
        if chosen != "new":
            record = pairing.record_of(request.user, kind, chosen)
            if record is None:
                if chosen:
                    raise Http404("No such record.")
                messages.error(request, _("Choose a record to pair it with."))
                return redirect(back)
        elif action == "pair":
            messages.error(request, _("Choose a record to pair it with."))
            return redirect(back)
        if too_often := _too_often(request, pairing=True):
            messages.error(request, too_often)
            return redirect(back)
        asking = self._asking(request, connection, plugin)
        try:
            item = pairing.find(
                asking, plugin, request.POST.get("collection", ""), request.POST.get("uid", "")
            )
            if kind not in item.kinds:
                raise pairing.Refusal(_("That item cannot be paired with one of those."))
            if action != "pair":
                if not pairing.can_fill(plugin):
                    raise Http404("That plugin makes no records.")
                if action == "fill":
                    # Refused before the plugin writes anything: an item another record holds
                    # would leave a record made and not paired.
                    holder = pairing.holders(connection, [item]).get(item.key)
                    if holder is not None and (record is None or holder.target != record):
                        raise AlreadyPaired(holder)
                record = pairing.fill(asking, plugin, connection, item, kind, into=record)
            if action != "import":
                pairing.pair(connection, item, record)
        except pairing.Refusal as refusal:
            messages.error(
                request,
                _("%(plugin)s could not do that: %(message)s")
                % {"plugin": plugin.label, "message": refusal},
            )
            return redirect(back)
        except AlreadyPaired as taken:
            holder = taken.link.target
            messages.error(
                request,
                _("That item is already paired with %(record)s. Unpair it first.")
                % {"record": pairing.name_of(holder) if holder is not None else "?"},
            )
            return redirect(back)
        name = pairing.name_of(record)
        if action == "import":
            messages.success(
                request,
                _("Made %(record)s from %(item)s, without pairing them.")
                % {"record": name, "item": item.label},
            )
        else:
            messages.success(
                request,
                _("Paired %(item)s with %(record)s. The next sync compares the two.")
                % {"record": name, "item": item.label},
            )
        return redirect(back)


class _LinkView(_PairingMixin, View):
    """A POST about one of the person's pairings on their connection, by the link's id."""

    http_method_names: ClassVar[list[str]] = ["post"]

    def _link(self, request, pk):
        """``(connection, plugin, link)``, or ``(connection, plugin, None)`` when none was named.

        A link that is not one of this person's on this connection is a 404, as any address
        naming somebody else's record is.
        """
        connection, plugin = self._connection(pk)
        if not request.POST.get("link"):
            messages.error(request, _("Choose a pairing."))
            return connection, plugin, None
        link = pairing.link_of(request.user, connection, request.POST.get("link"))
        if link is None:
            raise Http404("No such pairing.")
        return connection, plugin, link


class PairingUnpairView(_LinkView):
    """*Unpair*: both sides are kept, and the plugin's next run leaves them alone (#326)."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection, _plugin, link = self._link(request, pk)
        if link is None:
            return redirect(self._page(connection))
        pairing.unpair(link)
        messages.success(
            request,
            _("Unpaired. Both sides are kept, and %(connection)s leaves them alone.")
            % {"connection": connection.label},
        )
        return redirect(safe_next(request, self._page(connection)))


class PairingRenewView(_LinkView):
    """*Make a new one*: for a record whose twin is gone, let the plugin make it another."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection, plugin, link = self._link(request, pk)
        if link is None:
            return redirect(self._page(connection))
        if not link.remote_gone:
            messages.error(request, _("That pairing still has its other side."))
        else:
            pairing.renew(link)
            messages.success(
                request,
                _("%(plugin)s makes a new one on its next run.") % {"plugin": plugin.label},
            )
        return redirect(safe_next(request, self._page(connection)))
