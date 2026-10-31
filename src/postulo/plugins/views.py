"""Settings → Connections: where a person tells a plugin where its service is.

One page lists every connection across plugins; adding one starts from the list of
installed plugins that need a connection, and the form is drawn from the plugin's own
field specifications. *Test* runs the plugin's ``test()`` and keeps the outcome.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import DeleteView, ListView

from postulo.core.files import FILE_POLICY
from postulo.core.mixins import OwnedObjectMixin

from . import logos, registry
from .base import CONNECTED_KINDS, call_with_user
from .forms import ConnectionForm
from .models import Connection
from .policy import allows, connected_plugins, decide
from .registry import find_plugin
from .secrets import SecretsUnreadable

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "notifier": _("Notifications"),
    "outbox": _("Sending as yourself"),
    "store": _("Document stores"),
    "sync": _("Synchronisation"),
}


def _plugin_or_404(kind: str, name: str, person=None):
    if kind not in CONNECTED_KINDS:
        raise Http404("No such kind of plugin.")
    plugin = find_plugin(kind, name)
    if plugin is None:
        raise Http404("That plugin is not installed.")
    # One that needs no connection is not on the pick page, and its address is not a way round it:
    # the built-in local store, connected by hand, made a second file beside every document (#785).
    if not getattr(plugin, "needs_connection", True):
        raise Http404("That plugin needs no connection.")
    # An address for a plugin the person is not offered is not theirs to open (#362).
    if person is not None and not decide(name, person).offered:
        raise Http404("That plugin is not available.")
    return plugin


def _switched_off(request, connection):
    """A redirect with the reason when the policy has this connection's plugin off, else None."""
    if allows(connection):
        return None
    messages.error(request, _("That plugin is switched off for you."))
    return redirect("connections:list")


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


#: The most links a connection's card may offer (#889): a card is a line of facts, not a menu.
MAX_CARD_LINKS = 6


def _links(plugin, connection: Connection) -> list[tuple[str, str]]:
    """The links a plugin offers on its connection's card: ``(label, https address)``.

    For a service that has no API a person can use but does have a space of their own -- the
    page where a monthly declaration is made, its messages -- so the card can save the search.
    Optional, like `summary`, and as safe to ask: a plugin that fails to describe itself, or
    offers an address that is not an ``https`` page (`page_address` is the check a posting's
    address passes), loses that link and is logged, never drawn (#889). At most
    `MAX_CARD_LINKS`.
    """
    from urllib.parse import urlsplit

    from postulo.core.addresses import page_address

    offered = getattr(plugin, "links", None)
    if offered is None:
        return []
    try:
        raw = list(offered(connection.full_config) or [])
    except SecretsUnreadable:
        return []
    except Exception:
        logger.exception(
            "Plugin %r could not list the links of connection %s", plugin.name, connection.pk
        )
        return []
    links: list[tuple[str, str]] = []
    for item in raw:
        try:
            label, address = item
            label = str(label).strip()
            address = page_address(str(address))
            parts = urlsplit(address)
            if not label or parts.scheme != "https" or not parts.netloc:
                raise ValueError("not an https page with a label")
        except (TypeError, ValueError):
            logger.warning("Plugin %r offered a link that was dropped: %r", plugin.name, item)
            continue
        links.append((label[:80], address))
        if len(links) == MAX_CARD_LINKS:
            break
    return links


