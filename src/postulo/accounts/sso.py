"""Single sign-on, as configured: a few questions the rest of the code asks."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse


def _app() -> dict | None:
    providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    apps = (providers.get("openid_connect") or {}).get("APPS") or []
    return apps[0] if apps else None


def enabled() -> bool:
    """Whether an identity provider is configured, and so a button is shown."""
    app = _app()
    return bool(app and app.get("client_id") and (app.get("settings") or {}).get("server_url"))


def name() -> str:
    app = _app()
    return str(app.get("name") or "Single sign-on") if app else ""


def provider_id() -> str:
    app = _app()
    return str(app.get("provider_id") or settings.POSTULO_OIDC_PROVIDER_ID) if app else ""


def server_url() -> str:
    app = _app()
    return str((app.get("settings") or {}).get("server_url", "")) if app else ""


def auto_signup() -> bool:
    """Whether the identity provider may create accounts, not only sign existing ones in."""
    return bool(getattr(settings, "POSTULO_OIDC_AUTO_SIGNUP", False))


def link_by_email() -> bool:
    """Whether an address the provider has verified signs somebody into the account holding it.

    On, this is the convenience that makes single sign-on worth having: nobody has to
    connect anything by hand. It also means the instance is trusting the provider's word
    that the person proved they hold that address, which is a question about the
    provider rather than about Postulo — see *Hardening*.
    """
    return bool(getattr(settings, "POSTULO_OIDC_LINK_BY_EMAIL", True))


def login_url() -> str:
    return reverse("openid_connect_login", kwargs={"provider_id": provider_id()})


def callback_url(request: HttpRequest) -> str:
    """What the identity provider must be told to send people back to. Exactly this."""
    path = reverse("openid_connect_callback", kwargs={"provider_id": provider_id()})
    return request.build_absolute_uri(path)
