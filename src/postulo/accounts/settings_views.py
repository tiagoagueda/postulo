"""The Settings area: how Postulo behaves for one person, one section per page.

*Your details* is what documents print — the name, the contact block. Everything here is
about the account and the interface instead: appearance, language and time, the username,
and the doors to the pages allauth provides for addresses and the password. Each section is
its own view so that it can grow, and so that a plugin can add one beside them.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView, UpdateView

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
        context["passkeys_url"] = reverse("mfa_list_webauthn")
        context["add_passkey_url"] = reverse("mfa_add_webauthn")
        context["recovery_codes_url"] = reverse("mfa_view_recovery_codes")
        context["sso_enabled"] = sso.enabled()
        context["sso_name"] = sso.name()
        context["connections_url"] = reverse("socialaccount_connections")
        return context