class PluginLogoView(LoginRequiredMixin, View):
    """A plugin's logo, served by this instance, as a PNG (#106).

    Not a static file, because plugins are installed at run time and the manifest
    `collectstatic` wrote at build time has never heard of them; not a URL at the vendor,
    because `img-src 'self'` is the point rather than the obstacle -- an `<img>` at
    somebody else's server would tell them which instances run their plugin and when.

    **Who may see one.** Anybody signed in can see the logo of a plugin that is available
    to them -- on or off, because *Settings -> Plugins* lists the ones that are off and
    draws their logo; a plugin that is not offered to this person at all (unavailable, or
    switched off for the whole instance) is theirs to see only if they administer the
    instance. That is not because a logo is sensitive -- it is
    that the set of plugins an instance has installed is a fact about the instance, and the
    page it belongs on is the administrator's.

    Never 500s on a bad file: a plugin whose logo will not decode is a 404 and a line in
    the log, because a broken image beside a name is a worse answer than no image.
    """

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        # Through the module rather than a bound name: which plugins exist is decided
        # at run time, and a name captured at import is a different question.
        plugin = registry.find_any(name)
        if plugin is None:
            raise Http404

        if not request.user.is_staff:
            from .policy import decide

            decision = decide(name, request.user)
            # `on` as well as `offered`: a transport is on for everyone but never listed.
            if not (decision.on or decision.offered):
                raise Http404

        found = logos.logo_for(plugin)
        if found is None:
            raise Http404
        mark, content_type = found

        response = HttpResponse(mark, content_type=content_type)
        # A visit straight to this address is a same-origin document, not the `<img>` a
        # page draws it as -- which matters now that a mark may be an SVG (#264). The
        # sanitiser is the first half of that defence and this is the second.
        response["Content-Security-Policy"] = FILE_POLICY
        response["X-Content-Type-Options"] = "nosniff"
        # The file cannot change without the plugin being reinstalled, and reinstalling
        # restarts the process. A day is long enough to be worth having and short enough
        # that an upgrade is visible the same afternoon.
        response["Cache-Control"] = "private, max-age=86400"
        return response


class ConnectionListView(OwnedObjectMixin, ListView):
    model = Connection
    template_name = "connections/list.html"
    context_object_name = "connections"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        installed = connected_plugins(self.request.user)
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
                    "links": _links(plugin, connection),
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
            for plugin in connected_plugins(request.user)
        ]
        return render(
            request, self.template_name, {"plugins": plugins, "section_title": _("Connections")}
        )


#: What a `form_attributes` name may be once `data-` is put in front of it.
_ATTRIBUTE_NAME = re.compile(r"[a-z][a-z0-9-]{0,40}")


def _plugin_attributes(plugin) -> str:
    """A plugin's ``form_attributes()``, as ``data-`` attributes for its connection card (#209).

    For the one thing a field cannot do: hand the page's script something it needs, such as
    a public key, without a template that knows the plugin by name. Names that are not plain
    lowercase words are dropped rather than escaped, and every value is escaped, so a plugin
    can put an attribute on the card and nothing else.
    """
    offered = getattr(plugin, "form_attributes", None)
    if offered is None:
        return ""
    try:
        attributes = offered() or {}
    except Exception:
        logger.exception("Plugin %r failed to give its form attributes", plugin.name)
        return ""
    return format_html_join(
        "",
        ' data-{}="{}"',
        (
            (name, str(value))
            for name, value in attributes.items()
            if _ATTRIBUTE_NAME.fullmatch(str(name))
        ),
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
        return Connection(owner=request.user), _plugin_or_404(kind, name, request.user)

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
                "plugin_attributes": _plugin_attributes(plugin),
                **self._consent_context(request, connection, plugin),
            },
        )

    @staticmethod
    def _consent_context(request, connection, plugin) -> dict:
        from . import consent as consent_flow

        wanted = consent_flow.wanted_by(plugin, connection.config if connection.pk else {})
        if wanted is None:
            return {}
        return {
            "consent": wanted,
            "consent_given": bool(connection.pk) and consent_flow.is_connected(connection),
            # Shown rather than described: an operator registering a redirect URI by hand
            # needs the exact string, and building one from a template is where this goes
            # wrong (#150).
            "consent_callback": consent_flow.callback_url(request),
        }

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
        if not decide(plugin.name, request.user).on:
            messages.error(request, _("That plugin is switched off for you."))
            return redirect("connections:list")
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


