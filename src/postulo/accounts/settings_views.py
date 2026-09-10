"""The Settings area: how Postulo behaves for one person, one section per page.

*Your details* is what documents print — the name, the contact block. Everything here is
about the account and the interface instead: appearance, language and time, the username,
and the doors to the pages allauth provides for addresses and the password. Each section is
its own view so that it can grow, and so that a plugin can add one beside them.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView, TemplateView, UpdateView

from postulo.core import site, widgets

from . import addresses, passkeys, sso
from .forms import AccountForm, AppearanceForm, LocaleForm
from .models import Profile


class SettingsIndexView(LoginRequiredMixin, RedirectView):
    """``/settings/`` is the first section; there is nothing to show on its own."""

    pattern_name = "settings:appearance"


class SettingsSectionMixin(LoginRequiredMixin):
    """One section: a form for the signed-in person's own record, saved in place."""

    section_title: str = ""
    saved_message = _("Saved.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section_title"] = self.section_title
        return context

    def get_success_url(self) -> str:
        return self.request.path

    def form_valid(self, form):
        messages.success(self.request, self.saved_message)
        return super().form_valid(form)


class ProfileSectionView(SettingsSectionMixin, UpdateView):
    model = Profile

    def get_object(self, queryset=None) -> Profile:
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return profile


class AppearanceView(ProfileSectionView):
    form_class = AppearanceForm
    template_name = "settings/appearance.html"
    section_title = _("Appearance")


class LocaleView(ProfileSectionView):
    form_class = LocaleForm
    template_name = "settings/locale.html"
    section_title = _("Language and time")


class AccountView(SettingsSectionMixin, UpdateView):
    form_class = AccountForm
    template_name = "settings/account.html"
    section_title = _("Account")
    saved_message = _("Your username has been changed.")
    success_url = reverse_lazy("settings:account")

    def get_object(self, queryset=None):
        return self.request.user

    def get_context_data(self, **kwargs):
        from allauth.mfa.adapter import get_adapter as get_mfa_adapter

        context = super().get_context_data(**kwargs)
        context["addresses"] = self.request.user.emailaddress_set.order_by("-primary", "email")
        context["email_url"] = reverse("account_email")
        # Whether the page for managing several addresses is offered at all (#145). A
        # feature governs what Postulo offers; the addresses themselves are allauth's,
        # and switching this off deletes none of them.
        context["several_addresses"] = addresses.is_offered(self.request.user)
        context["password_url"] = reverse("account_change_password")
        context["mfa_enabled"] = get_mfa_adapter().is_mfa_enabled(self.request.user)
        # A fourth way in, on a page that already explains three (#153).
        context["email_sign_in"] = site.email_sign_in()
        context["mfa_url"] = reverse("mfa_index")
        context["passkeys"] = passkeys.summary(self.request.user, self.request)
        context["sso_is_second_factor"] = site.sso_is_second_factor()
        context["passkeys_url"] = reverse("mfa_list_webauthn")
        context["add_passkey_url"] = reverse("mfa_add_webauthn")
        context["recovery_codes_url"] = reverse("mfa_view_recovery_codes")
        context["sso_enabled"] = sso.enabled()
        context["sso_name"] = sso.name()
        context["connections_url"] = reverse("socialaccount_connections")
        return context


class PluginsView(SettingsSectionMixin, TemplateView):
    """What is running for this account, and who decided it.

    *Settings → Connections* answers "what have I set up". It says nothing about the
    parsers that read a posting off a page, which need no connection and so appear nowhere,
    and nothing about **why** a plugin is available at all.

    This page exists mainly for one of its rows: the one an administrator decided. #95 lets
    them make a plugin available, unavailable, always on or always off for a named account,
    and the whole reason that is acceptable is that it cannot be held quietly. A permission
    somebody holds over your account should be visible from your own settings without your
    having to ask anybody.

    A form and a Save button, so it works with no JavaScript at all — and a control that is
    not yours to change is shown disabled with the reason beside it, rather than hidden.
    Hiding it would be the quiet version of exactly what this page is against.
    """

    template_name = "settings/plugins.html"
    section_title = _("Plugins")

    def get_context_data(self, **kwargs):
        from postulo.plugins import policy

        context = super().get_context_data(**kwargs)
        context["rows"] = policy.overview(self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        from postulo.plugins import policy

        wanted = set(request.POST.getlist("on"))
        changed = 0
        for row in policy.overview(request.user):
            # Only what is theirs. `set_choice` refuses the rest anyway, which matters:
            # a disabled checkbox submits nothing, so without that refusal a locked-on
            # plugin would read as "switch me off" on every save.
            if row["theirs"] and policy.set_choice(
                request.user, row["name"], on=row["name"] in wanted
            ):
                changed += 1
        messages.success(request, _("Saved.") if changed else _("Nothing was different."))
        return redirect("settings:plugins")


class DashboardView(SettingsSectionMixin, TemplateView):
    """Arranging the dashboard: which widgets, in what order.

    A list with buttons rather than dragging. The board can drag since #35, but that took
    real work to make reachable without a mouse, and arranging is done once and then not
    again — a form that posts is keyboard-workable, screen-reader-workable and
    script-free for nothing.

    Every action is a POST to this address, which is what makes it survive a reload and
    behave under the back button.
    """

    template_name = "settings/dashboard.html"
    section_title = _("Dashboard")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self._profile()
        chosen = widgets.keys_for(profile)
        # Anything this account has never been offered, shown apart and first: a widget a
        # release or a plugin added waits here rather than walking onto the page (#123).
        fresh = [widget for widget in widgets.new_for(profile) if widget.key not in chosen]
        context["chosen"] = [widgets.get(key) for key in chosen]
        context["fresh"] = fresh
        context["available"] = [
            (group, [w for w in items if w.key not in chosen and w not in fresh])
            for group, items in widgets.groups()
        ]
        context["is_standard"] = widgets.is_standard(profile)
        return context

    def post(self, request, *args, **kwargs):
        profile = self._profile()
        keys = widgets.keys_for(profile)
        action = request.POST.get("action", "")
        key = request.POST.get("key", "")

        if action == "reset":
            # This account's own copy of the standard arrangement, not an absence of one:
            # reset means "give me the standard page", and it is still this account's (#123).
            widgets.seed(profile)
            profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])
            return redirect("settings:dashboard")
        if key not in widgets.REGISTRY:
            messages.error(request, _("That is not a widget Postulo knows about."))
            return redirect("settings:dashboard")
        # "dismiss" changes nothing about the page and everything about the offer: a new
        # widget somebody said no to stops being new without going on.
        if action != "dismiss":
            keys = self._rearrange(keys, action, key)

        profile.dashboard_widgets = keys
        # Whatever was just acted on is decided about now, however it was decided.
        profile.dashboard_known = sorted(widgets.known_to(profile) | {key})
        profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])
        return redirect("settings:dashboard")

    def _profile(self) -> Profile:
        profile, created = Profile.objects.get_or_create(user=self.request.user)
        if created:
            widgets.seed(profile)
            profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])
        return profile

    @staticmethod
    def _rearrange(keys: list[str], action: str, key: str) -> list[str]:
        keys = list(keys)
        if action == "add":
            if key not in keys:
                keys.append(key)
        elif action == "remove":
            keys = [k for k in keys if k != key]
        elif action in {"up", "down"} and key in keys:
            index = keys.index(key)
            target = index - 1 if action == "up" else index + 1
            if 0 <= target < len(keys):
                keys[index], keys[target] = keys[target], keys[index]
        return keys
