"""Whether this page should ask for browser notifications waiting for it (#209)."""

from __future__ import annotations

from django.http import HttpRequest


def browser_notices(request: HttpRequest) -> dict:
    """``browser_notices``: true when the person has a browser notifier switched on.

    A callable, so the query runs only for a template that asks -- the base layout does, once
    per page, and a fragment swapped in by htmx does not.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"browser_notices": False}

    def switched_on() -> bool:
        from postulo.plugins.models import Connection

        return (
            Connection.objects.for_user(user)
            .enabled()
            .of_kind("notifier")
            .filter(plugin="browser")
            .exists()
        )

    return {"browser_notices": switched_on}
