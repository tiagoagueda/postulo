"""Views that belong to no particular feature."""

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render


def home(request: HttpRequest):
    """Placeholder landing page until the dashboard lands in M2."""
    return render(request, "core/home.html")


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for container orchestration and uptime monitoring."""
    try:
        connection.ensure_connection()
    except Exception:  # pragma: no cover - only on a broken database
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
