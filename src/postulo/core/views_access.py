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
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

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

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action", "")
        if action == "create":
            name = request.POST.get("name", "").strip()
            if not name or len(name) > MAX_GROUP_NAME:
                messages.error(request, _("A group needs a name."))
            elif Group.objects.filter(name__iexact=name).exists():
                messages.error(request, _("There is a group of that name already."))
            else:
                Group.objects.create(name=name)
                logger.warning("Group %r made by %s", name, request.user.username)
                messages.success(request, _("Group made."))
            return redirect("server:groups")
        group = get_object_or_404(Group, pk=request.POST.get("group", "0") or 0)
        from . import groups as lifecycle

        if not request.user.is_superuser and PluginAccess.objects.filter(group=group).exists():
            # A group an access rule names is part of that rule (#967): who is in it, what
            # it is called and whether it exists decide what the people in it may do.
            messages.error(
                request,
                _("Only the operator may change a group that a plugin's access rules name."),
            )
            return redirect("server:groups")

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
            try:
                with transaction.atomic():
                    group.name = name
                    group.save(update_fields=["name"])
                    group.user_set.set(get_user_model().objects.filter(pk__in=chosen))
            except lifecycle.LastMemberOwns as refusal:
                messages.error(request, str(refusal))
                return redirect("server:groups")
            logger.warning("Group %r set by %s", group.name, request.user.username)
            messages.success(request, _("Saved."))
        else:
            raise Http404
        return redirect("server:groups")


def _is_about_me(user, *, person_id=None, group_id=None) -> bool:
    """Whether a rule's subject is this administrator or a group they belong to (#967).

    The operator (a superuser) is never held to it. Any other administrator is not to
    write or lift the rules that decide what they themselves may do, or change who is in
    a group such a rule names: the restriction would be theirs to undo.
    """
    if user.is_superuser:
        return False
    if person_id is not None and person_id == user.pk:
        return True
    return group_id is not None and user.groups.filter(pk=group_id).exists()


def _refuse_own_rule(request: HttpRequest) -> None:
    messages.error(
        request,
        _("Only the operator may change a rule about your own account or one of your groups."),
    )


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
            if _is_about_me(request.user, person_id=rule.person_id, group_id=rule.group_id):
                _refuse_own_rule(request)
                continue
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
            subject = {"person": get_user_model().objects.filter(pk=pk).first()}
        else:
            subject = {}
        target = next(iter(subject.values()), None)
        if target is None:
            messages.error(request, _("Choose a group or an account."))
            return
        if _is_about_me(
            request.user,
            person_id=getattr(target, "pk", None) if "person" in subject else None,
            group_id=target.pk if "group" in subject else None,
        ):
            _refuse_own_rule(request)
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
