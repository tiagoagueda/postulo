"""The Settings area: how Postulo behaves for one person, one section per page.

*Your details* is what documents print — the name, the contact block. Everything here is
about the account and the interface instead: appearance, language and time, the username,
and the doors to the pages allauth provides for addresses and the password. Each section is
its own view so that it can grow, and so that a plugin can add one beside them.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.formats import time_format
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import RedirectView, TemplateView, UpdateView

from postulo.core import site
from postulo.core.cells import STALE, moved_on, one_field_form

from . import addresses, passkeys, sso
from .forms import (
    AccessibilityForm,
    AccountForm,
    AppearanceForm,
    CaptureKeepingForm,
    LocaleForm,
)
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
    #: The name this section answers to in `SAVE_AS_YOU_GO`, when its form can save a field
    #: as it changes (#656).
    section = ""

    def get_object(self, queryset=None) -> Profile:
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def get_context_data(self, **kwargs):
        """What the page needs to save a field as it changes, when the person has asked.

        Nothing at all otherwise, so the form is the form it always was: the form says
        whether it *can*, and this person says whether it *does*.
        """
        context = super().get_context_data(**kwargs)
        fields = getattr(self.form_class, "save_as_you_go_fields", ())
        if self.section and fields and self.object.save_as_you_go:
            context["save_as_you_go"] = {
                "url": reverse("settings:save_field", args=[self.section, "__name__"]),
                "fields": " ".join(fields),
                "stamp": self.object.updated_at.isoformat(),
            }
        return context


class AppearanceView(ProfileSectionView):
    form_class = AppearanceForm
    section = "appearance"
    template_name = "settings/appearance.html"
    section_title = _("Appearance")

    def form_valid(self, form):
        """Saved, and when an arrow was pressed, where the item went (#299).

        A move reloads the page, so it says the new place in words -- a move that happens
        in silence is one somebody using a screen reader has to go looking for, which is
        the dashboard's rule -- and the address carries a fragment that puts focus back on
        the arrow that was pressed, so pressing it again moves the item again. At the end
        of the list that arrow is disabled and cannot hold focus, and the item's row takes
        it instead.
        """
        from postulo.core import navigation

        self.object = form.save()
        if form.ident_moved:
            return self._identifier_moved(form)
        if not form.moved:
            messages.success(self.request, self.saved_message)
            return redirect(self.get_success_url())
        key, direction = form.moved
        order = form.nav_order
        messages.success(
            self.request,
            _("%(name)s is now number %(place)s in the navigation.")
            % {"name": navigation.BY_KEY[key].label, "place": order.index(key) + 1},
        )
        target = (
            f"nav-{direction}-{key}"
            if navigation.can_move(order, key, direction)
            else f"nav-row-{key}"
        )
        return redirect(f"{self.request.path}#{target}")

    def _identifier_moved(self, form):
        """The same for the identifier list (#672): say where it went, and put focus back."""
        from postulo.core import identifier_order, identifiers

        key, direction = form.ident_moved
        order = form.ident_order
        messages.success(
            self.request,
            _("%(name)s is now number %(place)s in the identifiers.")
            % {"name": identifiers.registry()[key].label, "place": order.index(key) + 1},
        )
        target = (
            f"ident-{direction}-{key}"
            if identifier_order.can_move(order, key, direction)
            else f"ident-row-{key}"
        )
        return redirect(f"{self.request.path}#{target}")


class AccessibilityView(ProfileSectionView):
    form_class = AccessibilityForm
    section = "accessibility"
    template_name = "settings/accessibility.html"
    section_title = _("Accessibility")


#: The sections whose form can save one field at a time, by the name in the address (#656).
#: What each form lets through is its own `save_as_you_go_fields`; a field not on that list
#: cannot be named, whatever the address says.
SAVE_AS_YOU_GO = {"appearance": AppearanceForm, "accessibility": AccessibilityForm}


class SaveFieldView(LoginRequiredMixin, View):
    """One field of a Settings form, saved as it changes (#656).

    Built from the cell's machinery (`core.cells`): the page's own form narrowed to one
    field, so a field refuses what the page refuses, in the same words, and a save whose
    stamp has moved is refused with the cell's sentence. The record is the signed-in
    person's own profile and nothing in the address names it, so there is no other
    account's to reach; what the address does name -- a section and a field -- is looked up
    in the forms' own lists and is a 404 when it is not on one.

    **Never for a person who has not asked.** A person who has not turned it on has the form
    as it was, and a request that says otherwise is a 404 rather than a write nobody expects.
    The answer is JSON: whether it saved, a sentence for the live region, the new stamp, and
    the refusal drawn by the same feedback partial the page uses.
    """

    def post(self, request, section: str, name: str):
        form_class = SAVE_AS_YOU_GO.get(section)
        if form_class is None or name not in form_class.save_as_you_go_fields:
            raise Http404("That field is not saved as it changes.")
        profile, _created = Profile.objects.get_or_create(user=request.user)
        if not profile.save_as_you_go:
            raise Http404("Saving as you go is not switched on.")

        if moved_on(profile, request.POST.get("stamp", "")):
            # What it says now, put back in the field with the cell's sentence beside it.
            profile.refresh_from_db()
            current = getattr(profile, name)
            shown = {} if current is False or current is None else {name: current}
            form = one_field_form(Profile, form_class, name, profile, shown, alone=True)
            form.is_valid()
            form.add_error(name, STALE)
            return self._answer(request, form, name, saved=False, value=current, code=409)

        form = one_field_form(Profile, form_class, name, profile, request.POST, alone=True)
        if not form.is_valid():
            return self._answer(request, form, name, saved=False, code=422)
        form.save()
        return self._answer(request, form, name, saved=True)

    def _answer(self, request, form, name, *, saved, code=200, **more):
        field = form[name]
        if field.errors:
            field.field.widget.attrs["aria-invalid"] = "true"
        stamp = Profile.objects.get(pk=form.instance.pk).updated_at
        said = gettext("Saved at %(time)s") % {
            "time": time_format(timezone.localtime(), "TIME_FORMAT")
        }
        body = {
            "saved": saved,
            "status": said if saved else gettext("Not saved"),
            "stamp": stamp.isoformat(),
            "feedback": (
                render_to_string("settings/save_feedback.html", {"field": field}, request=request)
                if field.errors
                else ""
            ),
            **more,
        }
        return JsonResponse(body, status=code)


class LocaleView(ProfileSectionView):
    form_class = LocaleForm
    template_name = "settings/locale.html"
    section_title = _("Language and time")


class CaptureView(ProfileSectionView):
    """Whether this person's captures keep the page they were read from (#256).

    The person's half of a decision two people make. The instance's half is an
    administrator's, under *Server settings → Capture*, and it is final: what is switched
    off there is shown here locked, with the reason, and cannot be switched on from here.

    The page also says what is being kept and what it weighs, because a switch that fills
    a disk should be beside the number that says how full it is.
    """

    form_class = CaptureKeepingForm
    template_name = "settings/capture.html"
    section_title = _("Capture")

    def get_context_data(self, **kwargs):
        from postulo.jobs import pages, remembered, rendering

        context = super().get_context_data(**kwargs)
        context["keeping"] = pages.keeping_for(self.request.user)
        context["kept"] = pages.kept_by(self.request.user)
        # What this person's corrections taught, by site, with a way to forget it (#267).
        context["remembered_sites"] = remembered.sites(self.request.user)
        context["limits"] = {
            "source": site.capture_source_max_bytes(),
            "rendering": site.capture_rendering_max_bytes(),
            "account": site.capture_account_max_bytes(),
            "days": site.capture_page_keep_days(),
        }
        context["no_renderer"] = rendering.why_not()
        return context


class ForgetRememberedPlacesView(LoginRequiredMixin, View):
    """Forget the places this person's corrections taught about one site (#267).

    A POST from *Settings → Capture*, naming the site in the body. Only the requester's own
    rows are looked at, so a site somebody else has places for is, from here, a site with
    nothing to forget -- the same answer as one nobody has.
    """

    def post(self, request):
        from postulo.jobs import remembered

        host = (request.POST.get("host") or "").strip().lower()[:253]
        if remembered.forget(request.user, host):
            messages.success(
                request,
                _("Forgotten. Your next capture from %(host)s is read as if for the first time.")
                % {"host": host},
            )
        else:
            messages.info(request, _("Nothing was remembered for that site."))
        return redirect(reverse("settings:capture") + "#remembered")


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
        from allauth.mfa.models import Authenticator

        from postulo.notifications import transport

        context = super().get_context_data(**kwargs)
        context["addresses"] = self.request.user.emailaddress_set.order_by("-primary", "email")
        context["email_url"] = reverse("account_email")
        # Whether the page for managing several addresses is offered at all (#145). A
        # feature governs what Postulo offers; the addresses themselves are allauth's,
        # and switching this off deletes none of them.
        context["several_addresses"] = addresses.is_offered(self.request.user)
        context["password_url"] = reverse("account_change_password")
        context["mfa_enabled"] = get_mfa_adapter().is_mfa_enabled(self.request.user)
        # Which second factors there are, so the card words each case truthfully (#428).
        context["mfa_totp"] = get_mfa_adapter().is_mfa_enabled(
            self.request.user, [Authenticator.Type.TOTP]
        )
        # How long a browser the person marked as trusted is not asked again; None when the
        # instance does not offer that, so the page never states a number settings do not hold.
        context["mfa_trust_days"] = (
            settings.MFA_TRUST_COOKIE_AGE.days if settings.MFA_TRUST_ENABLED else None
        )
        # A fourth way in, on a page that already explains three (#153).
        context["email_sign_in"] = transport.email_sign_in()
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
