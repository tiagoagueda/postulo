"""Groups, and who may use, switch and configure each plugin and module (#886).

Two pages under *Server settings*, for administrators only:

* **Groups** (``server:groups``): Django's own ``auth.Group``, named and filled with accounts.
  A group has no permissions of its own here; it is a name for a set of people that an access
  rule can point at.
* **Access** (``server:plugin_access``): for one plugin or module, the rules that say who may use
  it, who may switch it for themselves and who may change its settings. *Everyone* is the
  instance's own row, so the old checkbox *Each person may change it* lives here as *Everyone may
  switch it*; accounts and groups are exceptions to that.

Every write is logged with who made it, as the plugin policy's are.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from postulo.accounts.reauth import reauthentication_required
from postulo.plugins import base, policy
from postulo.plugins.models import PluginAccess, PluginPolicy

from .mixins import StaffRequiredMixin

logger = logging.getLogger(__name__)

#: A group's name, the way the admin allows it.
MAX_GROUP_NAME = 150


class GroupsView(StaffRequiredMixin, View):
    """List, create, rename, fill and delete groups."""

    template_name = "server/groups.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        people = get_user_model().objects.order_by("username")
        groups = [
            {"group": group, "members": set(group.user_set.values_list("pk", flat=True))}
            for group in Group.objects.order_by("name")
        ]
        return render(
            request,
            self.template_name,
            {"section_title": _("People"), "groups": groups, "people": people},
        )

    @method_decorator(reauthentication_required)
    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action", "")
        if action == "create":
            name = request.POST.get("name", "").strip()
            if not name or len(name) > MAX_GROUP_NAME:
                messages.error(request, _("A group needs a name."))
            elif _make_group(name):
                logger.warning("Group %r made by %s", name, request.user.username)
                messages.success(request, _("Group made."))
            else:
                messages.error(request, _("There is a group of that name already."))
            return redirect("server:groups")
        pk = request.POST.get("group", "")
        if not pk.isdigit():
            raise Http404
        group = get_object_or_404(Group, pk=int(pk))
        from . import groups as lifecycle

        if action == "delete":
            # Refused while it owns records (#896); its members keep copies of what was
            # shared with it that their own data points at.
            try:
                lifecycle.delete_group(group)
            except lifecycle.GroupOwnsRecords as refusal:
                messages.error(request, str(refusal))
                return redirect("server:groups")
            logger.warning("Group %r deleted by %s", group.name, request.user.username)
            messages.success(request, _("Group deleted."))
        elif action == "save":
            name = request.POST.get("name", "").strip()
            clash = Group.objects.filter(name__iexact=name).exclude(pk=group.pk).exists()
            if not name or len(name) > MAX_GROUP_NAME or clash:
                messages.error(request, _("That is not a name this group can have."))
                return redirect("server:groups")
            chosen = [int(pk) for pk in request.POST.getlist("members") if pk.isdigit()]
            # Whoever is taken out keeps copies of what their own data points at, and the
            # last member of a group that owns records stays (#896): `core.groups` does both,
            # for this page and every other way the membership changes.
            People = get_user_model()
            try:
                with transaction.atomic():
                    # Locked, so two administrators saving at once take turns (#989).
                    group = Group.objects.select_for_update().get(pk=group.pk)
                    before = set(group.user_set.values_list("pk", flat=True))
                    # A deactivated account may stay a member -- nothing is deleted -- but is
                    # not added: it could sign in to nothing (#947).
                    wanted = People.objects.filter(pk__in=chosen).filter(
                        Q(is_active=True) | Q(pk__in=before)
                    )
                    group.name = name
                    group.save(update_fields=["name"])
                    group.user_set.set(wanted)
                    after = set(group.user_set.values_list("pk", flat=True))
            except lifecycle.LastMemberOwns as refusal:
                messages.error(request, str(refusal))
                return redirect("server:groups")
            names = dict(People.objects.filter(pk__in=before | after).values_list("pk", "username"))
            # Membership is what authorises a group's records, so the line says who came and
            # who went -- usernames, never addresses (#945).
            logger.warning(
                "Group %r set by %s: added %s, removed %s",
                group.name,
                request.user.username,
                sorted(names[pk] for pk in after - before) or "nobody",
                sorted(names[pk] for pk in before - after) or "nobody",
            )
            messages.success(request, _("Saved."))
        else:
            raise Http404
        return redirect("server:groups")


def _make_group(name: str) -> bool:
    """Make the group, or say it exists: the unique name decides, not a look beforehand (#948)."""
    if Group.objects.filter(name__iexact=name).exists():
        return False
    try:
        with transaction.atomic():  # a savepoint: the refusal must not poison the request's
            Group.objects.create(name=name)
    except IntegrityError:
        return False
    return True


def _governed(name: str):
    from postulo.core.server_views import _governed_plugins

    for plugin in _governed_plugins():
        if plugin.name == name:
            return plugin
    raise Http404


class PluginAccessView(StaffRequiredMixin, View):
    """The rules for one plugin or module."""

    template_name = "server/plugin_access.html"

    def _context(self, plugin) -> dict:
        manifest = base.manifest_of(plugin)
        instance = PluginPolicy.objects.filter(plugin=plugin.name, person=None).first()
        return {
            "section_title": _("Plugins"),
            "plugin": plugin,
            "manifest": manifest,
            "shipped": policy.shipped_inside(plugin.name),
            "everyone_switches": not (instance and instance.state == PluginPolicy.State.FORCED_ON),
            "rules": PluginAccess.objects.filter(plugin=plugin.name).select_related(
                "person", "group"
            ),
            "groups": Group.objects.order_by("name"),
            "people": get_user_model().objects.order_by("username"),
        }

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        return render(request, self.template_name, self._context(_governed(name)))

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        plugin = _governed(name)
        action = request.POST.get("action", "")
        if action == "everyone":
            self._everyone(request, plugin.name)
        elif action == "save":
            self._save(request, plugin.name)
        elif action == "add":
            self._add(request, plugin.name)
        else:
            raise Http404
        return redirect("server:plugin_access", name=plugin.name)

    def _everyone(self, request: HttpRequest, name: str) -> None:
        """*Everyone may switch it* is the instance row's available against forced on."""
        free = "everyone_switches" in request.POST
        row = PluginPolicy.objects.filter(plugin=name, person=None).first()
        if row is not None and row.state not in (
            PluginPolicy.State.AVAILABLE,
            PluginPolicy.State.FORCED_ON,
        ):
            messages.error(
                request, _("Switch it on first: while it is off for everybody this does not apply.")
            )
            return
        if free and row is not None:
            row.delete()
        elif not free and row is None:
            PluginPolicy.objects.create(
                plugin=name, state=PluginPolicy.State.FORCED_ON, decided_by=request.user
            )
        logger.warning(
            "Plugin %r: everyone may switch it = %s, by %s", name, free, request.user.username
        )
        messages.success(request, _("Saved."))

    def _save(self, request: HttpRequest, name: str) -> None:
        for rule in PluginAccess.objects.filter(plugin=name):
            if f"remove-{rule.pk}" in request.POST:
                rule.delete()
                continue
            rule.may_use = f"use-{rule.pk}" in request.POST
            rule.may_switch = f"switch-{rule.pk}" in request.POST
            rule.may_change_settings = f"settings-{rule.pk}" in request.POST
            rule.save()
        logger.warning("Access to plugin %r set by %s", name, request.user.username)
        messages.success(request, _("Saved."))

    def _add(self, request: HttpRequest, name: str) -> None:
        kind, _sep, pk = request.POST.get("subject", "").partition(":")
        if kind == "group" and pk.isdigit():
            subject = {"group": Group.objects.filter(pk=pk).first()}
        elif kind == "person" and pk.isdigit():
            subject = {"person": get_user_model().objects.filter(pk=pk, is_active=True).first()}
        else:
            subject = {}
        target = next(iter(subject.values()), None)
        if target is None:
            messages.error(request, _("Choose a group or an account."))
            return
        rule, made = PluginAccess.objects.get_or_create(plugin=name, **subject)
        rule.may_use = "use" in request.POST
        rule.may_switch = "switch" in request.POST
        rule.may_change_settings = "settings" in request.POST
        rule.save()
        logger.warning(
            "Access to plugin %r added for %s by %s", name, target, request.user.username
        )
        messages.success(request, _("Saved.") if made else _("That rule existed, and is changed."))


def access_url(name: str) -> str:
    return reverse("server:plugin_access", args=[name])