class ConnectionConsentView(OwnedObjectMixin, View):
    """Send somebody to the provider to agree (#150).

    A POST rather than a link, because it starts a round trip that ends in a stored
    credential, and a GET that does that is a GET somebody else's page can cause.
    """

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from . import consent as consent_flow

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        plugin = connection.plugin_instance
        if plugin is None:
            messages.error(request, _("That plugin is no longer installed."))
            return redirect("connections:list")
        if (refused := _switched_off(request, connection)) and not request.POST.get("forget"):
            return refused
        if request.POST.get("forget"):
            consent_flow.forget(connection)
            messages.success(
                request,
                _(
                    "Postulo has forgotten it. The provider still has the grant until you "
                    "withdraw it there as well."
                ),
            )
            return redirect("connections:list")
        try:
            where = consent_flow.start(connection, plugin, request)
        except consent_flow.ConsentFailed as error:
            messages.error(request, str(error))
            return redirect("connections:edit", pk=connection.pk)
        return redirect(where)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ConnectionConsentCallbackView(LoginRequiredMixin, View):
    """Where every provider sends people back, for every connection on the instance.

    Out of the request's transaction: the token exchange waits on the provider, and the
    tokens are written in a short one of their own (#357).

    One address rather than one per plugin, because it is the thing an operator registers by
    hand: registering it once is the difference between this being usable and being a chore
    repeated per provider (#150).
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        from postulo.core import mail_auth

        from . import consent as consent_flow

        state = request.GET.get("state", "")
        # The instance's own mail comes back here too, so an operator registers one address
        # rather than two. Its state is signed with a salt of its own, which is how the two
        # are told apart without either being readable as the other (#151).
        if mail_auth.is_mail_consent(state):
            return self._mail(request, state)

        refused = request.GET.get("error", "")
        if refused:
            # A person who pressed *Cancel* has not done anything wrong, and the provider's
            # own word for it is not a sentence anybody reads.
            messages.info(request, _("Nothing was connected. Nobody agreed to anything."))
            return redirect("connections:list")
        try:
            connection = consent_flow.finish(
                request, request.GET.get("code", ""), request.GET.get("state", "")
            )
        except consent_flow.ConsentFailed as error:
            messages.error(request, str(error))
            return redirect("connections:list")
        messages.success(request, _("Connected. Test it to make sure it works."))
        return redirect("connections:edit", pk=connection.pk)

    @staticmethod
    def _mail(request: HttpRequest, state: str) -> HttpResponse:
        from postulo.core import mail_auth

        if request.GET.get("error", ""):
            messages.info(request, _("Nothing was connected. Nobody agreed to anything."))
            return redirect("server:email")
        try:
            mail_auth.finish_consent(request, request.GET.get("code", ""), state)
        except mail_auth.TokenUnavailable as error:
            messages.error(request, str(error))
            return redirect("server:email")
        messages.success(
            request, _("Signed in to the mail provider. Test the connection to make sure.")
        )
        return redirect("server:email")


def _too_often(request: HttpRequest) -> str:
    """The sentence refusing one more call on somebody's server just now, or empty.

    A test is a real message, or a real request to somebody's server, at a press of a button.
    Bounded per account, as the text transport's sends are (#232). *Look on the server* signs
    in with the same password, so it spends the same allowance (#332): two buttons are not
    twice the sign-ins.
    """
    from postulo.core import throttle

    try:
        throttle.consume(
            "connection-test", request.user, throttle.rate_for("POSTULO_CONNECTION_TEST_RATE")
        )
    except throttle.TooOften as too_often:
        return ngettext(
            "That is a lot of tests. Try again in %(seconds)d second.",
            "That is a lot of tests. Try again in %(seconds)d seconds.",
            too_often.retry_after,
        ) % {"seconds": too_often.retry_after}
    return ""


def _on_the_server(request: HttpRequest, connection: Connection, plugin, method):
    """Call ``method(config)`` on the connected server: ``(True, answer)`` or ``(False, why)``.

    What *Test* and *Look on the server* share: for a connection that authenticates by
    consent, the first thing to prove is that the grant still stands -- a provider refusing
    to renew is not a failure a mail server can report, and "consent was withdrawn" is fixed
    by agreeing again rather than by editing a field (#150). Whatever the plugin raises comes
    back as a sentence in its own words, and is logged.
    """
    from . import consent as consent_flow

    try:
        if consent_flow.wanted_by(plugin, connection.config) is not None:
            consent_flow.access_token(connection)
        return True, call_with_user(method, connection.full_config, user=request.user)
    except (consent_flow.ConsentWithdrawn, consent_flow.ConsentFailed, SecretsUnreadable) as error:
        return False, str(error)
    except Exception as error:
        logger.exception("Calling %r for connection %s failed", plugin.name, connection.pk)
        return False, f"{type(error).__name__}: {error}"


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ConnectionTestView(OwnedObjectMixin, View):
    """*Test*: a login or a real request to the connected server, then what came of it.

    Out of the request's transaction (#357); `record_test` and a token refresh write in
    short ones of their own.
    """

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        connection = get_object_or_404(self.get_queryset(), pk=pk)
        plugin = connection.plugin_instance
        if plugin is None:
            messages.error(request, _("That plugin is no longer installed."))
            return redirect("connections:list")
        if refused := _switched_off(request, connection):
            return refused
        if too_often := _too_often(request):
            messages.error(request, too_often)
            return redirect("connections:list")
        ok, result = _on_the_server(request, connection, plugin, plugin.test)
        if ok:
            try:
                ok, message = bool(result.ok), str(result.message or "")
            except AttributeError as error:
                logger.exception("Testing connection %s gave no result", connection.pk)
                ok, message = False, f"{type(error).__name__}: {error}"
        else:
            message = result
        connection.record_test(ok, message)
        if ok:
            messages.success(request, message or _("It works."))
        else:
            messages.error(request, _("Test failed: %(message)s") % {"message": message})
        return redirect("connections:list")


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ConnectionDiscoverView(ConnectionFormView):
    """*Look on the server*: ask the plugin what there is to choose from, and draw the form again.

    A POST from its own button on a saved connection's form, never on page load or on *Save*,
    which would sign in on every page view or change what *Save* only stores (#332). The whole
    form is posted here, as #688's *Look up* posts its form: what was typed is drawn back as it
    was and nothing of it is saved. The plugin is asked with the configuration as last saved,
    which is what holds the password it signs in with.

    What it finds is kept in the connection's configuration (`discovery.KEY`), in a short
    transaction of its own once the server has answered, as *Test* records its outcome (#357);
    a failure is a sentence, and what was found last time stays as it was.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int | None = None, kind=None, name=None):
        from . import discovery

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        plugin = connection.plugin_instance
        if plugin is None:
            messages.error(request, _("That plugin is no longer installed."))
            return redirect("connections:list")
        if refused := _switched_off(request, connection):
            return refused
        specs = self._specs(plugin, connection)
        if not discovery.can_look(plugin, specs):
            messages.error(
                request, _("%(plugin)s has nothing to look for.") % {"plugin": plugin.label}
            )
            return redirect("connections:edit", pk=connection.pk)
        if too_often := _too_often(request):
            messages.error(request, too_often)
        else:
            ok, found = _on_the_server(request, connection, plugin, plugin.discover)
            if ok and not isinstance(found, Mapping):
                logger.error("Plugin %r discovered %r, not a mapping", plugin.name, found)
                ok, found = False, str(_("It did not say what it found."))
            if ok:
                with transaction.atomic():
                    fresh = self.get_queryset().select_for_update().get(pk=connection.pk)
                    fresh.config = discovery.record(fresh.config, specs, found)
                    fresh.save(update_fields=["config", "updated_at"])
                connection.refresh_from_db()
                count = sum(
                    len(discovery.tidy(spec, found.get(spec.name)))
                    for spec in discovery.specs_of(specs)
                )
                messages.success(
                    request,
                    ngettext(
                        "Found %(count)d option on the server. Choose what to use, then save.",
                        "Found %(count)d options on the server. Choose what to use, then save.",
                        count,
                    )
                    % {"count": count},
                )
            else:
                messages.error(
                    request,
                    _("%(plugin)s could not look on the server: %(message)s")
                    % {"plugin": plugin.label, "message": found},
                )
        try:
            form = ConnectionForm(plugin, instance=connection)
        except SecretsUnreadable:
            # Already said: the plugin could not be asked without them.
            connection.secrets_encrypted = ""
            form = ConnectionForm(plugin, instance=connection)
        form.initial.update(_as_typed(form, request.POST))
        return self._render(request, form, connection, plugin)

    @staticmethod
    def _specs(plugin, connection) -> list:
        """The fields the form draws, without reading a secret to find them."""
        from .forms import kind_specs

        return list(call_with_user(plugin.config_fields, user=connection.owner)) + kind_specs(
            plugin
        )


def _as_typed(form: ConnectionForm, posted) -> dict:
    """What was posted, as a form's initial values, so that looking loses nothing typed (#332).

    Only when the whole form came: a press from anywhere else posts no label, and an absent
    tick box would otherwise read as one unticked. A secret is never drawn back, here as
    anywhere: a password typed and not saved has to be typed again.
    """
    from django import forms as django_forms

    if "label" not in posted:
        return {}
    secret = {
        f"plugin_{spec.name}" for spec in form.specs if spec.secret or spec.type == "password"
    }
    initial = {}
    for name, field in form.fields.items():
        if name in secret:
            continue
        if isinstance(field, django_forms.BooleanField):
            initial[name] = name in posted
        elif name in posted:
            initial[name] = posted.get(name)
    return initial


class ConnectionBackfillView(OwnedObjectMixin, View):
    """*Send everything*: offer every existing document to a store connected later."""

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user).of_kind("store")

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.documents.archiving import backfill

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        if refused := _switched_off(request, connection):
            return refused
        count = backfill(connection)
        if count:
            messages.success(
                request,
                ngettext(
                    "%(count)d document is queued for %(label)s. The scheduler sends "
                    "it on its next pass; the document shows how that went.",
                    "%(count)d documents are queued for %(label)s. The scheduler sends "
                    "them on its next pass; each document shows how that went.",
                    count,
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


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ConnectionSyncNowView(OwnedObjectMixin, View):
    """*Sync now*: run a sync connection at once rather than on its interval.

    Not atomic, so that the connection's lease is committed the moment it is taken and a
    scheduler pass in another process sees it (#586).
    """

    def get_queryset(self):
        return Connection.objects.for_user(self.request.user).of_kind("sync")

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from .syncing import sync_connection

        connection = get_object_or_404(self.get_queryset(), pk=pk)
        if refused := _switched_off(request, connection):
            return refused
        from postulo.core import throttle

        # A run is a series of real requests to somebody's server, made at a press of a button,
        # and the scheduler's own fifteen minutes between runs did not apply to it (#786).
        try:
            throttle.consume(
                "connection-sync", request.user, throttle.rate_for("POSTULO_CONNECTION_SYNC_RATE")
            )
        except throttle.TooOften as too_often:
            messages.error(
                request,
                ngettext(
                    "That is a lot of syncs. Try again in %(seconds)d second.",
                    "That is a lot of syncs. Try again in %(seconds)d seconds.",
                    too_often.retry_after,
                )
                % {"seconds": too_often.retry_after},
            )
            return redirect("connections:list")
        report = sync_connection(connection)
        if report.already_running:
            messages.info(request, report.notes[0])
        elif report.error:
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
