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

from postulo.core import site

from . import addresses, passkeys, sso
from .forms import AccessibilityForm, AccountForm, AppearanceForm, LocaleForm
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


class AccessibilityView(ProfileSectionView):
    form_class = AccessibilityForm
    template_name = "settings/accessibility.html"
    section_title = _("Accessibility")


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

    def showing_internal(self) -> bool:
        """Whether the check mark that shows Postulo's own plugins is on (#200).

        A query parameter rather than a preference: the mark is a moment's curiosity about
        what the instance runs, and a page that remembered it would be a page that shows
        a dozen locked rows for ever after one look.
        """
        source = self.request.POST if self.request.method == "POST" else self.request.GET
        return source.get("internal") == "1"

    def get_context_data(self, **kwargs):
        from postulo.plugins import policy

        context = super().get_context_data(**kwargs)
        context["showing_internal"] = self.showing_internal()
        context["rows"] = policy.overview(self.request.user, internal=self.showing_internal())
        return context

    def post(self, request, *args, **kwargs):
        from postulo.plugins import policy

        wanted = set(request.POST.getlist("on"))
        changed = 0
        for row in policy.overview(request.user, internal=True):
            # Only what is theirs. `set_choice` refuses the rest anyway, which matters:
            # a disabled checkbox submits nothing, so without that refusal a locked-on
            # plugin would read as "switch me off" on every save.
            if row["theirs"] and policy.set_choice(
                request.user, row["name"], on=row["name"] in wanted
            ):
                changed += 1
        messages.success(request, _("Saved.") if changed else _("Nothing was different."))
        target = reverse("settings:plugins")
        return redirect(f"{target}?internal=1" if self.showing_internal() else target)
