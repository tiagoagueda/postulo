"""Profile and invitation views."""

from __future__ import annotations

from allauth.account.decorators import reauthentication_required
from django.contrib import messages
from django.contrib.auth import logout
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
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from postulo.core.context_processors import theme_switch
from postulo.core.files import serve_private_file
from postulo.core.mixins import StaffRequiredMixin
from postulo.core.redirects import safe_next

from . import avatars, deletion
from .adapter import INVITE_SESSION_KEY
from .forms import InviteForm, PersonIdentifierFormSet, ProfileForm
from .models import Invite, Profile, Theme


class ProfileView(LoginRequiredMixin, UpdateView):
    """Edit your own details. There is no view of anyone else's."""

    model = Profile
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")
    context_object_name = "profile"

    def get_object(self, queryset=None) -> Profile:
        profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return profile

    def get_identifiers(self):
        """The identifiers block: an inline formset saved with the profile."""
        kwargs = {"instance": self.object, "prefix": "identifiers"}
        # A form posted without the block -- an older client, a script -- means no change
        # to the identifiers, not an error about a missing management form.
        if self.request.method == "POST" and "identifiers-TOTAL_FORMS" in self.request.POST:
            kwargs["data"] = self.request.POST
        return PersonIdentifierFormSet(**kwargs)

    def get_context_data(self, **kwargs) -> dict:
        from . import identifiers as person_identifiers

        context = super().get_context_data(**kwargs)
        context.setdefault("identifiers", self.get_identifiers())
        context["identifier_schemes"] = person_identifiers.SCHEMES.values()
        return context

    def form_valid(self, form):
        from django.db import transaction

        formset = self.get_identifiers()
        if formset.is_bound and not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, identifiers=formset))
        with transaction.atomic():
            response = super().form_valid(form)
            if formset.is_bound:
                formset.instance = self.object
                formset.save()
        return self._after_saving(form, response)

    def _after_saving(self, form, response):
        messages.success(self.request, _("Your details have been saved."))
        outcome = getattr(form, "gravatar_outcome", "")
        if outcome == "none":
            messages.info(
                self.request,
                _("Gravatar has no picture for your address, so your initials stay."),
            )
        elif outcome == "error":
            messages.warning(
                self.request, _("Gravatar could not be reached just now. Try again later.")
            )
        return response


class AvatarView(LoginRequiredMixin, View):
    """Serve a person's picture: their own, or anyone's to an administrator.

    The file lives under private media like every other personal file and comes out only
    through here, so the content security policy can keep saying ``img-src 'self'``. A
    face is not a CV, so the cache may keep it for a day; the address changes with the
    profile, so a new picture is never hidden by an old cache entry.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        if pk != request.user.pk and not request.user.is_staff:
            raise Http404
        profile = get_object_or_404(Profile, user_id=pk)
        picture = profile.picture
        if not picture:
            raise Http404
        response = serve_private_file(request, picture, download_name="picture.png")
        response["Cache-Control"] = "private, max-age=86400"
        return response


class GravatarRefreshView(LoginRequiredMixin, View):
    """Ask Gravatar again, on demand, for one's own picture."""

    def post(self, request: HttpRequest) -> HttpResponse:
        profile, _created = Profile.objects.get_or_create(user=request.user)
        if not profile.use_gravatar:
            messages.error(request, _("Turn on “Use my Gravatar” first."))
        else:
            outcome = avatars.fetch_gravatar(profile)
            messages.success(
                request,
                {
                    "found": _("Your Gravatar has been fetched again."),
                    "none": _("Gravatar has no picture for your address, so your initials stay."),
                    "error": _("Gravatar could not be reached just now. Try again later."),
                }[outcome],
            )
        return redirect("accounts:profile")


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
        return HttpResponseRedirect(safe_next(request, reverse("core:home")))


class DeleteAccountView(LoginRequiredMixin, View):
    """Settings > Your data > Delete my account.

    Two proofs, because deletion is the one operation where a half-measure is worse than
    nothing: allauth's reauthentication (the password again, or the second factor), and
    the address typed out. No confirmation link by email — the person is signed in and has
    just proven it. The last administrator is told to appoint another first.
    """

    template_name = "accounts/delete_account.html"

    @method_decorator(reauthentication_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def render(self, request: HttpRequest, error: str = "") -> HttpResponse:
        from postulo.core.export import build_document

        return render(
            request,
            self.template_name,
            {
                "section_title": _("Your data"),
                "counts": build_document(request.user).get("counts", {}),
                "last_administrator": deletion.is_last_administrator(request.user),
                "error": error,
            },
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        return self.render(request)

    def post(self, request: HttpRequest) -> HttpResponse:
        typed = request.POST.get("confirm_email", "").strip().casefold()
        if typed != request.user.email.casefold():
            return self.render(request, _("That is not the address on this account."))
        user = request.user
        if deletion.is_last_administrator(user):
            return self.render(request)
        logout(request)
        try:
            deletion.delete_account(user)
        except deletion.LastAdministrator:  # pragma: no cover - checked just above
            return redirect("account_login")
        messages.success(request, _("Your account and everything in it have been deleted."))
        return redirect("account_login")
