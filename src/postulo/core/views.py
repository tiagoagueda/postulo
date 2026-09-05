"""Views that belong to no particular feature."""

from __future__ import annotations

from datetime import timedelta

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone

#: How long an application may sit unanswered before the dashboard suggests chasing it.
CHASE_AFTER_DAYS = 14

#: How far back "recent" reaches on the dashboard.
RECENT_DAYS = 30


def home(request: HttpRequest):
    """The landing page for visitors, and the dashboard for anyone signed in."""
    if not request.user.is_authenticated:
        return render(request, "core/home.html")

    # Imported here rather than at module scope: core is the foundation these apps are
    # built on, and importing them at the top would make the dependency circular.
    from postulo.applications.models import Application, ApplicationEvent, Reminder, Status
    from postulo.jobs.models import JobPosting

    applications = Application.objects.for_user(request.user)
    listings = JobPosting.objects.for_user(request.user)
    now = timezone.now()

    awaiting = applications.filter(
        status__in=[Status.APPLIED, Status.ACKNOWLEDGED],
        applied_at__lte=now - timedelta(days=CHASE_AFTER_DAYS),
    ).with_display_data()

    context = {
        "open_count": applications.open().count(),
        "total_count": applications.count(),
        "applied_recently": applications.filter(
            applied_at__gte=now - timedelta(days=RECENT_DAYS)
        ).count(),
        "interviewing_count": applications.filter(
            status__in=[Status.SCREENING, Status.INTERVIEWING, Status.ASSESSMENT]
        ).count(),
        "offer_count": applications.filter(status=Status.OFFER).count(),
        "listings_to_decide": listings.undecided().count(),
        "closing_soon_count": listings.closing_soon().count(),
        "awaiting_reply": awaiting[:10],
        "awaiting_reply_count": awaiting.count(),
        "chase_after_days": CHASE_AFTER_DAYS,
        "due_reminders": (
            Reminder.objects.for_user(request.user)
            .due()
            .select_related("application", "application__posting")[:10]
        ),
        "recent_events": (
            ApplicationEvent.objects.for_user(request.user).select_related(
                "application", "application__posting", "application__posting__company"
            )[:10]
        ),
    }
    return render(request, "core/dashboard.html", context)


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for container orchestration and uptime monitoring."""
    try:
        connection.ensure_connection()
    except Exception:  # pragma: no cover - only on a broken database
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
