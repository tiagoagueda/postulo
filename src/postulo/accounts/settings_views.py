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

from . import passkeys, sso
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
        context["password_url"] = reverse("account_change_password")
        context["mfa_enabled"] = get_mfa_adapter().is_mfa_enabled(self.request.user)
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
        context["chosen"] = [widgets.get(key) for key in chosen]
        context["available"] = [
            (group, [w for w in items if w.key not in chosen]) for group, items in widgets.groups()
        ]
        context["is_default"] = not widgets.has_arranged(profile)
        return context

    def post(self, request, *args, **kwargs):
        profile = self._profile()
        keys = widgets.keys_for(profile)
        action = request.POST.get("action", "")
        key = request.POST.get("key", "")

        if action == "reset":
            # None, not []: back to "never arranged", which is what reset means.
            profile.dashboard_widgets = None
            profile.save(update_fields=["dashboard_widgets"])
            return redirect("settings:dashboard")
        elif key in widgets.REGISTRY:
            keys = self._rearrange(keys, action, key)
        else:
            messages.error(request, _("That is not a widget Postulo knows about."))
            return redirect("settings:dashboard")

        profile.dashboard_widgets = keys
        profile.save(update_fields=["dashboard_widgets"])
        return redirect("settings:dashboard")

    def _profile(self) -> Profile:
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
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
