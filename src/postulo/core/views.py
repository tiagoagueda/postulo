"""Views that belong to no particular feature."""

from __future__ import annotations

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from . import widgets


def home(request: HttpRequest):
    """The landing page for visitors, and the dashboard for anyone signed in.

    The dashboard is built from widgets now (#44). What it shows and in what order is the
    person's own arrangement; what each widget computes is the widget's business, and the
    shared work behind several of them happens once.
    """
    if not request.user.is_authenticated:
        return render(request, "core/home.html")

    profile = getattr(request.user, "profile", None)
    return render(request, "core/dashboard.html", {"page": widgets.build_page(request, profile)})


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for container orchestration and uptime monitoring."""
    try:
        connection.ensure_connection()
    except Exception:  # pragma: no cover - only on a broken database
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    from .context_processors import installed_version

    return JsonResponse({"status": "ok", "database": "ok", "version": installed_version()})


def manifest(request: HttpRequest) -> JsonResponse:
    """The web app manifest, so a phone that installs Postulo names it correctly.

    Rendered rather than served as a static file because an operator can name their
    instance, and a home screen saying "Postulo" on an instance called something else is
    exactly the sort of small wrongness that makes software feel like somebody else's.
    """
    from django.templatetags.static import static

    from . import site

    name = site.instance_name()
    return JsonResponse(
        {
            "name": name,
            "short_name": name,
            "description": str(site.tagline() or _("Your job search, on your server.")),
            "start_url": reverse("core:home"),
            "display": "standalone",
            "background_color": "#f8fafc",
            "theme_color": "#f8fafc",
            "icons": [
                {"src": static("brand/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {
                    "src": static("brand/icon-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        },
        content_type="application/manifest+json",
    )
