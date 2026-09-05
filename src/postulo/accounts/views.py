"""Profile and invitation views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from postulo.core.context_processors import theme_switch
from postulo.core.mixins import StaffRequiredMixin

from .adapter import INVITE_SESSION_KEY
from .forms import InviteForm, ProfileForm
from .models import Invite, Profile, Theme


class ProfileView(LoginRequiredMixin, UpdateView):
    """Edit your own details. There is no view of anyone else's."""

    model = Profile
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None) -> Profile:
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        messages.success(self.request, _("Your details have been saved."))
        return super().form_valid(form)


class InviteListView(StaffRequiredMixin, ListView):
    """Invitations issued from this instance."""

    model = Invite
    template_name = "accounts/invite_list.html"
    context_object_name = "invites"
    paginate_by = 50


class InviteCreateView(StaffRequiredMixin, CreateView):
    model = Invite
    form_class = InviteForm
    template_name = "accounts/invite_form.html"
    success_url = reverse_lazy("accounts:invite_list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(
            self.request,
            _("Invitation created. Send the person the link shown below; it can be used once."),
        )
        return response


class InviteRevokeView(StaffRequiredMixin, View):
    """Delete an invitation that has not been accepted."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        invite = get_object_or_404(Invite, pk=pk)
        if invite.is_accepted:
            messages.error(
                request, _("That invitation has already been accepted and cannot be revoked.")
            )
        else:
            invite.delete()
            messages.success(request, _("Invitation revoked."))
        return redirect("accounts:invite_list")


class InviteAcceptView(View):
    """Follow an invitation link, then continue to the signup form.

    The token is held in the session rather than passed along in the URL, so it does not
    end up in a browser history, a bookmark, or a referrer header once signup begins.
    """

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        invite = Invite.objects.filter(token=token).first()
        if invite is None or not invite.is_valid():
            raise Http404("This invitation is not valid.")

        if request.user.is_authenticated:
            messages.info(request, _("You are already signed in."))
            return redirect("core:home")

        request.session[INVITE_SESSION_KEY] = invite.token
        if invite.email:
            messages.info(
                request,
                _("This invitation is for %(email)s. Please sign up with that address.")
                % {"email": invite.email},
            )
        return HttpResponseRedirect(reverse("account_signup"))


class ThemeView(LoginRequiredMixin, View):
    """Save the theme from the switch in the header.

    One field, three values, and a reply that fits the way it was asked: an htmx request
    gets the switch back, re-rendered for its new state; a plain form post goes back to
    the page it came from. Nothing else about the profile is touched. The select on
    *Your details* still exists for anyone who prefers the explicit version.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        choice = request.POST.get("theme", "")
        if choice not in Theme.values:
            return HttpResponseBadRequest("Not a theme.")
        profile, _created = Profile.objects.get_or_create(user=request.user)
        profile.theme = choice
        profile.save()
        if getattr(request, "htmx", False):
            return render(
                request,
                "core/partials/theme_switch.html",
                {"theme_switch": theme_switch(choice), "ui_theme": choice},
            )
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            target = reverse("core:home")
        return HttpResponseRedirect(target)
