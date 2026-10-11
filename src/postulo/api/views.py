"""Managing capture tokens."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.accounts.reauth import reauthentication_required
from postulo.core import throttle
from postulo.core.mixins import OwnedObjectMixin

from .forms import ApiTokenForm
from .models import ApiToken


class ApiTokenListView(OwnedObjectMixin, ListView):
    model = ApiToken
    template_name = "api/token_list.html"
    context_object_name = "tokens"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["form"] = ApiTokenForm(user=self.request.user)
        # Only the response that made a token has one to show; see the view below.
        context.setdefault("new_token", None)
        context["api_root"] = self.request.build_absolute_uri("/api/v1/")
        # How many are good for something right now, said where they are made (#764): a number
        # a person can compare with what they remember making.
        context["live_tokens"] = ApiToken.objects.for_user(self.request.user).active().count()
        return context


class ApiTokenCreateView(ApiTokenListView):
    """Make a token, and show its secret once, on the response that made it.

    The list is drawn here and not after a redirect, as an invitation's link is (#232).
    A redirect needs somewhere for the secret to wait, and the only place is the session,
    which is a row in the database: signed, not encrypted, and copied by every backup. If
    the page after the redirect was never drawn, a closed tab being enough, the secret
    stayed in that row for as long as the row did (#441). Postulo keeps a hash and
    nothing else, so that a copy of the database is not a set of working credentials.
    """

    http_method_names = ["post"]

    # A token is a way into the account that needs no password and no second factor, so
    # making one needs a recent sign-in, and only so many an hour (#764).
    @method_decorator(reauthentication_required)
    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            throttle.consume("api-token", request.user, throttle.rate_for("POSTULO_API_TOKEN_RATE"))
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return redirect("api:token_list")
        form = ApiTokenForm(request.POST, user=request.user)
        if not form.is_valid():
            messages.error(request, _("Give the token a name and at least one scope."))
            return redirect("api:token_list")
        _token, raw = ApiToken.issue(
            request.user,
            form.cleaned_data["name"],
            scopes=form.cleaned_data["scopes"],
            expires_at=form.expires_at(),
        )
        messages.success(request, _("Token created. Copy it now; it is not shown again."))
        self.object_list = self.get_queryset()
        response = self.render_to_response(self.get_context_data(new_token=raw))
        # The page holds a credential: no cache, shared or the browser's own, keeps it.
        response["Cache-Control"] = "private, max-age=0, no-store"
        return response


class ApiTokenRevokeView(OwnedObjectMixin, View):
    def get_queryset(self):
        return ApiToken.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        token = get_object_or_404(self.get_queryset(), pk=pk)
        token.revoke()
        messages.success(request, _("Token revoked."))
        return redirect("api:token_list")
