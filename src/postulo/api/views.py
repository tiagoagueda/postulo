"""Managing capture tokens."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView

from postulo.core.mixins import OwnedObjectMixin

from .forms import CaptureTokenForm
from .models import CaptureToken

#: Where the newly created token waits for exactly one page render. A session key,
#: not a secret: the secret it briefly points at never touches the database.
NEW_TOKEN_SESSION_KEY = "postulo_new_capture_token"  # noqa: S105


class CaptureTokenListView(OwnedObjectMixin, ListView):
    model = CaptureToken
    template_name = "api/token_list.html"
    context_object_name = "tokens"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["form"] = CaptureTokenForm(user=self.request.user)
        # Shown once and then forgotten: Postulo stores only a hash and genuinely
        # cannot produce the token again.
        context["new_token"] = self.request.session.pop(NEW_TOKEN_SESSION_KEY, None)
        context["api_root"] = self.request.build_absolute_uri("/api/v1/")
        return context


class CaptureTokenCreateView(OwnedObjectMixin, View):
    def get_queryset(self):
        return CaptureToken.objects.for_user(self.request.user)

    def post(self, request: HttpRequest) -> HttpResponse:
        form = CaptureTokenForm(request.POST, user=request.user)
        if form.is_valid():
            _token, raw = CaptureToken.issue(request.user, form.cleaned_data["name"])
            request.session[NEW_TOKEN_SESSION_KEY] = raw
            messages.success(request, _("Token created. Copy it now; it is not shown again."))
        else:
            messages.error(request, _("Give the token a name."))
        return redirect("api:token_list")


class CaptureTokenRevokeView(OwnedObjectMixin, View):
    def get_queryset(self):
        return CaptureToken.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        token = get_object_or_404(self.get_queryset(), pk=pk)
        token.revoke()
        messages.success(request, _("Token revoked."))
        return redirect("api:token_list")


token_list_url = reverse_lazy("api:token_list")
