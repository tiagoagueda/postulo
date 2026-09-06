"""Settings → Connections: where a person tells a plugin where its service is.

One page lists every connection across plugins; adding one starts from the list of
installed plugins that need a connection, and the form is drawn from the plugin's own
field specifications. *Test* runs the plugin's ``test()`` and keeps the outcome.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DeleteView, ListView

from postulo.core.mixins import OwnedObjectMixin

from .base import CONNECTED_KINDS
from .forms import ConnectionForm
from .models import Connection
from .registry import connected_plugins, find_plugin
from .secrets import SecretsUnreadable

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "notifier": _("Notifications"),
    "store": _("Document stores"),
    "sync": _("Synchronisation"),
}


def _plugin_or_404(kind: str, name: str):
    if kind not in CONNECTED_KINDS:
        raise Http404("No such kind of plugin.")
    plugin = find_plugin(kind, name)
    if plugin is None:
        raise Http404("That plugin is not installed.")
    return plugin


def _summary(plugin, connection: Connection) -> str:
    """The plugin's one-line description of a connection, or nothing if it has none.

    A plugin failing to describe itself must not take the list down: the connection is
    still there to be edited or removed.
    """
    summary = getattr(plugin, "summary", None)
    if summary is None:
        return ""
    try:
        return str(summary(connection.full_config) or "")
    except SecretsUnreadable:
        return ""
    except Exception:
        logger.exception("Plugin %r could not summarise connection %s", plugin.name, connection.pk)
        return ""


class ConnectionListView(OwnedObjectMixin, ListView):
    model = Connection
    template_name = "connections/list.html"
    context_object_name = "connections"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        installed = connected_plugins()
        context["plugins_available"] = installed
        context["kind_labels"] = KIND_LABELS
        rows = []
        for connection in context["connections"]:
            plugin = connection.plugin_instance
            rows.append(
                {
                    "connection": connection,
                    "plugin": plugin,
                    "kind_label": KIND_LABELS.get(connection.kind, connection.kind),
                    "summary": _summary(plugin, connection),
                    "is_store": connection.kind == "store",
                    "is_sync": connection.kind == "sync",
                }
            )
        context["rows"] = rows
        context["section_title"] = _("Connections")
        return context


class ConnectionPickView(OwnedObjectMixin, View):
    """Which plugin to connect to. Only installed plugins that need a connection appear."""

    template_name = "connections/pick.html"

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user)

    def get(self, request: HttpRequest) -> HttpResponse:
        plugins = [
            {"plugin": plugin, "kind_label": KIND_LABELS.get(plugin.kind, plugin.kind)}
            for plugin in connected_plugins()
        ]
        return render(
            request, self.template_name, {"plugins": plugins, "section_title": _("Connections")}
        )


class ConnectionFormView(OwnedObjectMixin, View):
    """Create or edit one connection, through the form the plugin describes."""

    template_name = "connections/form.html"

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user)

    def _load(self, request: HttpRequest, pk: int | None, kind: str | None, name: str | None):
        if pk is not None:
            connection = get_object_or_404(self.get_queryset(), pk=pk)
            plugin = connection.plugin_instance
            if plugin is None:
                raise Http404("That plugin is no longer installed.")
            return connection, plugin
        return Connection(owner=request.user), _plugin_or_404(kind, name)

    def _render(self, request, form, connection, plugin) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "connection": connection if connection.pk else None,
                "plugin": plugin,
                "kind_label": KIND_LABELS.get(plugin.kind, plugin.kind),
                "section_title": _("Connections"),
            },
        )

    def get(self, request: HttpRequest, pk: int | None = None, kind=None, name=None):
        connection, plugin = self._load(request, pk, kind, name)
        try:
            form = ConnectionForm(plugin, instance=connection)
        except SecretsUnreadable as error:
            messages.error(request, str(error))
            connection.secrets_encrypted = ""
            form = ConnectionForm(plugin, instance=connection)
        return self._render(request, form, connection, plugin)

    def post(self, request: HttpRequest, pk: int | None = None, kind=None, name=None):
        connection, plugin = self._load(request, pk, kind, name)
        try:
            form = ConnectionForm(plugin, request.POST, instance=connection)
        except SecretsUnreadable:
            connection.secrets_encrypted = ""
            form = ConnectionForm(plugin, request.POST, instance=connection)
        if not form.is_valid():
            return self._render(request, form, connection, plugin)
        saved = form.save(commit=False)
        saved.owner = request.user
        saved.save()
        messages.success(request, _("Connection saved. Test it to make sure it works."))
        return redirect("connections:list")


class ConnectionTestView(OwnedObjectMixin, View):
    def get_queryset(self):
        return Connection.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection = get_object_or_404(self.get_queryset(), pk=pk)
        plugin = connection.plugin_instance
        if plugin is None:
            messages.error(request, _("That plugin is no longer installed."))
            return redirect("connections:list")
        try:
            result = plugin.test(connection.full_config)
            ok, message = bool(result.ok), str(result.message or "")
        except SecretsUnreadable as error:
            ok, message = False, str(error)
        except Exception as error:
            logger.exception("Testing connection %s failed", connection.pk)
            ok, message = False, f"{type(error).__name__}: {error}"
        connection.record_test(ok, message)
        if ok:
            messages.success(request, message or _("It works."))
        else:
            messages.error(request, _("Test failed: %(message)s") % {"message": message})
        return redirect("connections:list")


class ConnectionBackfillView(OwnedObjectMixin, View):
    """*Send everything*: offer every existing document to a store connected later."""

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user).of_kind("store")

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.documents.archiving import backfill

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        count = backfill(connection)
        if count:
            messages.success(
                request,
                _(
                    "%(count)d documents are queued for %(label)s. The scheduler sends "
                    "them on its next pass; each document shows how that went."
                )
                % {"count": count, "label": connection.label},
            )
        else:
            messages.info(
                request,
                _(
                    "Nothing to queue: every document of the chosen kinds is already listed for "
                    "%(label)s."
                )
                % {"label": connection.label},
            )
        return redirect("connections:list")


class ConnectionSyncNowView(OwnedObjectMixin, View):
    """*Sync now*: run a sync connection at once rather than on its interval."""

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user).of_kind("sync")

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from .syncing import sync_connection

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        report = sync_connection(connection)
        if report.error:
            messages.error(request, _("Sync failed: %(error)s") % {"error": report.error})
        else:
            messages.success(request, _("Synced: %(summary)s.") % {"summary": report.summary()})
        return redirect("connections:list")


class ConnectionDeleteView(OwnedObjectMixin, DeleteView):
    model = Connection
    template_name = "connections/confirm_delete.html"
    success_url = reverse_lazy("connections:list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section_title"] = _("Connections")
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Connection removed, secrets included."))
        return super().form_valid(form)
